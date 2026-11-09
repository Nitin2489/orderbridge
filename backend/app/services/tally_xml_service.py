"""TallyPrime XML sales vouchers for an export batch.

Built from the batch's stored CSV (tally_export_service), so the XML always
describes exactly what was exported, even if a retailer or product is
renamed later, and needs no extra storage. The CSV itself is unchanged.

Structure follows Tally's documented "Sales Transaction - As Invoice" import
sample (ENVELOPE / HEADER: Import, Data, Vouchers / BODY / DESC / DATA /
TALLYMESSAGE / VOUCHER). One voucher per order:

    LEDGERENTRIES.LIST    party ledger     ISDEEMEDPOSITIVE Yes  AMOUNT -grand total
    LEDGERENTRIES.LIST    discount ledger  ISDEEMEDPOSITIVE Yes  AMOUNT -discount total
                          (only when the order has a discount)
    ALLINVENTORYENTRIES.LIST per line      ISDEEMEDPOSITIVE No   AMOUNT  rate x billed
        RATE "777.60/case", BILLEDQTY = ordered cases,
        ACTUALQTY = ordered + free cases (free goods move stock, not money)
        ACCOUNTINGALLOCATIONS.LIST  sales ledger, same amount

Debits are negative and credits positive; every voucher's ledger and
inventory amounts must sum to exactly zero, which is checked before any XML
is produced. The discount is booked once, as its own ledger entry; item
amounts are gross.

Not handled, by design: GST (no rates, HSN codes or registrations are
available; amounts exclude GST), Tally master creation, and direct transfer
to Tally. Tally ledger, stock item and unit names must already exist with
exactly the names listed by required_masters().
"""

import csv
import io
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.services.tally_export_service import CSV_BOM, CSV_COLUMNS

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
#: Tally's default location and batch, as in its documented invoice sample.
DEFAULT_GODOWN = "Main Location"
DEFAULT_BATCH = "Primary Batch"

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
#: Characters XML 1.0 cannot hold (most control characters, surrogates and
#: U+FFFE/U+FFFF). A name containing one would make the whole file invalid.
_XML_INVALID = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


class TallyXmlError(Exception):
    """The XML cannot be produced."""


class TallyConfigurationError(TallyXmlError):
    """A required Tally name is not configured."""


class BatchDataError(TallyXmlError):
    """The stored CSV is not a consistent export (never adjusted silently)."""


@dataclass(frozen=True)
class TallySettings:
    sales_ledger: str | None
    discount_ledger: str | None
    voucher_type: str = "Sales"


@dataclass(frozen=True)
class VoucherLine:
    line_number: int
    sku: str
    item_name: str
    billed_quantity: int
    free_quantity: int
    unit: str
    rate: Decimal
    gross_amount: Decimal
    discount_amount: Decimal
    net_amount: Decimal

    @property
    def actual_quantity(self) -> int:
        return self.billed_quantity + self.free_quantity


@dataclass(frozen=True)
class Voucher:
    order_number: str
    voucher_date: str  # YYYYMMDD, the confirmation date in India time
    party_name: str
    subtotal: Decimal
    discount_total: Decimal
    net_total: Decimal
    lines: tuple[VoucherLine, ...]


@dataclass(frozen=True)
class StockItemMaster:
    name: str
    unit: str


@dataclass(frozen=True)
class RequiredMasters:
    voucher_type: str
    sales_ledger: str | None
    discount_ledger: str | None
    discount_needed: bool
    party_ledgers: tuple[str, ...]
    stock_items: tuple[StockItemMaster, ...]
    units: tuple[str, ...]


# ---------------------------------------------------------------------------
# Reading the stored CSV
# ---------------------------------------------------------------------------


def _unprotect(value: str) -> str:
    """Undo tally_export_service.protect_text: drop the apostrophe it adds
    before a value that starts like a spreadsheet formula."""
    if value.startswith("'") and value[1:].startswith(_FORMULA_PREFIXES):
        return value[1:]
    return value


