"""TallyPrime XML vouchers built from a stored export CSV (pure; no database).

The CSVs are produced by the real CSV builder (tally_export_service), so the
XML is tested against exactly what an export stores.
"""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.tally_export_service import (
    ExportLine,
    ExportOrder,
    build_rows,
    render_csv,
)
from app.services.tally_xml_service import (
    BatchDataError,
    TallyConfigurationError,
    TallySettings,
    build_tally_xml,
    read_batch_csv,
    required_masters,
)

SETTINGS = TallySettings(sales_ledger="Sales Ledger", discount_ledger="Discount Ledger")
CONFIRMED = datetime(2026, 11, 8, 18, 12, tzinfo=UTC)  # 23:42 India time


def line(number, sku, name, quantity, free, rate, discount="0.00"):
    gross = Decimal(rate) * quantity
    return ExportLine(
        line_number=number,
        sku=sku,
        item_name=name,
        quantity=quantity,
        free_quantity=free,
        unit="case",
        rate=Decimal(rate),
        discount_amount=Decimal(discount),
        net_amount=gross - Decimal(discount),
        scheme_name=None,
    )


def order(number, party, *lines, confirmed=CONFIRMED):
    subtotal = sum((ln.net_amount + ln.discount_amount for ln in lines), Decimal(0))
    discount = sum((ln.discount_amount for ln in lines), Decimal(0))
    return ExportOrder(
        order_number=number,
        created_at=confirmed,
        confirmed_at=confirmed,
        party_name=party,
        party_address="Jabalpur",
        subtotal=subtotal,
        discount_total=discount,
        net_total=subtotal - discount,
        lines=tuple(lines),
    )


ORDINARY = order(
    "OB-2026-000002",
    "Gupta Kirana Store",
    line(1, "SKU-BS-075", "Glucose Biscuit 75g", 5, 0, "777.60"),
    line(2, "SKU-DT-1KG", "Detergent Powder 1kg", 2, 0, "2030.00"),
)
SLAB = order(
    "OB-2026-000003",
    "Sharma General Store",
    line(1, "SKU-DT-1KG", "Detergent Powder 1kg", 5, 0, "2030.00", "304.50"),
)
FREE_GOODS = order(
    "OB-2026-000004",
    "Patel Provision Store",
    line(1, "SKU-BS-075", "Glucose Biscuit 75g", 10, 1, "777.60"),
)


def csv_for(*orders):
    return render_csv(build_rows("EX-2026-000009", orders))


def xml_for(*orders, settings=SETTINGS):
    return build_tally_xml("EX-2026-000009", read_batch_csv(csv_for(*orders)), settings)


def vouchers(xml: bytes) -> list[ET.Element]:
    return ET.fromstring(xml).findall("./BODY/DATA/TALLYMESSAGE/VOUCHER")


def amounts(voucher: ET.Element) -> list[Decimal]:
    """Every amount Tally balances: ledger entries and inventory entries."""
    return [
        Decimal(entry.findtext("AMOUNT"))
        for entry in voucher.findall("LEDGERENTRIES.LIST")
        + voucher.findall("ALLINVENTORYENTRIES.LIST")
    ]


def ledger(voucher, name):
    (entry,) = [
        e
        for e in voucher.findall("LEDGERENTRIES.LIST")
        if e.findtext("LEDGERNAME") == name
    ]
    return entry


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


def test_envelope_matches_tallys_documented_import_structure():
    root = ET.fromstring(xml_for(ORDINARY))

    assert root.tag == "ENVELOPE"
    assert root.findtext("HEADER/TALLYREQUEST") == "Import"
    assert root.findtext("HEADER/TYPE") == "Data"
    assert root.findtext("HEADER/ID") == "Vouchers"
    assert root.find("BODY/DESC") is not None
    (voucher,) = vouchers(xml_for(ORDINARY))
    assert voucher.get("VCHTYPE") == "Sales"
    assert voucher.get("ACTION") == "Create"
    assert voucher.get("OBJVIEW") == "Invoice Voucher View"  # as in Tally's sample
    assert voucher.findtext("VOUCHERTYPENAME") == "Sales"
    assert voucher.findtext("VOUCHERNUMBER") == "OB-2026-000002"
    assert voucher.findtext("DATE") == "20261108"  # confirmation date, India time
    assert voucher.findtext("PERSISTEDVIEW") == "Invoice Voucher View"
    assert voucher.findtext("ISINVOICE") == "Yes"
    assert "Amounts exclude GST." in voucher.findtext("NARRATION")


