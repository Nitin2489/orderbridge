"""Captured order messages: creation, review, discard and submission.

Workflow
--------
1. create_capture(): keep the message exactly as received, run the
   interpreter (Phase A: the deterministic RulesInterpreter, not an AI
   model) and the deterministic matcher, and store every line as a pending
   proposal. A message with no order lines is refused and nothing is stored.
2. review_line(): a person decides each line. Unit and product are
   independent decisions: confirming a product never confirms its unit, and
   "peti" is never turned into cases unless a person confirms it.
   - confirm_unit: cases or pieces, and the quantity in that unit.
   - include: an active product (optionally with the unit decision);
     requires a confirmed unit. Pieces are converted with the product's
     units_per_case only when they divide exactly.
   - exclude: with a reason of 3-500 characters.
   - reset / clear_unit: undo a decision.
3. submit_capture(): for a READY capture, create a PENDING_REVIEW order
   through retailer_order_service.submit_capture_order() (the shared
   pricing, scheme, stock and retailer validation; nothing is deducted),
   link it, mark the capture SUBMITTED and save the aliases the order desk
   opted in to, all in the caller's single transaction.
4. discard_capture(): with a reason; terminal.

Status: NEEDS_REVIEW until every line is included or excluded, at least one
is included and every included line has a confirmed unit; then READY.
SUBMITTED and DISCARDED are terminal. FAILED is never set by Phase A.

Access: a retailer sees and changes only its own retailer's captures; the
order desk, all of them. A capture the actor may not see does not exist
for it. Only the order desk may opt in to saving an alias ("desk
corrections become aliases for that retailer").

Concurrency: every change locks the capture row (FOR UPDATE) and must name
the version it saw (stale -> StaleCaptureVersionError); the version goes up
by one per change. Creation is serialised per (user, client_request_id)
with a transaction-scoped advisory lock, and alias writes per (retailer,
phrase). Lock order: order_captures row, then the retailers row (inside
submit_capture_order), then alias locks and rows.

The service never commits; the caller owns the transaction, so an error at
any point leaves no order, link, alias or status change behind.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CaptureChannel,
    CaptureLineResolution,
    CaptureStatus,
    ConfirmedUnit,
    Order,
    OrderCapture,
    OrderCaptureLine,
    Product,
    ProductStatus,
    Retailer,
    RetailerProductAlias,
    UserRole,
)
from app.models.capture import (
    MAX_ALIAS_LENGTH,
    MAX_CAPTURE_QUANTITY,
    MAX_CONFIRMED_QUANTITY,
    MAX_REASON_LENGTH,
    MIN_REASON_LENGTH,
)
from app.services.capture_interpreter import (
    ExtractedLine,
    MessageInterpreter,
    RulesInterpreter,
)
from app.services.capture_matcher import (
    LineMatch,
    load_matching_context,
    match_extraction,
)
from app.services.capture_text import normalize_phrase
from app.services.order_service import LineIssueCode
from app.services.retailer_order_service import (
    OrderLineInput,
    OrderLinesInvalidError,
    request_fingerprint,
    submit_capture_order,
)

TERMINAL_STATUSES = frozenset(
    {CaptureStatus.SUBMITTED, CaptureStatus.DISCARDED, CaptureStatus.FAILED}
)
#: Columns that are 40 characters wide on capture lines.
_SHORT_TEXT = 40
#: PostgreSQL INTEGER.
_MAX_INTEGER = 2_147_483_647


# ---------------------------------------------------------------------------
# Errors and results
# ---------------------------------------------------------------------------


class CaptureError(Exception):
    """Base exception for capture operations."""


class CaptureNotFoundError(CaptureError):
    """Missing, or not visible to this actor."""


class CaptureLineNotFoundError(CaptureError):
    pass


class RetailerNotFoundError(CaptureError):
    pass


class RetailerInactiveError(CaptureError):
    pass


class InvalidCaptureRequestError(CaptureError):
    """The request itself is not acceptable (e.g. a missing retailer)."""


class NoOrderLinesError(CaptureError):
    """The message contains no order lines; nothing was stored."""


class CaptureRequestConflictError(CaptureError):
    """The client_request_id was already used for a different capture."""


class StaleCaptureVersionError(CaptureError):
    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__("The capture was changed by someone else; reload it.")


class CaptureStateError(CaptureError):
    """The capture's status does not allow the operation."""