def _money(value: str, field: str, order: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise BatchDataError(f"{order}: {field} is not an amount.") from None
    if amount != amount.quantize(CENT) or amount < 0:
        raise BatchDataError(f"{order}: {field} is not a valid amount.")
    return amount.quantize(CENT)


def _whole(value: str, field: str, order: str) -> int:
    if not value.isdigit():
        raise BatchDataError(f"{order}: {field} is not a whole number.")
    return int(value)


def _voucher_date(confirmed_at: str, order: str) -> str:
    try:
        moment = datetime.fromisoformat(confirmed_at)
    except ValueError:
        raise BatchDataError(f"{order}: confirmed_at is not a date.") from None
    # The CSV stores the confirmation time already in India time (+05:30).
    return moment.strftime("%Y%m%d")


def read_batch_csv(content: str) -> list[Voucher]:
    """Vouchers from a stored export CSV, in file order, fully checked."""
    reader = csv.reader(io.StringIO(content.removeprefix(CSV_BOM)))
    header = next(reader, None)
    if header is None or tuple(header) != CSV_COLUMNS:
        raise BatchDataError("The stored export does not have the expected columns.")

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in reader:
        if len(row) != len(CSV_COLUMNS):
            raise BatchDataError("A stored export row has the wrong number of fields.")
        record = dict(zip(CSV_COLUMNS, row, strict=True))
        grouped.setdefault(record["order_number"], []).append(record)

    vouchers = []
    for order_number, rows in grouped.items():
        first = rows[0]
        for field in (
            "confirmed_at",
            "party_name",
            "order_subtotal",
            "order_discount_total",
            "order_net_total",
        ):
            if any(row[field] != first[field] for row in rows):
                raise BatchDataError(f"{order_number}: rows disagree on {field}.")

        lines = []
        for row in rows:
            line = VoucherLine(
                line_number=_whole(row["line_number"], "line_number", order_number),
                sku=_unprotect(row["sku"]),
                item_name=_unprotect(row["item_name"]),
                billed_quantity=_whole(row["quantity"], "quantity", order_number),
                free_quantity=_whole(
                    row["free_quantity"], "free_quantity", order_number
                ),
                unit=_unprotect(row["unit"]),
                rate=_money(row["rate"], "rate", order_number),
                gross_amount=_money(row["gross_amount"], "gross_amount", order_number),
                discount_amount=_money(
                    row["discount_amount"], "discount_amount", order_number
                ),
                net_amount=_money(row["net_amount"], "net_amount", order_number),
            )
            if line.billed_quantity <= 0:
                raise BatchDataError(f"{order_number}: a line has no billed quantity.")
            if (line.rate * line.billed_quantity).quantize(CENT) != line.gross_amount:
                raise BatchDataError(
                    f"{order_number}: line {line.line_number} amount is not rate "
                    "x quantity."
                )
            if line.gross_amount - line.discount_amount != line.net_amount:
                raise BatchDataError(
                    f"{order_number}: line {line.line_number} net amount does not "
                    "equal amount less discount."
                )
            if not line.item_name or not line.unit:
                raise BatchDataError(f"{order_number}: a line has no item or unit.")
            if _XML_INVALID.search(line.item_name) or _XML_INVALID.search(line.unit):
                raise BatchDataError(
                    f"{order_number}: line {line.line_number}'s product name or unit "
                    "contains a control character that XML cannot hold. Correct "
                    "the product name."
                )
            lines.append(line)

        voucher = Voucher(
            order_number=_unprotect(order_number),
            voucher_date=_voucher_date(first["confirmed_at"], order_number),
            party_name=_unprotect(first["party_name"]),
            subtotal=_money(first["order_subtotal"], "order_subtotal", order_number),
            discount_total=_money(
                first["order_discount_total"], "order_discount_total", order_number
            ),
            net_total=_money(first["order_net_total"], "order_net_total", order_number),
            lines=tuple(sorted(lines, key=lambda line: line.line_number)),
        )
        if not voucher.party_name:
            raise BatchDataError(f"{order_number}: the party name is missing.")
        if _XML_INVALID.search(voucher.party_name) or _XML_INVALID.search(
            voucher.order_number
        ):
            raise BatchDataError(
                f"{order_number}: the retailer name contains a control character "
                "that XML cannot hold. Correct the retailer name."
            )
        if sum((line.gross_amount for line in lines), ZERO) != voucher.subtotal:
            raise BatchDataError(
                f"{order_number}: lines do not add up to the subtotal."
            )
        if (
            sum((line.discount_amount for line in lines), ZERO)
            != voucher.discount_total
        ):
            raise BatchDataError(
                f"{order_number}: line discounts do not add up to the discount total."
            )
        if voucher.subtotal - voucher.discount_total != voucher.net_total:
            raise BatchDataError(
                f"{order_number}: subtotal less discount does not equal the total."
            )
        vouchers.append(voucher)

    if not vouchers:
        raise BatchDataError("The stored export has no orders.")
    return vouchers


# ---------------------------------------------------------------------------
# Masters and settings
# ---------------------------------------------------------------------------


def _clean(name: str | None) -> str | None:
    return name.strip() if name and name.strip() else None


def required_masters(
    vouchers: Sequence[Voucher], settings: TallySettings
) -> RequiredMasters:
    """Every Tally master the vouchers refer to, sorted, without duplicates."""
    items = {(line.item_name, line.unit) for v in vouchers for line in v.lines}
    return RequiredMasters(
        voucher_type=settings.voucher_type,
        sales_ledger=_clean(settings.sales_ledger),
        discount_ledger=_clean(settings.discount_ledger),
        discount_needed=any(v.discount_total > 0 for v in vouchers),
        party_ledgers=tuple(sorted({v.party_name for v in vouchers})),
        stock_items=tuple(StockItemMaster(n, u) for n, u in sorted(items)),
        units=tuple(sorted({unit for _, unit in items})),
    )


def missing_settings(masters: RequiredMasters) -> list[str]:
    missing = []
    if masters.sales_ledger is None:
        missing.append("TALLY_SALES_LEDGER_NAME")
    if masters.discount_needed and masters.discount_ledger is None:
        missing.append("TALLY_DISCOUNT_LEDGER_NAME")
    return missing


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------


def _amount(value: Decimal) -> str:
    return format(value.quantize(CENT), "f")


def _quantity(value: int, unit: str) -> str:
    return f"{value} {unit}"


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    element = ET.SubElement(parent, tag)
    if text is not None:
        element.text = text
    return element


def _check_balance(voucher: Voucher) -> None:
    """Ledger and inventory amounts must sum to exactly zero."""
    total = -voucher.net_total - voucher.discount_total
    total += sum((line.gross_amount for line in voucher.lines), ZERO)
    if total != ZERO:
        raise BatchDataError(f"{voucher.order_number}: the voucher does not balance.")


def _voucher_element(
    voucher: Voucher, batch_number: str, masters: RequiredMasters
) -> ET.Element:
    assert masters.sales_ledger is not None
    element = ET.Element(
        "VOUCHER",
        {
            "VCHTYPE": masters.voucher_type,
            "ACTION": "Create",
            "OBJVIEW": "Invoice Voucher View",
        },
    )
    _sub(element, "DATE", voucher.voucher_date)
    _sub(element, "VOUCHERTYPENAME", masters.voucher_type)
    _sub(element, "VOUCHERNUMBER", voucher.order_number)
    _sub(element, "PARTYLEDGERNAME", voucher.party_name)
    _sub(element, "PERSISTEDVIEW", "Invoice Voucher View")
    _sub(element, "ISINVOICE", "Yes")
    _sub(
        element,
        "NARRATION",
        f"OrderBridge order {voucher.order_number}, export {batch_number}. "
        "Amounts exclude GST.",
    )

    party = _sub(element, "LEDGERENTRIES.LIST")
    _sub(party, "LEDGERNAME", voucher.party_name)
    _sub(party, "ISDEEMEDPOSITIVE", "Yes")
    _sub(party, "ISPARTYLEDGER", "Yes")
    _sub(party, "ISLASTDEEMEDPOSITIVE", "Yes")
    _sub(party, "AMOUNT", _amount(-voucher.net_total))
    bill = _sub(party, "BILLALLOCATIONS.LIST")
    _sub(bill, "NAME", voucher.order_number)
    _sub(bill, "BILLTYPE", "New Ref")
    _sub(bill, "AMOUNT", _amount(-voucher.net_total))

    if voucher.discount_total > 0:
        assert masters.discount_ledger is not None
        discount = _sub(element, "LEDGERENTRIES.LIST")
        _sub(discount, "LEDGERNAME", masters.discount_ledger)
        _sub(discount, "ISDEEMEDPOSITIVE", "Yes")
        _sub(discount, "AMOUNT", _amount(-voucher.discount_total))

    for line in voucher.lines:
        entry = _sub(element, "ALLINVENTORYENTRIES.LIST")
        _sub(entry, "STOCKITEMNAME", line.item_name)
        _sub(entry, "ISDEEMEDPOSITIVE", "No")
        _sub(entry, "RATE", f"{_amount(line.rate)}/{line.unit}")
        _sub(entry, "AMOUNT", _amount(line.gross_amount))
        _sub(entry, "ACTUALQTY", _quantity(line.actual_quantity, line.unit))
        _sub(entry, "BILLEDQTY", _quantity(line.billed_quantity, line.unit))
        batch = _sub(entry, "BATCHALLOCATIONS.LIST")
        _sub(batch, "GODOWNNAME", DEFAULT_GODOWN)
        _sub(batch, "BATCHNAME", DEFAULT_BATCH)
        _sub(batch, "AMOUNT", _amount(line.gross_amount))
        _sub(batch, "ACTUALQTY", _quantity(line.actual_quantity, line.unit))
        _sub(batch, "BILLEDQTY", _quantity(line.billed_quantity, line.unit))
        allocation = _sub(entry, "ACCOUNTINGALLOCATIONS.LIST")
        _sub(allocation, "LEDGERNAME", masters.sales_ledger)
        _sub(allocation, "ISDEEMEDPOSITIVE", "No")
        _sub(allocation, "AMOUNT", _amount(line.gross_amount))

    return element


def build_tally_xml(
    batch_number: str, vouchers: Sequence[Voucher], settings: TallySettings
) -> bytes:
    """The import file (UTF-8). Raises TallyConfigurationError when a needed
    ledger name is not configured, or BatchDataError when a voucher would
    not balance. Deterministic for the same batch and settings."""
    masters = required_masters(vouchers, settings)
    missing = missing_settings(masters)
    if missing:
        raise TallyConfigurationError(
            "Set " + " and ".join(missing) + " to the ledger names in Tally."
        )
    for voucher in vouchers:
        _check_balance(voucher)

    envelope = ET.Element("ENVELOPE")
    header = _sub(envelope, "HEADER")
    _sub(header, "TALLYREQUEST", "Import")
    _sub(header, "TYPE", "Data")
    _sub(header, "ID", "Vouchers")
    body = _sub(envelope, "BODY")
    _sub(body, "DESC")
    data = _sub(body, "DATA")
    for voucher in vouchers:
        message = _sub(data, "TALLYMESSAGE")
        message.append(_voucher_element(voucher, batch_number, masters))

    ET.indent(envelope)
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True) + b"\n"