def test_ordinary_order_debits_the_party_and_credits_sales():
    (voucher,) = vouchers(xml_for(ORDINARY))

    party = ledger(voucher, "Gupta Kirana Store")
    assert party.findtext("ISDEEMEDPOSITIVE") == "Yes"
    assert party.findtext("ISPARTYLEDGER") == "Yes"
    assert party.findtext("ISLASTDEEMEDPOSITIVE") == "Yes"  # as in Tally's sample
    assert party.findtext("AMOUNT") == "-7948.00"
    assert party.findtext("BILLALLOCATIONS.LIST/NAME") == "OB-2026-000002"
    assert party.findtext("BILLALLOCATIONS.LIST/AMOUNT") == "-7948.00"
    assert len(voucher.findall("LEDGERENTRIES.LIST")) == 1  # no discount entry
    items = voucher.findall("ALLINVENTORYENTRIES.LIST")
    assert [i.findtext("AMOUNT") for i in items] == ["3888.00", "4060.00"]
    for item in items:
        assert item.findtext("ISDEEMEDPOSITIVE") == "No"
        assert item.findtext("ACCOUNTINGALLOCATIONS.LIST/LEDGERNAME") == "Sales Ledger"
        assert item.findtext("ACCOUNTINGALLOCATIONS.LIST/ISDEEMEDPOSITIVE") == "No"
        assert item.findtext("ACCOUNTINGALLOCATIONS.LIST/AMOUNT") == item.findtext(
            "AMOUNT"
        )
    assert sum(amounts(voucher)) == 0


def test_slab_discount_is_booked_once_as_a_debit_and_the_voucher_balances():
    (voucher,) = vouchers(xml_for(SLAB))

    assert ledger(voucher, "Sharma General Store").findtext("AMOUNT") == "-9845.50"
    discount = ledger(voucher, "Discount Ledger")
    assert discount.findtext("ISDEEMEDPOSITIVE") == "Yes"
    assert discount.findtext("AMOUNT") == "-304.50"
    (item,) = voucher.findall("ALLINVENTORYENTRIES.LIST")
    # The item stays at gross (rate x billed); the discount is not deducted
    # from it as well.
    assert item.findtext("RATE") == "2030.00/case"
    assert item.findtext("AMOUNT") == "10150.00"
    assert item.findtext("ACCOUNTINGALLOCATIONS.LIST/AMOUNT") == "10150.00"
    assert amounts(voucher) == [
        Decimal("-9845.50"),
        Decimal("-304.50"),
        Decimal("10150.00"),
    ]
    assert sum(amounts(voucher)) == 0


def test_buy_10_get_1_bills_10_cases_and_moves_11_out_of_stock():
    (voucher,) = vouchers(xml_for(FREE_GOODS))

    (item,) = voucher.findall("ALLINVENTORYENTRIES.LIST")
    assert item.findtext("BILLEDQTY") == "10 case"
    assert item.findtext("ACTUALQTY") == "11 case"
    assert item.findtext("RATE") == "777.60/case"
    assert item.findtext("AMOUNT") == "7776.00"  # 10 x 777.60, free case unpaid
    assert item.findtext("BATCHALLOCATIONS.LIST/BILLEDQTY") == "10 case"
    assert item.findtext("BATCHALLOCATIONS.LIST/ACTUALQTY") == "11 case"
    assert item.findtext("BATCHALLOCATIONS.LIST/GODOWNNAME") == "Main Location"
    assert ledger(voucher, "Patel Provision Store").findtext("AMOUNT") == "-7776.00"
    assert sum(amounts(voucher)) == 0


def test_one_voucher_per_order_in_export_order():
    xml = xml_for(ORDINARY, SLAB, FREE_GOODS)
    assert [v.findtext("VOUCHERNUMBER") for v in vouchers(xml)] == [
        "OB-2026-000002",
        "OB-2026-000003",
        "OB-2026-000004",
    ]
    assert all(sum(amounts(v)) == 0 for v in vouchers(xml))


def test_the_same_batch_always_gives_identical_xml():
    assert xml_for(ORDINARY, SLAB) == xml_for(ORDINARY, SLAB)


# ---------------------------------------------------------------------------
# Names and text
# ---------------------------------------------------------------------------


def test_special_characters_and_formula_protection_round_trip():
    awkward = order(
        "OB-2026-000005",
        '=Shah & Sons <Kirana> "Best"',
        line(1, "SKU-1", "+Tea & Co 250g", 1, 0, "10.00"),
    )
    raw = csv_for(awkward)
    assert "'=Shah & Sons" in raw  # the CSV protects it for spreadsheets

    (voucher,) = vouchers(xml_for(awkward))
    assert voucher.findtext("PARTYLEDGERNAME") == '=Shah & Sons <Kirana> "Best"'
    assert (
        voucher.findtext("ALLINVENTORYENTRIES.LIST/STOCKITEMNAME") == "+Tea & Co 250g"
    )