class InvalidReviewError(CaptureError):
    """A review decision is incomplete or not valid."""


class AliasNotPermittedError(CaptureError):
    """Only the order desk may save aliases."""


class AliasConflictError(CaptureError):
    """An active alias for this phrase points to another product."""

    def __init__(self, line_number: int, phrase: str, product_id: UUID) -> None:
        self.line_number = line_number
        self.phrase = phrase
        self.product_id = product_id
        super().__init__(
            f"'{phrase}' is already saved for this retailer as another product. "
            "Choose to replace that alias, or do not save one."
        )


class CaptureNotReadyError(CaptureError):
    def __init__(self, unresolved_lines: tuple[int, ...]) -> None:
        self.unresolved_lines = unresolved_lines
        super().__init__("Every line must be included or excluded before submitting.")


@dataclass(frozen=True)
class CaptureLineIssue:
    line_number: int  # the capture line number
    product_id: UUID
    code: LineIssueCode
    message: str


class CaptureLinesInvalidError(CaptureError):
    """Included lines that cannot be ordered (from the shared validation)."""

    def __init__(self, issues: tuple[CaptureLineIssue, ...]) -> None:
        self.issues = issues
        super().__init__(f"{len(issues)} line(s) cannot be ordered.")


@dataclass(frozen=True)
class CaptureActor:
    """Who is acting. Built only from the authenticated session."""

    user_id: UUID
    role: UserRole
    retailer_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.role == UserRole.RETAILER and self.retailer_id is None:
            raise ValueError("A retailer actor needs its retailer.")
        if self.role == UserRole.ORDER_DESK and self.retailer_id is not None:
            raise ValueError("An order desk actor acts for no single retailer.")

    @property
    def is_desk(self) -> bool:
        return self.role == UserRole.ORDER_DESK

    def can_access(self, capture: OrderCapture) -> bool:
        return self.is_desk or capture.retailer_id == self.retailer_id


@dataclass(frozen=True)
class CaptureResult:
    capture: OrderCapture
    created: bool  # False when an identical earlier request was returned


@dataclass(frozen=True)
class CapturePage:
    captures: list[OrderCapture]
    total: int


@dataclass(frozen=True)
class SubmissionOutcome:
    capture: OrderCapture
    order: Order
    created: bool  # False when the capture had already been submitted


LineAction = Literal["confirm_unit", "clear_unit", "include", "exclude", "reset"]


@dataclass(frozen=True)
class LineDecision:
    """One review decision for one line. Only the fields of the action may
    be set; anything else is refused rather than ignored."""

    action: LineAction
    unit: ConfirmedUnit | None = None
    quantity: int | None = None
    product_id: UUID | None = None
    reason: str | None = None
    remember_alias: bool = False
    replace_existing_alias: bool = False


