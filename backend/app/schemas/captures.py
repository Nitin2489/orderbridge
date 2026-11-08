"""Request and response schemas for /api/v1/captures.

Requests carry only decisions and identifiers; unknown fields are refused,
so a client cannot set statuses, scores, prices, actors or a retailer it
does not own. Responses never include prices: pricing happens when the
order is created and confirmed through the existing order workflow.

Match scores are deterministic heuristic ranking scores (0-100). They are
not probabilities or confidence percentages.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    CaptureChannel,
    CaptureLineResolution,
    CaptureStatus,
    ConfirmedUnit,
    OrderCapture,
    OrderCaptureLine,
    OrderStatus,
    UnitHint,
)
from app.models.capture import MAX_CAPTURE_MESSAGE_LENGTH, MAX_CONFIRMED_QUANTITY
from app.services.capture_interpreter import RULES_INTERPRETER_NAME, InterpreterKind
from app.services.capture_matcher import Candidate, MatchBand
from app.services.order_service import LineIssueCode

MATCH_SCORE_DESCRIPTION = (
    "Deterministic heuristic ranking score from 0 to 100, with its reasons. "
    "Not a probability or a confidence percentage."
)
_LONG_TEXT = 2000

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class CreateCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID = Field(
        description=(
            "Client-generated UUID. Resending the same message with the same "
            "id returns the original capture; reusing it for a different "
            "message is rejected (409)."
        )
    )
    message: str = Field(
        min_length=1,
        max_length=MAX_CAPTURE_MESSAGE_LENGTH,
        description="The message exactly as received; stored unchanged.",
    )
    retailer_id: UUID | None = Field(
        default=None,
        description=(
            "Order desk only: the retailer the message is from. Retailers "
            "must omit it; their captures are always for their own account."
        ),
    )


class ReviewLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, description="The capture version you saw.")
    action: Literal["confirm_unit", "clear_unit", "include", "exclude", "reset"]
    unit: ConfirmedUnit | None = Field(
        default=None, description="confirm_unit / include: 'case' or 'piece'."
    )
    quantity: int | None = Field(
        default=None,
        ge=1,
        le=MAX_CONFIRMED_QUANTITY,
        description="confirm_unit / include: the quantity in that unit.",
    )
    product_id: UUID | None = Field(default=None, description="include: the product.")
    reason: str | None = Field(
        default=None,
        max_length=_LONG_TEXT,
        description="exclude: 3 to 500 characters after trimming.",
    )
    remember_alias: bool = Field(
        default=False,
        description=(
            "include, order desk only, corrected lines only: save this line's "
            "product phrase as an alias for the retailer when submitted."
        ),
    )
    replace_existing_alias: bool = Field(
        default=False,
        description=(
            "Explicitly replace an active alias of this retailer that maps the "
            "same phrase to another product (the old alias is kept, inactive)."
        ),
    )


class DiscardCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str = Field(max_length=_LONG_TEXT, description="3 to 500 characters.")


class SubmitCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    client_request_id: UUID = Field(
        description="Idempotency key for the order this submission creates."
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class CaptureRetailer(BaseModel):
    id: UUID
    business_name: str


class CaptureOrderLink(BaseModel):
    """The order created on submission. Its status is the order's own: a
    submitted capture's order still awaits the order desk's confirmation."""

    id: UUID
    order_number: str
    status: OrderStatus


class InterpretationInfo(BaseModel):
    name: str
    kind: InterpreterKind
    is_ai_model: bool
    description: str


class CandidateResponse(BaseModel):
    product_id: UUID
    sku: str
    name: str
    match_score: int = Field(ge=0, le=100, description=MATCH_SCORE_DESCRIPTION)
    band: MatchBand
    weak_match: bool = Field(description="True for the weak and very weak bands.")
    reasons: list[str]
    stock_cases: int | None = Field(
        default=None,
        description=(
            "Current stock in cases, for display (0 when the product is not "
            "available). Checked again when the order is created and confirmed."
        ),
    )

    @classmethod
    def from_candidate(
        cls, candidate: Candidate, stock: Mapping[UUID, int] | None = None
    ) -> "CandidateResponse":
        return cls(
            stock_cases=(stock or {}).get(candidate.product_id),
            product_id=candidate.product_id,
            sku=candidate.sku,
            name=candidate.name,
            match_score=candidate.score,
            band=candidate.band,
            weak_match=candidate.weak_match,
            reasons=list(candidate.reasons),
        )


class LineMatchResponse(BaseModel):
    proposal: CandidateResponse | None = Field(
        description="The suggested product, if one is clearly ahead. Not a decision."
    )
    alternatives: list[CandidateResponse] = Field(max_length=2)
    reasons: list[str]


class UnitDecisionResponse(BaseModel):
    confirmed: bool
    unit: ConfirmedUnit | None
    quantity: int | None
    quantity_cases: int | None = Field(
        description="Whole cases to order; set while the line is included."
    )


class SelectedProductResponse(BaseModel):
    id: UUID
    sku: str
    name: str
    units_per_case: int


class CaptureLineResponse(BaseModel):
    line_number: int
    start: int
    end: int
    original_text: str
    product_text: str | None
    product_start: int | None
    product_end: int | None
    quantity_text: str | None
    suggested_quantity: int | None
    unit_text: str | None
    unit_hint: UnitHint = Field(description="A hint only; never applied by itself.")
    notes: list[str]
    match: LineMatchResponse
    unit: UnitDecisionResponse
    resolution: CaptureLineResolution
    selected_product: SelectedProductResponse | None
    exclusion_reason: str | None
    is_corrected: bool
    remember_alias: bool
    replace_existing_alias: bool


class IgnoredTextResponse(BaseModel):
    start: int
    end: int
    text: str
    reason: Literal["greeting", "no_words"]


class CaptureSummary(BaseModel):
    id: UUID
    version: int
    status: CaptureStatus
    retailer: CaptureRetailer
    message_preview: str
    line_count: int
    unresolved_count: int
    created_at: datetime
    order: CaptureOrderLink | None

    @classmethod
    def from_capture(cls, capture: OrderCapture) -> "CaptureSummary":
        return cls(
            id=capture.id,
            version=capture.version,
            status=CaptureStatus(capture.status),
            retailer=_retailer(capture),
            message_preview=_preview(capture.original_message),
            line_count=len(capture.lines),
            unresolved_count=len(_unresolved(capture)),
            created_at=capture.created_at,
            order=_order(capture),
        )


class CapturePage(BaseModel):
    items: list[CaptureSummary]
    total: int
    limit: int
    offset: int


class CaptureDetail(BaseModel):
    id: UUID
    version: int
    status: CaptureStatus
    channel: CaptureChannel
    interpretation: InterpretationInfo
    retailer: CaptureRetailer
    original_message: str = Field(description="Exactly as received.")
    ignored: list[IgnoredTextResponse] = Field(
        description="Message text that is not part of any line, and why."
    )
    created_at: datetime
    submitted_at: datetime | None
    discarded_at: datetime | None
    discard_reason: str | None
    order: CaptureOrderLink | None
    can_submit: bool
    unresolved_lines: list[int]
    lines: list[CaptureLineResponse]

    @classmethod
    def from_capture(
        cls, capture: OrderCapture, stock: Mapping[UUID, int] | None = None
    ) -> "CaptureDetail":
        data = capture.extracted_data or {}
        notes = {
            entry.get("line_number"): list(entry.get("notes", []))
            for entry in data.get("lines", [])
        }
        return cls(
            id=capture.id,
            version=capture.version,
            status=CaptureStatus(capture.status),
            channel=CaptureChannel(capture.channel),
            interpretation=_interpretation(capture.interpreter),
            retailer=_retailer(capture),
            original_message=capture.original_message,
            ignored=[IgnoredTextResponse(**span) for span in data.get("ignored", [])],
            created_at=capture.created_at,
            submitted_at=capture.submitted_at,
            discarded_at=capture.discarded_at,
            discard_reason=capture.discard_reason,
            order=_order(capture),
            can_submit=capture.status == CaptureStatus.READY,
            unresolved_lines=_unresolved(capture),
            lines=[
                _line(line, notes.get(line.line_number, []), stock)
                for line in capture.lines
            ],
        )


class SubmitCaptureResponse(BaseModel):
    created: bool = Field(
        description="False when the capture had already been submitted."
    )
    order: CaptureOrderLink
    capture: CaptureDetail


# Error bodies


class StaleCaptureResponse(BaseModel):
    detail: str
    current_version: int


class CaptureNotReadyResponse(BaseModel):
    detail: str
    unresolved_lines: list[int]


class CaptureLineIssueResponse(BaseModel):
    line_number: int = Field(description="The capture line number.")
    product_id: UUID
    code: LineIssueCode
    message: str


class CaptureLinesErrorResponse(BaseModel):
    detail: str
    issues: list[CaptureLineIssueResponse]


class AliasConflictResponse(BaseModel):
    detail: str
    line_number: int
    alias_text: str
    current_product_id: UUID


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

_PREVIEW_LENGTH = 120


def _preview(message: str) -> str:
    flat = " ".join(message.split())
    return flat if len(flat) <= _PREVIEW_LENGTH else flat[: _PREVIEW_LENGTH - 1] + "…"


def _retailer(capture: OrderCapture) -> CaptureRetailer:
    return CaptureRetailer(
        id=capture.retailer.id, business_name=capture.retailer.business_name
    )


def _order(capture: OrderCapture) -> CaptureOrderLink | None:
    order = capture.order
    if order is None:
        return None
    return CaptureOrderLink(
        id=order.id, order_number=order.order_number, status=OrderStatus(order.status)
    )


def _unresolved(capture: OrderCapture) -> list[int]:
    return [
        line.line_number
        for line in capture.lines
        if line.resolution == CaptureLineResolution.PENDING
    ]


def _interpretation(name: str) -> InterpretationInfo:
    if name == RULES_INTERPRETER_NAME:
        return InterpretationInfo(
            name=name,
            kind=InterpreterKind.RULES,
            is_ai_model=False,
            description=(
                "Rule-based interpretation: deterministic word rules and "
                "product matching. Not an AI model."
            ),
        )
    return InterpretationInfo(
        name=name,
        kind=InterpreterKind.LANGUAGE_MODEL,
        is_ai_model=True,
        description="Interpreted by a language model.",
    )


def _line(
    line: OrderCaptureLine,
    notes: list[str],
    stock: Mapping[UUID, int] | None = None,
) -> CaptureLineResponse:
    candidates = [Candidate.model_validate(entry) for entry in line.candidates or []]
    proposal = next(
        (c for c in candidates if c.product_id == line.proposed_product_id), None
    )
    others = [c for c in candidates if c is not proposal]
    product = line.selected_product
    return CaptureLineResponse(
        line_number=line.line_number,
        start=line.start_offset,
        end=line.end_offset,
        original_text=line.original_text,
        product_text=line.product_text,
        product_start=line.product_start_offset,
        product_end=line.product_end_offset,
        quantity_text=line.quantity_text,
        suggested_quantity=line.suggested_quantity,
        unit_text=line.unit_text,
        unit_hint=UnitHint(line.unit_hint),
        notes=notes,
        match=LineMatchResponse(
            proposal=(
                CandidateResponse.from_candidate(proposal, stock) if proposal else None
            ),
            alternatives=[
                CandidateResponse.from_candidate(c, stock) for c in others[:2]
            ],
            reasons=list(line.match_reasons or []),
        ),
        unit=UnitDecisionResponse(
            confirmed=line.unit_confirmed,
            unit=ConfirmedUnit(line.confirmed_unit) if line.confirmed_unit else None,
            quantity=line.confirmed_quantity,
            quantity_cases=line.quantity_cases,
        ),
        resolution=CaptureLineResolution(line.resolution),
        selected_product=(
            SelectedProductResponse(
                id=product.id,
                sku=product.sku,
                name=product.name,
                units_per_case=product.units_per_case,
            )
            if product is not None
            else None
        ),
        exclusion_reason=line.exclusion_reason,
        is_corrected=(
            line.resolution == CaptureLineResolution.INCLUDED
            and line.selected_product_id != line.proposed_product_id
        ),
        remember_alias=line.remember_alias,
        replace_existing_alias=line.replace_existing_alias,
    )