def test_devanagari_names_are_kept():
    hindi = order(
        "OB-2026-000006", "शर्मा किराना", line(1, "S", "चाय 250g", 1, 0, "1.00")
    )
    (voucher,) = vouchers(xml_for(hindi))
    assert voucher.findtext("PARTYLEDGERNAME") == "शर्मा किराना"


def test_required_masters_lists_every_name_once():
    masters = required_masters(
        read_batch_csv(csv_for(ORDINARY, SLAB, FREE_GOODS)), SETTINGS
    )

    assert masters.party_ledgers == (
        "Gupta Kirana Store",
        "Patel Provision Store",
        "Sharma General Store",
    )
    assert [(s.name, s.unit) for s in masters.stock_items] == [
        ("Detergent Powder 1kg", "case"),
        ("Glucose Biscuit 75g", "case"),
    ]
    assert masters.units == ("case",)
    assert masters.discount_needed is True
    assert masters.sales_ledger == "Sales Ledger"
    assert masters.voucher_type == "Sales"


# ---------------------------------------------------------------------------
# Configuration and data checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sales", [None, "", "   "])
def test_missing_sales_ledger_is_refused(sales):
    with pytest.raises(TallyConfigurationError, match="TALLY_SALES_LEDGER_NAME"):
        xml_for(ORDINARY, settings=TallySettings(sales, "Discount Ledger"))


def test_discount_ledger_is_needed_only_when_there_is_a_discount():
    no_discount = TallySettings("Sales Ledger", None)
    assert vouchers(xml_for(ORDINARY, FREE_GOODS, settings=no_discount))
    with pytest.raises(TallyConfigurationError, match="TALLY_DISCOUNT_LEDGER_NAME"):
        xml_for(SLAB, settings=no_discount)


def tampered(old: str, new: str) -> str:
    content = csv_for(SLAB)
    assert old in content
    return content.replace(old, new)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (",9845.50,", ",9845.00,", "net amount does not equal"),
        (",10150.00,304.50,", ",10150.00,300.00,", "net amount does not equal"),
        (",2030.00,10150.00,", ",2031.00,10150.00,", "not rate x quantity"),
        (",17621.50,", ",1.00,", "subtotal"),
        (",5,0,case,", ",5.5,0,case,", "whole number"),
        (",304.50,9845.50,", ",304.505,9845.495,", "not a valid amount"),
    ],
)
def test_inconsistent_stored_data_is_refused_not_adjusted(old, new, message):
    if old == ",17621.50,":
        # SLAB alone has net 9845.50; make the order total disagree instead.
        old, new = ",9845.50,exclusive", ",9000.00,exclusive"
    with pytest.raises(BatchDataError, match=message):
        read_batch_csv(tampered(old, new))


def test_wrong_columns_are_refused():
    with pytest.raises(BatchDataError, match="expected columns"):
        read_batch_csv("order_number,party_name\r\nOB-1,X\r\n")


# ---------------------------------------------------------------------------
# Names XML cannot hold, and the voucher type setting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["\x01", "\x0b", "\x1f", "\ufffe"])
def test_control_character_in_a_retailer_name_is_refused_clearly(bad):
    awkward = order(
        "OB-2026-000007", f"Shah{bad}Stores", line(1, "S", "Tea", 1, 0, "1.00")
    )
    with pytest.raises(
        BatchDataError, match="retailer name contains a control character"
    ):
        read_batch_csv(csv_for(awkward))


@pytest.mark.parametrize("bad", ["\x00", "\x07", "\x1b"])
def test_control_character_in_a_product_name_is_refused_clearly(bad):
    awkward = order(
        "OB-2026-000008", "Shah Stores", line(1, "S", f"Tea{bad}250g", 1, 0, "1.00")
    )
    with pytest.raises(BatchDataError, match="product name or unit contains a control"):
        read_batch_csv(csv_for(awkward))


def test_tab_newline_and_non_latin_text_are_still_allowed():
    fine = order(
        "OB-2026-000009",
        "Shah\tStores शर्मा 😀",
        line(1, "S", "Tea\n250g", 1, 0, "1.00"),
    )
    (voucher,) = vouchers(xml_for(fine))
    assert voucher.findtext("PARTYLEDGERNAME") == "Shah\tStores शर्मा 😀"


def tally_settings(**values) -> Settings:
    return Settings(
        _env_file=None,
        app_env="testing",
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        **values,
    )


@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_blank_voucher_type_is_refused(blank):
    with pytest.raises(
        ValidationError, match="TALLY_VOUCHER_TYPE_NAME must not be blank"
    ):
        tally_settings(tally_voucher_type_name=blank)


def test_voucher_type_is_trimmed_and_defaults_to_sales():
    assert tally_settings().tally_voucher_type_name == "Sales"
    assert tally_settings(
        tally_voucher_type_name="  Sales GST  "
    ).tally_voucher_type_name == ("Sales GST")