_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "confirm_unit": frozenset({"unit", "quantity"}),
    "clear_unit": frozenset(),
    "include": frozenset(
        {"unit", "quantity", "product_id", "remember_alias", "replace_existing_alias"}
    ),
    "exclude": frozenset({"reason"}),
    "reset": frozenset(),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _lock_key(namespace: str, *parts: object) -> int:
    """A signed 64-bit advisory lock key for these values."""
    value = ":".join([namespace, *(str(part) for part in parts)])
    digest = hashlib.sha256(value.encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _advisory_lock(session: Session, key: int) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _creation_fingerprint(retailer_id: UUID, message: str) -> str:
    return request_fingerprint(
        {"kind": "capture", "retailer_id": str(retailer_id), "message": message}
    )


def _new_line(line: ExtractedLine, match: LineMatch) -> OrderCaptureLine:
    quantity_text = line.quantity.text if line.quantity else None
    suggested = line.suggested_quantity
    if quantity_text is not None and len(quantity_text) > _SHORT_TEXT:
        # Too long to store as a suggestion; the text stays in the message.
        quantity_text, suggested = None, None
    if suggested is not None and suggested > _MAX_INTEGER:
        suggested = None
    proposal = match.proposal
    return OrderCaptureLine(
        line_number=line.line_number,
        start_offset=line.span.start,
        end_offset=line.span.end,
        original_text=line.span.text,
        product_start_offset=line.product.start if line.product else None,
        product_end_offset=line.product.end if line.product else None,
        product_text=line.product.text if line.product else None,
        quantity_text=quantity_text,
        suggested_quantity=suggested,
        unit_text=line.unit.text if line.unit else None,
        unit_hint=line.unit_hint,
        proposed_product_id=proposal.product_id if proposal else None,
        proposed_score=proposal.score if proposal else None,
        match_reasons=list(match.reasons),
        candidates=[
            candidate.model_dump(mode="json") for candidate in match.candidates
        ],
        unit_confirmed=False,
        resolution=CaptureLineResolution.PENDING,
        remember_alias=False,
        replace_existing_alias=False,
    )


def _with_lines(statement):
    return statement.options(
        selectinload(OrderCapture.lines).selectinload(OrderCaptureLine.selected_product)
    )


def _lock_capture(
    session: Session, actor: CaptureActor, capture_id: UUID
) -> OrderCapture:
    capture = session.scalar(
        _with_lines(select(OrderCapture).where(OrderCapture.id == capture_id))
        .with_for_update(of=OrderCapture)
        .execution_options(populate_existing=True)
    )
    if capture is None or not actor.can_access(capture):
        raise CaptureNotFoundError("Capture not found.")
    return capture


def _require_open(capture: OrderCapture, expected_version: int) -> None:
    if capture.status in TERMINAL_STATUSES:
        raise CaptureStateError(
            f"This capture is {capture.status}; it can no longer be changed."
        )
    if capture.version != expected_version:
        raise StaleCaptureVersionError(capture.version)


def _valid_reason(reason: str | None, what: str) -> str:
    trimmed = (reason or "").strip()
    if not MIN_REASON_LENGTH <= len(trimmed) <= MAX_REASON_LENGTH:
        raise InvalidReviewError(
            f"Enter a {what} of {MIN_REASON_LENGTH} to {MAX_REASON_LENGTH} characters."
        )
    return trimmed


def _active_product(session: Session, product_id: UUID | None) -> Product:
    if product_id is None:
        raise InvalidReviewError("Choose a product to include this line.")
    product = session.get(Product, product_id)
    if product is None or product.status != ProductStatus.ACTIVE:
        raise InvalidReviewError("That product does not exist or is not available.")
    return product


def cases_for(unit: ConfirmedUnit, quantity: int, product: Product) -> int:
    """Whole cases for a confirmed quantity, or InvalidReviewError.

    Pieces are converted only when they make whole cases of this product;
    nothing is rounded.
    """
    if unit == ConfirmedUnit.CASE:
        cases = quantity
    else:
        per_case = product.units_per_case
        if quantity % per_case:
            raise InvalidReviewError(
                f"{quantity} pieces is not a whole number of cases of "
                f"{product.name} ({per_case} per case). Enter the quantity in "
                "cases, or a multiple of the case size."
            )
        cases = quantity // per_case
    if not 1 <= cases <= MAX_CAPTURE_QUANTITY:
        raise InvalidReviewError(f"A line may order 1 to {MAX_CAPTURE_QUANTITY} cases.")
    return cases


def _included(lines: list[OrderCaptureLine]) -> list[OrderCaptureLine]:
    return [line for line in lines if line.resolution == CaptureLineResolution.INCLUDED]


def _status_for(lines: list[OrderCaptureLine]) -> CaptureStatus:
    included = _included(lines)
    decided = all(line.resolution != CaptureLineResolution.PENDING for line in lines)
    complete = all(
        line.unit_confirmed and line.quantity_cases is not None for line in included
    )
    if lines and decided and included and complete:
        return CaptureStatus.READY
    return CaptureStatus.NEEDS_REVIEW


def _find_line(capture: OrderCapture, line_number: int) -> OrderCaptureLine:
    for line in capture.lines:
        if line.line_number == line_number:
            return line
    raise CaptureLineNotFoundError("Line not found.")


def _active_alias(
    session: Session, retailer_id: UUID, normalized: str, *, lock: bool = False
) -> RetailerProductAlias | None:
    statement = select(RetailerProductAlias).where(
        RetailerProductAlias.retailer_id == retailer_id,
        RetailerProductAlias.normalized_alias == normalized,
        RetailerProductAlias.is_active.is_(True),
    )
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    return session.scalar(statement)


def _check_alias_choice(
    session: Session,
    actor: CaptureActor,
    capture: OrderCapture,
    line: OrderCaptureLine,
    product: Product,
    decision: LineDecision,
) -> None:
    if decision.replace_existing_alias and not decision.remember_alias:
        raise InvalidReviewError("Replacing an alias requires saving one.")
    if not decision.remember_alias:
        return
    if not actor.is_desk:
        raise AliasNotPermittedError("Only the order desk can save aliases.")
    if line.product_text is None:
        raise InvalidReviewError("This line has no product words to save as an alias.")
    if product.id == line.proposed_product_id:
        raise InvalidReviewError(
            "Only a corrected line can be saved as an alias: this is the "
            "product that was proposed."
        )
    normalized = normalize_phrase(line.product_text)
    if (
        not normalized
        or len(line.product_text) > MAX_ALIAS_LENGTH
        or len(normalized) > MAX_ALIAS_LENGTH
    ):
        raise InvalidReviewError(
            f"An alias may be at most {MAX_ALIAS_LENGTH} characters."
        )
    existing = _active_alias(session, capture.retailer_id, normalized)
    if (
        existing is not None
        and existing.product_id != product.id
        and not decision.replace_existing_alias
    ):
        raise AliasConflictError(
            line.line_number, line.product_text, existing.product_id
        )


def _validated_unit(
    unit: ConfirmedUnit | None, quantity: int | None
) -> tuple[ConfirmedUnit, int]:
    if unit is None or quantity is None:
        raise InvalidReviewError(
            "Confirm both the unit (cases or pieces) and the quantity."
        )
    if not 1 <= quantity <= MAX_CONFIRMED_QUANTITY:
        raise InvalidReviewError(
            f"Enter a quantity from 1 to {MAX_CONFIRMED_QUANTITY}."
        )
    return ConfirmedUnit(unit), quantity


def _apply_unit(
    line: OrderCaptureLine,
    unit: ConfirmedUnit,
    quantity: int,
    actor: CaptureActor,
    now: datetime,
) -> None:
    line.unit_confirmed = True
    line.confirmed_unit = unit
    line.confirmed_quantity = quantity
    line.unit_confirmed_by_user_id = actor.user_id
    line.unit_confirmed_at = now


def _clear_decision(line: OrderCaptureLine) -> None:
    line.resolution = CaptureLineResolution.PENDING
    line.selected_product = None
    line.selected_product_id = None
    line.quantity_cases = None
    line.exclusion_reason = None
    line.remember_alias = False
    line.replace_existing_alias = False
    line.reviewed_by_user_id = None
    line.reviewed_at = None


def _mark_reviewed(line: OrderCaptureLine, actor: CaptureActor, now: datetime) -> None:
    line.reviewed_by_user_id = actor.user_id
    line.reviewed_at = now


def _changed(capture: OrderCapture) -> None:
    capture.status = _status_for(capture.lines)
    capture.version += 1


# ---------------------------------------------------------------------------
# Create and read
# ---------------------------------------------------------------------------


def create_capture(
    session: Session,
    actor: CaptureActor,
    *,
    message: str,
    client_request_id: UUID,
    retailer_id: UUID | None = None,
    interpreter: MessageInterpreter | None = None,
    now: datetime | None = None,
) -> CaptureResult:
    """Store a message and its proposed lines, or return the identical
    capture this user already created with the same client_request_id.

    A retailer always captures for itself and may not name a retailer; the
    order desk must name an active retailer. Raises InvalidCaptureRequestError,
    RetailerNotFoundError, RetailerInactiveError, CaptureRequestConflictError,
    NoOrderLinesError, or the interpreter's InvalidMessageError /
    TooManyLinesError. Nothing is stored on any error.
    """
    if actor.is_desk:
        if retailer_id is None:
            raise InvalidCaptureRequestError(
                "Choose the retailer this message is from."
            )
    else:
        if retailer_id is not None:
            raise InvalidCaptureRequestError(
                "A retailer's captures are always for its own account."
            )
        retailer_id = actor.retailer_id
    assert retailer_id is not None

    fingerprint = _creation_fingerprint(retailer_id, message)
    _advisory_lock(
        session, _lock_key("capture-create", actor.user_id, client_request_id)
    )
    existing = session.scalar(
        _with_lines(
            select(OrderCapture).where(
                OrderCapture.created_by_user_id == actor.user_id,
                OrderCapture.client_request_id == client_request_id,
            )
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise CaptureRequestConflictError(
                "This client_request_id was already used for a different message."
            )
        return CaptureResult(capture=existing, created=False)

    retailer = session.get(Retailer, retailer_id)
    if retailer is None:
        raise RetailerNotFoundError("Retailer not found.")
    if not retailer.is_active:
        raise RetailerInactiveError("This retailer account is inactive.")

    extraction = (interpreter or RulesInterpreter()).extract(message)
    if not extraction.lines:
        raise NoOrderLinesError("No order lines were found in this message.")
    matches = match_extraction(extraction, load_matching_context(session, retailer.id))

    capture = OrderCapture(
        id=uuid4(),
        retailer_id=retailer.id,
        created_by_user_id=actor.user_id,
        channel=CaptureChannel.TEXT,
        original_message=message,
        interpreter=extraction.interpreter,
        extracted_data=extraction.model_dump(mode="json", exclude={"message"}),
        status=CaptureStatus.NEEDS_REVIEW,
        version=1,
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        created_at=now or _utcnow(),
        lines=[
            _new_line(line, match)
            for line, match in zip(extraction.lines, matches, strict=True)
        ],
    )
    session.add(capture)
    session.flush()
    return CaptureResult(capture=capture, created=True)


def get_capture(
    session: Session, actor: CaptureActor, capture_id: UUID
) -> OrderCapture:
    capture = session.scalar(
        _with_lines(select(OrderCapture).where(OrderCapture.id == capture_id))
    )
    if capture is None or not actor.can_access(capture):
        raise CaptureNotFoundError("Capture not found.")
    return capture


def list_captures(
    session: Session,
    actor: CaptureActor,
    *,
    status: CaptureStatus | None = None,
    retailer_id: UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> CapturePage:
    """Captures this actor may see, newest first (created_at, then id).

    A retailer always sees only its own; retailer_id filters the desk's view.
    """
    filters = []
    if not actor.is_desk:
        filters.append(OrderCapture.retailer_id == actor.retailer_id)
    elif retailer_id is not None:
        filters.append(OrderCapture.retailer_id == retailer_id)
    if status is not None:
        filters.append(OrderCapture.status == status)

    total = session.scalar(select(func.count(OrderCapture.id)).where(*filters))
    captures = list(
        session.scalars(
            _with_lines(select(OrderCapture).where(*filters))
            .order_by(OrderCapture.created_at.desc(), OrderCapture.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return CapturePage(captures=captures, total=total or 0)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def review_line(
    session: Session,
    actor: CaptureActor,
    capture_id: UUID,
    line_number: int,
    decision: LineDecision,
    *,
    expected_version: int,
    now: datetime | None = None,
) -> OrderCapture:
    """Apply one decision to one line; returns the capture (new version).

    Raises CaptureNotFoundError, CaptureLineNotFoundError, CaptureStateError,
    StaleCaptureVersionError, InvalidReviewError, AliasNotPermittedError or
    AliasConflictError. Nothing changes on any error.
    """
    allowed = _ALLOWED_FIELDS[decision.action]
    supplied = {
        name
        for name, value in (
            ("unit", decision.unit),
            ("quantity", decision.quantity),
            ("product_id", decision.product_id),
            ("reason", decision.reason),
            ("remember_alias", decision.remember_alias or None),
            ("replace_existing_alias", decision.replace_existing_alias or None),
        )
        if value is not None
    }
    if supplied - allowed:
        unexpected = ", ".join(sorted(supplied - allowed))
        raise InvalidReviewError(f"'{decision.action}' does not take: {unexpected}")

    capture = _lock_capture(session, actor, capture_id)
    _require_open(capture, expected_version)
    line = _find_line(capture, line_number)
    now = now or _utcnow()

    # Every check runs before anything is changed.
    if decision.action == "confirm_unit":
        if line.resolution == CaptureLineResolution.EXCLUDED:
            raise InvalidReviewError("This line is excluded; reset it first.")
        unit, quantity = _validated_unit(decision.unit, decision.quantity)
        quantity_cases = None
        if line.resolution == CaptureLineResolution.INCLUDED:
            product = _active_product(session, line.selected_product_id)
            quantity_cases = cases_for(unit, quantity, product)
        _apply_unit(line, unit, quantity, actor, now)
        if quantity_cases is not None:
            line.quantity_cases = quantity_cases
            _mark_reviewed(line, actor, now)

    elif decision.action == "clear_unit":
        if line.resolution == CaptureLineResolution.INCLUDED:
            raise InvalidReviewError(
                "An included line needs its unit; reset or exclude the line first."
            )
        line.unit_confirmed = False
        line.confirmed_unit = None
        line.confirmed_quantity = None
        line.unit_confirmed_by_user_id = None
        line.unit_confirmed_at = None

    elif decision.action == "include":
        product = _active_product(session, decision.product_id)
        new_unit = decision.unit is not None or decision.quantity is not None
        if new_unit:
            unit, quantity = _validated_unit(decision.unit, decision.quantity)
        elif line.unit_confirmed:
            unit, quantity = line.confirmed_unit, line.confirmed_quantity
        else:
            raise InvalidReviewError(
                "Confirm the unit (cases or pieces) and quantity before "
                "including this line."
            )
        quantity_cases = cases_for(unit, quantity, product)
        _check_alias_choice(session, actor, capture, line, product, decision)
        if new_unit:
            _apply_unit(line, unit, quantity, actor, now)
        line.resolution = CaptureLineResolution.INCLUDED
        # Set the relationship too, so responses show the new product at once.
        line.selected_product = product
        line.selected_product_id = product.id
        line.quantity_cases = quantity_cases
        line.exclusion_reason = None
        line.remember_alias = decision.remember_alias
        line.replace_existing_alias = decision.replace_existing_alias
        _mark_reviewed(line, actor, now)

    elif decision.action == "exclude":
        reason = _valid_reason(decision.reason, "reason for excluding the line")
        _clear_decision(line)
        line.resolution = CaptureLineResolution.EXCLUDED
        line.exclusion_reason = reason
        _mark_reviewed(line, actor, now)

    else:  # reset
        _clear_decision(line)

    _changed(capture)
    session.flush()
    return capture


def discard_capture(
    session: Session,
    actor: CaptureActor,
    capture_id: UUID,
    *,
    expected_version: int,
    reason: str,
    now: datetime | None = None,
) -> OrderCapture:
    """Discard a capture that has not been submitted, with a reason."""
    capture = _lock_capture(session, actor, capture_id)
    _require_open(capture, expected_version)
    reason = _valid_reason(reason, "reason for discarding")
    capture.status = CaptureStatus.DISCARDED
    capture.discard_reason = reason
    capture.discarded_at = now or _utcnow()
    capture.discarded_by_user_id = actor.user_id
    capture.version += 1
    session.flush()
    return capture


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def _save_aliases(
    session: Session,
    capture: OrderCapture,
    included: list[OrderCaptureLine],
    now: datetime,
) -> None:
    """Save the aliases the order desk opted in to, never overwriting one.

    The alias is credited to the desk user who made the choice on the line.
    Replacing deactivates the old row (who and when) and links it to its
    replacement, so the history remains.
    """
    for line in included:
        if not line.remember_alias or line.product_text is None:
            continue
        chooser = line.reviewed_by_user_id
        assert chooser is not None
        normalized = normalize_phrase(line.product_text)
        _advisory_lock(session, _lock_key("alias", capture.retailer_id, normalized))
        existing = _active_alias(session, capture.retailer_id, normalized, lock=True)

        if existing is not None and existing.product_id == line.selected_product_id:
            continue
        if existing is not None and not line.replace_existing_alias:
            raise AliasConflictError(
                line.line_number, line.product_text, existing.product_id
            )

        if existing is not None:
            existing.is_active = False
            existing.deactivated_at = now
            existing.deactivated_by_user_id = chooser
            # Free the one-active-alias index before adding the replacement.
            session.flush()

        alias = RetailerProductAlias(
            id=uuid4(),
            retailer_id=capture.retailer_id,
            product_id=line.selected_product_id,
            alias_text=line.product_text,
            normalized_alias=normalized,
            is_active=True,
            created_by_user_id=chooser,
            source_capture_line_id=line.id,
            created_at=now,
        )
        session.add(alias)
        session.flush()
        if existing is not None:
            existing.replaced_by_alias_id = alias.id


def submit_capture(
    session: Session,
    actor: CaptureActor,
    capture_id: UUID,
    *,
    expected_version: int,
    client_request_id: UUID,
    now: datetime | None = None,
) -> SubmissionOutcome:
    """Create the PENDING_REVIEW order for a READY capture.

    A capture that is already submitted returns its order (created=False),
    so a repeated request never creates a second order. Raises
    CaptureNotFoundError, CaptureStateError, StaleCaptureVersionError,
    CaptureNotReadyError, InvalidReviewError, CaptureLinesInvalidError,
    AliasConflictError, and the order service's RetailerNotOrderableError or
    IdempotencyConflictError. Nothing changes on any error.
    """
    capture = _lock_capture(session, actor, capture_id)
    if capture.status == CaptureStatus.SUBMITTED:
        order = session.get(Order, capture.order_id)
        assert order is not None
        return SubmissionOutcome(capture=capture, order=order, created=False)
    _require_open(capture, expected_version)

    lines = list(capture.lines)
    unresolved = tuple(
        line.line_number
        for line in lines
        if line.resolution == CaptureLineResolution.PENDING
    )
    included = _included(lines)
    if unresolved or not included or _status_for(lines) != CaptureStatus.READY:
        raise CaptureNotReadyError(unresolved)

    # Re-derive case quantities from the current products: a product's case
    # size may have changed since the line was reviewed.
    products = {
        product.id: product
        for product in session.scalars(
            select(Product).where(
                Product.id.in_({line.selected_product_id for line in included})
            )
        )
    }
    for line in included:
        product = products.get(line.selected_product_id)
        if product is not None:
            line.quantity_cases = cases_for(
                line.confirmed_unit, line.confirmed_quantity, product
            )

    now = now or _utcnow()
    try:
        result = submit_capture_order(
            session,
            capture.retailer_id,
            [
                OrderLineInput(
                    product_id=line.selected_product_id, quantity=line.quantity_cases
                )
                for line in included
            ],
            capture_id=capture.id,
            client_request_id=client_request_id,
            actor_user_id=actor.user_id,
            now=now,
        )
    except OrderLinesInvalidError as error:
        # Order line numbers count included lines only; report capture lines.
        raise CaptureLinesInvalidError(
            tuple(
                CaptureLineIssue(
                    line_number=included[issue.line_number - 1].line_number,
                    product_id=issue.product_id,
                    code=issue.code,
                    message=issue.message,
                )
                for issue in error.issues
            )
        ) from None

    _save_aliases(session, capture, included, now)

    capture.order = result.order
    capture.order_id = result.order.id
    capture.status = CaptureStatus.SUBMITTED
    capture.submitted_at = now
    capture.submitted_by_user_id = actor.user_id
    capture.version += 1
    session.flush()
    return SubmissionOutcome(capture=capture, order=result.order, created=True)


def candidate_stock(session: Session, capture: OrderCapture) -> dict[UUID, int]:
    """Current sellable stock (cases) of every product a capture shows.

    Display only, read at request time: an inactive product counts as 0.
    Stock is checked again, authoritatively, when the order is created and
    when the order desk confirms it.
    """
    ids: set[UUID] = set()
    for line in capture.lines:
        for entry in line.candidates or []:
            ids.add(UUID(str(entry["product_id"])))
        if line.selected_product_id is not None:
            ids.add(line.selected_product_id)
    if not ids:
        return {}
    return {
        product_id: stock if status == ProductStatus.ACTIVE else 0
        for product_id, stock, status in session.execute(
            select(Product.id, Product.stock_quantity, Product.status).where(
                Product.id.in_(ids)
            )
        )
    }
