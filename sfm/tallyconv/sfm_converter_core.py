# ============================================================
# SFM FULL DATE-WISE + SHEET-WISE EXCEL TO TALLY XML CONVERTER - V12 FLEXIBLE DATE FIX
# For: SAI FUEL MART 26-27
# Reads: SFM Daily Readings.xlsx
# Creates: Tally XML + validation report
# Includes: HSD/MS/CNG/Lube/Payment/Receipt/Meter Reading
# V10 FINAL adds V9 pre-import summary, duplicate warning, folders, and auto previous-day meter opening. XML creation only; no direct Tally upload and no master sync.
# V7 adds CSV_OUTPUT folder for import-history CSV
# V6 adds XML_OUTPUT folder for XML/report files
# V5 adds duplicate warning using automatic import history
# Meter master updated for MPD4: 07 NOZZLE CNG 1 DU4 and 08 NOZZLE CNG 2 DU4
#
# Main feature:
# 1) First asks for one common date range for all sheets.
# 2) If you say No, it asks date range sheet-wise.
# ============================================================

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

try:
    from openpyxl import load_workbook
except ImportError:
    print("openpyxl is not installed. Run this once: pip install openpyxl")
    sys.exit(1)

# -----------------------------
# SETTINGS - edit only if exact Tally names change
# -----------------------------
EXCEL_FILE = "SFM Daily Readings.xlsx"
COMPANY_NAME = "SAI FUEL MART 26-27"
IMPORT_HISTORY_FILE = "SFM_Import_History.csv"
OUTPUT_FOLDER = "XML_OUTPUT"
CSV_FOLDER = "CSV_OUTPUT"

XML_FOLDER_README = """SFM XML_OUTPUT FOLDER
=====================

This folder is created automatically by the converter.

Files created here:
1. SFM_Tally_Import_*.xml
   - This is the file to import in Tally.
   - Tally path: O: Import -> Transactions/Vouchers -> select this XML file.

2. SFM_Tally_Import_Report_*.txt
   - Open this report before importing.
   - If STATUS is SUCCESS, XML is ready.
   - If STATUS is ERROR, fix the Excel errors first.

3. SFM_Duplicate_Warning_*.txt
   - Created only when selected date range overlaps earlier XML history.
   - If that earlier XML was already imported in Tally, importing again can duplicate vouchers.

After successful import in Tally, you can move old XML/report files to an OLD_XML_BACKUP folder.
Do not delete current XML until you have checked Day Book in Tally.
"""

CSV_FOLDER_README = """SFM CSV_OUTPUT FOLDER
=====================

This folder is created automatically by the converter.

Files created here:
1. SFM_Import_History.csv
   - This file stores every XML creation history.
   - It is used for duplicate/overlap warning.
   - Do not delete it unless you want to reset duplicate warning history.

Columns inside history file:
Run Time, XML File, Date Mode, Sheet Name, From Date, To Date, Voucher Count, Status

If you imported wrong XML in Tally and deleted those vouchers from Tally, you may also remove that matching line from this CSV manually.
Otherwise keep this file as it is.
"""

FUEL_SHEETS = {
    "HSD SALES": {
        "voucher_type": "DIESELSALE",
        "sales_ledger": "HSD Sales",
        "stock_item": "HSD (Diesel)",
        "qty_header": "HSD Qty",
        "unit": "LTR",
    },
    "MS SALES": {
        "voucher_type": "MS SALES",
        "sales_ledger": "MS Sales",
        "stock_item": "MS (Petrol)",
        "qty_header": "MS Qty",
        "unit": "LTR",
    },
    "CNG SALES": {
        "voucher_type": "Cng Sales",
        "sales_ledger": "Cng Sales A/c",
        "stock_item": "CNG",
        "qty_header": "CNG Qty",
        "unit": "Kgs",
    },
}

LUBE_SHEET = "LUBE SALES"
LUBE_VOUCHER_TYPE = "Lube Sales"
LUBE_SALES_LEDGER = "Lube Sales"
DEF_SALES_LEDGER = "DEF Sales A/c"
OUTPUT_CGST_LEDGER = "Output CGST"
OUTPUT_OGST_LEDGER = "Output OGST"
ROUNDOFF_LEDGER = "Roff"
LUBE_GST_PERCENT = Decimal("18")
LUBE_UNIT_DEFAULT = "Pcs"


def is_def_item(item_name: str) -> bool:
    """True when the lube item is a DEF (Diesel Exhaust Fluid) item, e.g. 'HP DEF 1x20L'."""
    tokens = re.split(r"[^A-Za-z]+", (item_name or "").upper())
    return "DEF" in tokens

# Party details fixed for every sales/lube invoice voucher created by this converter.
# Change these values only if the shop's state/country/place of supply changes.
PARTY_STATE_NAME = "Odisha"
PARTY_COUNTRY_NAME = "India"
PARTY_PLACE_OF_SUPPLY = "Odisha"
PARTY_GST_REGISTRATION_TYPE = "Unregistered/Consumer"

PAYMENT_SHEET_NAMES = ["PAYMENT", "Payment"]
RECEIPT_SHEET_NAMES = ["RECEIPT", "Receive", "RECEIVE"]

METER_READING_SHEET = "METER READING"
METER_MASTER_SHEET = "METER MASTER"
METER_VOUCHER_TYPE = "METER READING"

# Meter Reading tags are matched from exported Tally XML meter.xml.

PROCESS_ORDER = ["HSD SALES", "MS SALES", "CNG SALES", "LUBE SALES", "METER READING", "PAYMENT", "RECEIPT"]

# -----------------------------
# BASIC HELPERS
# -----------------------------
def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def x(value: Any) -> str:
    return escape(clean(value))


def norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(value).lower())


def D(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).replace(",", "").strip()
    if not text:
        return Decimal("0")
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal("0")


def q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money(value: Any) -> str:
    return f"{q2(D(value)):.2f}"


def qty_text(value: Any) -> str:
    d = D(value)
    if d == d.to_integral():
        return str(int(d))
    return format(d.normalize(), "f")

# -----------------------------
# DATE HELPERS
# -----------------------------
def parse_date(value: Any) -> date:
    """
    Flexible Indian date parser for Excel + user input.

    Accepts all these as the same date:
    - 01/07/2026
    - 1/7/2026
    - 01-07-2026
    - 1-7-2026
    - 01.07.2026
    - 2026-07-01
    - Excel real date values
    - Excel serial numbers such as 46204
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    # Excel may store dates as serial numbers if the cell is formatted as Date.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            from openpyxl.utils.datetime import from_excel
            return from_excel(value).date()
        except Exception:
            pass

    text = clean(value)
    if not text:
        raise ValueError("blank date")

    # Remove time part if Excel/text contains it, e.g. 01/07/2026 00:00:00
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    if " " in text:
        text = text.split(" ")[0]

    # Normalize separators: 01-07-2026, 01.07.2026 => 01/07/2026
    text = text.replace("-", "/").replace(".", "/")

    # Tally style YYYYMMDD
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date()

    # Prefer Indian date format D/M/Y. Python accepts both 01/07/2026 and 1/7/2026.
    for fmt in ("%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    raise ValueError(f"invalid date: {value!r}; use day/month/year, e.g. 9/7/2026 = 9 July 2026")


def tally_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def file_date(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def human_date(d: date) -> str:
    return f"{d.day}/{d.month}/{d.year:04d}"


def parse_input_date(value: str) -> date:
    """Interactive entry is always day/month/four-digit-year."""
    value = value.strip()
    if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
        raise ValueError("Use d/m/yyyy, for example 9/7/2026 = 9 July 2026")
    return datetime.strptime(value, "%d/%m/%Y").date()


def human_timestamp(value=None) -> str:
    value = value or datetime.now()
    return human_date(value.date()) + value.strftime(" %H:%M:%S")


def parse_date_for_selected_range(value: Any, start_date: date, end_date: date) -> date:
    """A date's identity NEVER depends on the selected export range.

    Text is day-first; native Excel dates retain their stored calendar date.
    The caller filters the result. Do not swap dates to fit a range.
    """
    return parse_date(value)

# -----------------------------
# EXCEL HELPERS
# -----------------------------
def find_sheet(wb, possible_names: List[str]) -> Optional[Any]:
    wanted = {n.strip().lower() for n in possible_names}
    for ws in wb.worksheets:
        if ws.title.strip().lower() in wanted:
            return ws
    return None


def find_header_map(ws, required_headers: List[str]) -> Tuple[int, Dict[str, int]]:
    required_norms = [norm_header(h) for h in required_headers]
    best_row, best_map, best_score = None, {}, -1

    # Search first 12 rows, because some sheets may have design blocks.
    for r in range(1, min(ws.max_row, 12) + 1):
        current = {}
        for c in range(1, ws.max_column + 1):
            h = norm_header(ws.cell(r, c).value)
            if h:
                current[h] = c
        score = sum(1 for h in required_norms if h in current)
        if score > best_score:
            best_row, best_map, best_score = r, current, score

    missing = [h for h in required_headers if norm_header(h) not in best_map]
    if missing:
        raise ValueError(f"header missing: {', '.join(missing)}")
    return int(best_row), best_map


def get_cell(ws, row: int, header_map: Dict[str, int], header_name: str) -> Any:
    col = header_map.get(norm_header(header_name))
    if not col:
        return None
    return ws.cell(row, col).value


def load_ledger_master(wb) -> Tuple[set, List[str]]:
    ws = find_sheet(wb, ["LEDGER MASTER"])
    if not ws:
        return set(), ["LEDGER MASTER sheet not found. Create it with exact Tally ledger names in column A."]

    ledgers = set()
    for r in range(2, ws.max_row + 1):
        name = clean(ws.cell(r, 1).value)
        if name:
            ledgers.add(name)

    errors = []
    if not ledgers:
        errors.append("LEDGER MASTER is blank. Put exact Tally ledger names in column A.")
    return ledgers, errors


def load_item_master(wb) -> Tuple[set, Dict[str, str], Dict[str, Decimal], List[str]]:
    ws = find_sheet(wb, ["ITEM MASTER"])
    if not ws:
        return set(), {}, {}, ["ITEM MASTER sheet not found. Create it with item names in column A and fuel items in column D."]

    items = set()
    units: Dict[str, str] = {}
    gst: Dict[str, Decimal] = {}

    # Lube list columns A:C: Item Name | Unit | GST %
    for r in range(2, ws.max_row + 1):
        item = clean(ws.cell(r, 1).value)
        if item:
            items.add(item)
            units[item] = clean(ws.cell(r, 2).value) or LUBE_UNIT_DEFAULT
            gst[item] = D(ws.cell(r, 3).value or LUBE_GST_PERCENT)

    # Fuel list columns D:E: Fuel Item Name | Unit
    for r in range(2, ws.max_row + 1):
        item = clean(ws.cell(r, 4).value)
        if item:
            items.add(item)
            units[item] = clean(ws.cell(r, 5).value) or ""
            gst[item] = Decimal("0")

    errors = []
    if not items:
        errors.append("ITEM MASTER is blank. Put exact Tally stock item names.")
    return items, units, gst, errors


def load_meter_master(wb) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Reads METER MASTER sheet.

    Expected columns:
    Pump Meter Name | MPD Name | Product | Unit | Rate

    For MPD4 CNG, use two rows:
    07 NOZZLE CNG 1 DU4 | MPD4 | CNG | Kgs | 95.42
    08 NOZZLE CNG 2 DU4 | MPD4 | CNG | Kgs | 95.42
    """
    ws = find_sheet(wb, [METER_MASTER_SHEET])
    if not ws:
        return {}, ["METER MASTER sheet not found. Create it with Pump Meter Name, MPD Name, Product, Unit, Rate."]

    try:
        header_row, hm = find_header_map(ws, ["Pump Meter Name", "MPD Name", "Product", "Unit", "Rate"])
    except Exception as e:
        return {}, [f"METER MASTER: {e}"]

    meter_master: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    for r in range(header_row + 1, ws.max_row + 1):
        meter = clean(get_cell(ws, r, hm, "Pump Meter Name"))
        if not meter:
            continue
        mpd = clean(get_cell(ws, r, hm, "MPD Name"))
        product = clean(get_cell(ws, r, hm, "Product"))
        unit = clean(get_cell(ws, r, hm, "Unit"))
        rate = D(get_cell(ws, r, hm, "Rate"))
        row_where = f"METER MASTER row {r}"
        if not mpd:
            errors.append(f"{row_where}: MPD Name is blank for '{meter}'")
        if not product:
            errors.append(f"{row_where}: Product is blank for '{meter}'")
        if not unit:
            errors.append(f"{row_where}: Unit is blank for '{meter}'")
        if rate <= 0:
            errors.append(f"{row_where}: Rate must be greater than zero for '{meter}'")
        meter_master[meter] = {"mpd": mpd, "product": product, "unit": unit, "rate": rate}

    if not meter_master:
        errors.append("METER MASTER is blank. Add pump meter rows.")
    return meter_master, errors


def validate_ledger(name: str, ledgers: set, errors: List[str], where: str):
    if not name:
        errors.append(f"{where}: blank ledger name")
    elif ledgers and name not in ledgers:
        errors.append(f"{where}: ledger not found in LEDGER MASTER -> '{name}'")


def validate_item(name: str, items: set, errors: List[str], where: str):
    if not name:
        errors.append(f"{where}: blank item name")
    elif items and name not in items:
        errors.append(f"{where}: item not found in ITEM MASTER -> '{name}'")

# -----------------------------
# XML HELPERS
# -----------------------------
def ledger_entry(ledger: str, amount: Decimal, is_deemed_positive: Optional[bool] = None) -> str:
    if is_deemed_positive is None:
        is_deemed_positive = amount < 0
    return f"""
    <LEDGERENTRIES.LIST>
      <LEDGERNAME>{x(ledger)}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>{'Yes' if is_deemed_positive else 'No'}</ISDEEMEDPOSITIVE>
      <AMOUNT>{money(amount)}</AMOUNT>
    </LEDGERENTRIES.LIST>"""


def sales_inventory_voucher(vch_date: date, party: str, qty: Decimal, rate: Decimal, amount: Decimal, cfg: Dict[str, str], narration: str) -> str:
    vtype = cfg["voucher_type"]
    sales_ledger = cfg["sales_ledger"]
    item = cfg["stock_item"]
    unit = cfg["unit"]
    qty_s = qty_text(qty)

    return f"""
<TALLYMESSAGE xmlns:UDF="TallyUDF">
  <VOUCHER VCHTYPE="{x(vtype)}" ACTION="Create" OBJVIEW="Invoice Voucher View">
    <DATE>{tally_date(vch_date)}</DATE>
    <EFFECTIVEDATE>{tally_date(vch_date)}</EFFECTIVEDATE>
    <VOUCHERTYPENAME>{x(vtype)}</VOUCHERTYPENAME>
    <PARTYLEDGERNAME>{x(party)}</PARTYLEDGERNAME>
    <BASICBASEPARTYNAME>{x(party)}</BASICBASEPARTYNAME>
    <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
    <VCHENTRYMODE>Item Invoice</VCHENTRYMODE>
    <ISINVOICE>Yes</ISINVOICE>
    <NARRATION>{x(narration)}</NARRATION>
    <COUNTRYOFRESIDENCE>{x(PARTY_COUNTRY_NAME)}</COUNTRYOFRESIDENCE>
    <STATENAME>{x(PARTY_STATE_NAME)}</STATENAME>
    <PARTYGSTIN></PARTYGSTIN>
    <GSTREGISTRATIONTYPE>{x(PARTY_GST_REGISTRATION_TYPE)}</GSTREGISTRATIONTYPE>
    <PLACEOFSUPPLY>{x(PARTY_PLACE_OF_SUPPLY)}</PLACEOFSUPPLY>
    <CONSIGNEESTATENAME>{x(PARTY_STATE_NAME)}</CONSIGNEESTATENAME>
    <CONSIGNEECOUNTRYNAME>{x(PARTY_COUNTRY_NAME)}</CONSIGNEECOUNTRYNAME>
    <CONSIGNEEMAILINGNAME.LIST>
      <CONSIGNEEMAILINGNAME>{x(party)}</CONSIGNEEMAILINGNAME>
    </CONSIGNEEMAILINGNAME.LIST>

    <LEDGERENTRIES.LIST>
      <LEDGERNAME>{x(party)}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
      <AMOUNT>-{money(amount)}</AMOUNT>
    </LEDGERENTRIES.LIST>

    <ALLINVENTORYENTRIES.LIST>
      <STOCKITEMNAME>{x(item)}</STOCKITEMNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <RATE>{money(rate)}/{x(unit)}</RATE>
      <AMOUNT>{money(amount)}</AMOUNT>
      <ACTUALQTY>{qty_s} {x(unit)}</ACTUALQTY>
      <BILLEDQTY>{qty_s} {x(unit)}</BILLEDQTY>

      <ACCOUNTINGALLOCATIONS.LIST>
        <LEDGERNAME>{x(sales_ledger)}</LEDGERNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <AMOUNT>{money(amount)}</AMOUNT>
      </ACCOUNTINGALLOCATIONS.LIST>
    </ALLINVENTORYENTRIES.LIST>
  </VOUCHER>
</TALLYMESSAGE>"""


def payment_or_receipt_voucher(vch_date: date, voucher_type: str, debit_ledger: str, credit_ledger: str, amount: Decimal, narration: str) -> str:
    return f"""
<TALLYMESSAGE xmlns:UDF="TallyUDF">
  <VOUCHER VCHTYPE="{x(voucher_type)}" ACTION="Create" OBJVIEW="Accounting Voucher View">
    <DATE>{tally_date(vch_date)}</DATE>
    <EFFECTIVEDATE>{tally_date(vch_date)}</EFFECTIVEDATE>
    <VOUCHERTYPENAME>{x(voucher_type)}</VOUCHERTYPENAME>
    <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
    <NARRATION>{x(narration)}</NARRATION>

    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>{x(debit_ledger)}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
      <AMOUNT>-{money(amount)}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>

    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>{x(credit_ledger)}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <AMOUNT>{money(amount)}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>
  </VOUCHER>
</TALLYMESSAGE>"""


def lube_voucher(vch_date: date, party: str, rows: List[Dict[str, Any]], item_units: Dict[str, str], sales_ledger: str = LUBE_SALES_LEDGER) -> Tuple[str, Dict[str, Decimal]]:
    inventory_xml: List[str] = []
    taxable_total = Decimal("0")
    cgst_total = Decimal("0")
    ogst_total = Decimal("0")
    invoice_total = Decimal("0")

    for row in rows:
        item = row["item"]
        qty = D(row["qty"])
        rate_incl = D(row["rate_incl"])
        line_total = D(row["amount"]) or q2(qty * rate_incl)
        gst_percent = D(row.get("gst_percent") or LUBE_GST_PERCENT)
        unit = item_units.get(item) or LUBE_UNIT_DEFAULT

        taxable_rate = q2(rate_incl / (Decimal("1") + gst_percent / Decimal("100")))
        taxable_amount = q2(taxable_rate * qty)
        cgst = q2(taxable_amount * Decimal("0.09"))
        ogst = q2(taxable_amount * Decimal("0.09"))

        taxable_total += taxable_amount
        cgst_total += cgst
        ogst_total += ogst
        invoice_total += line_total

        inventory_xml.append(f"""
    <ALLINVENTORYENTRIES.LIST>
      <STOCKITEMNAME>{x(item)}</STOCKITEMNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <RATE>{money(taxable_rate)}/{x(unit)}</RATE>
      <AMOUNT>{money(taxable_amount)}</AMOUNT>
      <ACTUALQTY>{qty_text(qty)} {x(unit)}</ACTUALQTY>
      <BILLEDQTY>{qty_text(qty)} {x(unit)}</BILLEDQTY>

      <ACCOUNTINGALLOCATIONS.LIST>
        <LEDGERNAME>{x(sales_ledger)}</LEDGERNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <AMOUNT>{money(taxable_amount)}</AMOUNT>
      </ACCOUNTINGALLOCATIONS.LIST>
    </ALLINVENTORYENTRIES.LIST>""")

    taxable_total = q2(taxable_total)
    cgst_total = q2(cgst_total)
    ogst_total = q2(ogst_total)
    invoice_total = q2(invoice_total)
    roundoff = q2(invoice_total - taxable_total - cgst_total - ogst_total)

    roundoff_xml = ""
    if abs(roundoff) >= Decimal("0.01"):
        # Positive roundoff is credit. Negative roundoff is debit.
        roundoff_xml = ledger_entry(ROUNDOFF_LEDGER, roundoff, roundoff < 0)

    xml_text = f"""
<TALLYMESSAGE xmlns:UDF="TallyUDF">
  <VOUCHER VCHTYPE="{x(LUBE_VOUCHER_TYPE)}" ACTION="Create" OBJVIEW="Invoice Voucher View">
    <DATE>{tally_date(vch_date)}</DATE>
    <EFFECTIVEDATE>{tally_date(vch_date)}</EFFECTIVEDATE>
    <VOUCHERTYPENAME>{x(LUBE_VOUCHER_TYPE)}</VOUCHERTYPENAME>
    <PARTYLEDGERNAME>{x(party)}</PARTYLEDGERNAME>
    <BASICBASEPARTYNAME>{x(party)}</BASICBASEPARTYNAME>
    <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
    <VCHENTRYMODE>Item Invoice</VCHENTRYMODE>
    <ISINVOICE>Yes</ISINVOICE>
    <NARRATION>Imported Lube Sales from Excel</NARRATION>
    <COUNTRYOFRESIDENCE>{x(PARTY_COUNTRY_NAME)}</COUNTRYOFRESIDENCE>
    <STATENAME>{x(PARTY_STATE_NAME)}</STATENAME>
    <PARTYGSTIN></PARTYGSTIN>
    <GSTREGISTRATIONTYPE>{x(PARTY_GST_REGISTRATION_TYPE)}</GSTREGISTRATIONTYPE>
    <PLACEOFSUPPLY>{x(PARTY_PLACE_OF_SUPPLY)}</PLACEOFSUPPLY>
    <CONSIGNEESTATENAME>{x(PARTY_STATE_NAME)}</CONSIGNEESTATENAME>
    <CONSIGNEECOUNTRYNAME>{x(PARTY_COUNTRY_NAME)}</CONSIGNEECOUNTRYNAME>
    <CONSIGNEEMAILINGNAME.LIST>
      <CONSIGNEEMAILINGNAME>{x(party)}</CONSIGNEEMAILINGNAME>
    </CONSIGNEEMAILINGNAME.LIST>

    <LEDGERENTRIES.LIST>
      <LEDGERNAME>{x(party)}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
      <AMOUNT>-{money(invoice_total)}</AMOUNT>
    </LEDGERENTRIES.LIST>

    {''.join(inventory_xml)}

    {ledger_entry(OUTPUT_CGST_LEDGER, cgst_total, False)}
    {ledger_entry(OUTPUT_OGST_LEDGER, ogst_total, False)}
    {roundoff_xml}
  </VOUCHER>
</TALLYMESSAGE>"""

    stats = {
        "taxable": taxable_total,
        "cgst": cgst_total,
        "ogst": ogst_total,
        "roundoff": roundoff,
        "invoice_total": invoice_total,
    }
    return xml_text, stats


def meter_udf_number_list(tag: str, desc: str, index: int, value: Any) -> str:
    """Tally UDF list format used by exported METER READING voucher."""
    return f"""
       <UDF:{tag}.LIST DESC="`{desc}`" ISLIST="YES" TYPE="Number" INDEX="{index}">
        <UDF:{tag} DESC="`{desc}`"> {qty_text(value)}</UDF:{tag}>
       </UDF:{tag}.LIST>"""


def meter_udf_amount_list(tag: str, desc: str, index: int, value: Any) -> str:
    return f"""
       <UDF:{tag}.LIST DESC="`{desc}`" ISLIST="YES" TYPE="Amount" INDEX="{index}">
        <UDF:{tag} DESC="`{desc}`">{money(value)}</UDF:{tag}>
       </UDF:{tag}.LIST>"""


def meter_reading_voucher(vch_date: date, mpd_name: str, rows: List[Dict[str, Any]]) -> str:
    """Creates one METER READING voucher per Date + MPD.

    Matched from exported Tally XML:
    - Row entries are INVENTORYENTRIESOUT.LIST
    - Opening is ACTUALQTY/BILLEDQTY
    - Closing/Testing/Net Sale/Net Qty/Amount are TDL UDF list fields
    - MPD name is voucher-level UDF METERMPDNAME
    """
    inventory_xml: List[str] = []

    for row in rows:
        meter = row["pump_meter"]
        opening = D(row["opening"])
        closing = D(row["closing"])
        testing = D(row["testing"])
        net_sale = D(row["net_sale"])
        unit = clean(row["unit"])
        rate = D(row["rate"])
        amount = D(row["amount"])
        net_qty = q2(closing - opening)  # Tally sample: VCHNetQty = Closing - Opening

        inventory_xml.append(f"""
      <INVENTORYENTRIESOUT.LIST>
       <STOCKITEMNAME>{x(meter)}</STOCKITEMNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <ISGSTASSESSABLEVALUEOVERRIDDEN>No</ISGSTASSESSABLEVALUEOVERRIDDEN>
       <STRDISGSTAPPLICABLE>No</STRDISGSTAPPLICABLE>
       <CONTENTNEGISPOS>No</CONTENTNEGISPOS>
       <ISLASTDEEMEDPOSITIVE>No</ISLASTDEEMEDPOSITIVE>
       <ISAUTONEGATE>No</ISAUTONEGATE>
       <ISCUSTOMSCLEARANCE>No</ISCUSTOMSCLEARANCE>
       <ISTRACKCOMPONENT>No</ISTRACKCOMPONENT>
       <ISTRACKPRODUCTION>No</ISTRACKPRODUCTION>
       <ISPRIMARYITEM>No</ISPRIMARYITEM>
       <ISSCRAP>No</ISSCRAP>
       <RATE>{money(rate)}/{x(unit)}</RATE>
       <ACTUALQTY> {qty_text(opening)} {x(unit)}</ACTUALQTY>
       <BILLEDQTY> {qty_text(opening)} {x(unit)}</BILLEDQTY>
       <BATCHALLOCATIONS.LIST>
        <GODOWNNAME>Main Location</GODOWNNAME>
        <BATCHNAME>Primary Batch</BATCHNAME>
        <INDENTNO>&#4; Not Applicable</INDENTNO>
        <ORDERNO>&#4; Not Applicable</ORDERNO>
        <TRACKINGNUMBER>&#4; Not Applicable</TRACKINGNUMBER>
        <DYNAMICCSTISCLEARED>No</DYNAMICCSTISCLEARED>
        <ACTUALQTY> {qty_text(opening)} {x(unit)}</ACTUALQTY>
        <BILLEDQTY> {qty_text(opening)} {x(unit)}</BILLEDQTY>
        <ADDITIONALDETAILS.LIST>        </ADDITIONALDETAILS.LIST>
        <VOUCHERCOMPONENTLIST.LIST>        </VOUCHERCOMPONENTLIST.LIST>
       </BATCHALLOCATIONS.LIST>
       <DUTYHEADDETAILS.LIST>       </DUTYHEADDETAILS.LIST>
       <RATEDETAILS.LIST>       </RATEDETAILS.LIST>
       <SUPPLEMENTARYDUTYHEADDETAILS.LIST>       </SUPPLEMENTARYDUTYHEADDETAILS.LIST>
       <TAXOBJECTALLOCATIONS.LIST>       </TAXOBJECTALLOCATIONS.LIST>
       <COSTTRACKALLOCATIONS.LIST>       </COSTTRACKALLOCATIONS.LIST>
       <REFVOUCHERDETAILS.LIST>       </REFVOUCHERDETAILS.LIST>
       <EXCISEALLOCATIONS.LIST>       </EXCISEALLOCATIONS.LIST>
       <EXPENSEALLOCATIONS.LIST>       </EXPENSEALLOCATIONS.LIST>{meter_udf_number_list("VCHCLOSINGQTY", "VCHClosingQty", 4100, closing)}{meter_udf_number_list("VCHTESTQTY", "VCHTestQty", 4101, testing)}{meter_udf_number_list("VCHNETSALEQTY", "VCHNetSaleQty", 4102, net_sale)}{meter_udf_number_list("VCHNETQTY", "VCHNetQty", 4106, net_qty)}{meter_udf_amount_list("MRVALUE", "MRValue", 4110, amount)}
      </INVENTORYENTRIESOUT.LIST>""")

    return f"""
<TALLYMESSAGE xmlns:UDF="TallyUDF">
  <VOUCHER VCHTYPE="{x(METER_VOUCHER_TYPE)}" ACTION="Create" OBJVIEW="Consumption Voucher View">
    <DATE>{tally_date(vch_date)}</DATE>
    <VCHSTATUSDATE>{tally_date(vch_date)}</VCHSTATUSDATE>
    <VOUCHERTYPENAME>{x(METER_VOUCHER_TYPE)}</VOUCHERTYPENAME>
    <NUMBERINGSTYLE>Auto Renumber</NUMBERINGSTYLE>
    <PERSISTEDVIEW>Consumption Voucher View</PERSISTEDVIEW>
    <VCHSTATUSVOUCHERTYPE>{x(METER_VOUCHER_TYPE)}</VCHSTATUSVOUCHERTYPE>
    <VCHGSTCLASS>&#4; Not Applicable</VCHGSTCLASS>
    <VCHENTRYMODE>Use for Stock Journal</VCHENTRYMODE>
    <DESTINATIONGODOWN>Main Location</DESTINATIONGODOWN>
    <EFFECTIVEDATE>{tally_date(vch_date)}</EFFECTIVEDATE>
    <ISOPTIONAL>No</ISOPTIONAL>
    <NARRATION>Imported Meter Reading from Excel</NARRATION>
    <INVENTORYENTRIESIN.LIST>    </INVENTORYENTRIESIN.LIST>
    {''.join(inventory_xml)}
    <GST.LIST>      </GST.LIST>
    <STKJRNLADDLCOSTDETAILS.LIST>      </STKJRNLADDLCOSTDETAILS.LIST>
    <UDF:METERMPDNAME.LIST DESC="`MeterMPDName`" ISLIST="YES" TYPE="String" INDEX="3882">
      <UDF:METERMPDNAME DESC="`MeterMPDName`">{x(mpd_name)}</UDF:METERMPDNAME>
    </UDF:METERMPDNAME.LIST>
  </VOUCHER>
</TALLYMESSAGE>"""

def envelope(vouchers: List[str]) -> str:
    return f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>{x(COMPANY_NAME)}</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        {''.join(vouchers)}
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

# -----------------------------
# PROCESSORS
# -----------------------------
def process_fuel_sheet(ws, cfg, start_date: date, end_date: date, ledgers: set, items: set, errors: List[str], counts: Dict[str, int], vouchers: List[str]):
    header_row, hm = find_header_map(ws, ["Date", "Party Name", cfg["qty_header"], "Rate", "Amount"])

    for r in range(header_row + 1, ws.max_row + 1):
        raw_date = get_cell(ws, r, hm, "Date")
        if not raw_date:
            continue
        row_errors: List[str] = []
        try:
            vdate = parse_date_for_selected_range(raw_date, start_date, end_date)
        except Exception as e:
            errors.append(f"{ws.title} row {r}: {e}")
            continue
        if not (start_date <= vdate <= end_date):
            continue

        party = clean(get_cell(ws, r, hm, "Party Name"))
        qty = D(get_cell(ws, r, hm, cfg["qty_header"]))
        rate = D(get_cell(ws, r, hm, "Rate"))
        amount = D(get_cell(ws, r, hm, "Amount"))
        if amount == 0 and qty and rate:
            amount = q2(qty * rate)

        where = f"{ws.title} row {r}"
        validate_ledger(party, ledgers, row_errors, where + " Party Name")
        validate_ledger(cfg["sales_ledger"], ledgers, row_errors, where + f" sales ledger '{cfg['sales_ledger']}'")
        validate_item(cfg["stock_item"], items, row_errors, where + f" item '{cfg['stock_item']}'")
        if qty <= 0:
            row_errors.append(f"{where}: qty must be greater than zero")
        if rate <= 0:
            row_errors.append(f"{where}: rate must be greater than zero")
        if amount <= 0:
            row_errors.append(f"{where}: amount must be greater than zero")

        if row_errors:
            errors.extend(row_errors)
        else:
            vouchers.append(sales_inventory_voucher(vdate, party, qty, rate, amount, cfg, f"Imported {ws.title} from Excel"))
            counts[ws.title] += 1


def process_lube_sheet(ws, start_date: date, end_date: date, ledgers: set, items: set, item_units: Dict[str, str], item_gst: Dict[str, Decimal], errors: List[str], counts: Dict[str, int], vouchers: List[str]):
    header_row, hm = find_header_map(ws, ["Date", "Party Name", "Item Name", "Qty", "Rate Incl Tax", "Amount"])
    groups: Dict[Tuple[date, str, str], List[Dict[str, Any]]] = defaultdict(list)

    for r in range(header_row + 1, ws.max_row + 1):
        raw_date = get_cell(ws, r, hm, "Date")
        if not raw_date:
            continue
        row_errors: List[str] = []
        try:
            vdate = parse_date_for_selected_range(raw_date, start_date, end_date)
        except Exception as e:
            errors.append(f"{ws.title} row {r}: {e}")
            continue
        if not (start_date <= vdate <= end_date):
            continue

        party = clean(get_cell(ws, r, hm, "Party Name"))
        item = clean(get_cell(ws, r, hm, "Item Name"))
        qty = D(get_cell(ws, r, hm, "Qty"))
        rate_incl = D(get_cell(ws, r, hm, "Rate Incl Tax"))
        amount = D(get_cell(ws, r, hm, "Amount"))
        if amount == 0 and qty and rate_incl:
            amount = q2(qty * rate_incl)

        where = f"{ws.title} row {r}"
        validate_ledger(party, ledgers, row_errors, where + " Party Name")
        item_sales_ledger = DEF_SALES_LEDGER if is_def_item(item) else LUBE_SALES_LEDGER
        validate_ledger(item_sales_ledger, ledgers, row_errors, where + f" sales ledger '{item_sales_ledger}'")
        validate_ledger(OUTPUT_CGST_LEDGER, ledgers, row_errors, where + f" tax ledger '{OUTPUT_CGST_LEDGER}'")
        validate_ledger(OUTPUT_OGST_LEDGER, ledgers, row_errors, where + f" tax ledger '{OUTPUT_OGST_LEDGER}'")
        validate_ledger(ROUNDOFF_LEDGER, ledgers, row_errors, where + f" roundoff ledger '{ROUNDOFF_LEDGER}'")
        validate_item(item, items, row_errors, where + " Item Name")
        if qty <= 0:
            row_errors.append(f"{where}: qty must be greater than zero")
        if rate_incl <= 0:
            row_errors.append(f"{where}: Rate Incl Tax must be greater than zero")
        if amount <= 0:
            row_errors.append(f"{where}: amount must be greater than zero")

        if row_errors:
            errors.extend(row_errors)
        else:
            groups[(vdate, party, item_sales_ledger)].append({
                "item": item,
                "qty": qty,
                "rate_incl": rate_incl,
                "amount": amount,
                "gst_percent": item_gst.get(item, LUBE_GST_PERCENT),
            })

    for (vdate, party, sales_ledger), rows in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
        xml_text, _stats = lube_voucher(vdate, party, rows, item_units, sales_ledger=sales_ledger)
        vouchers.append(xml_text)
        counts[ws.title] += 1
        counts[ws.title + " item rows"] += len(rows)


def process_payment_sheet(ws, start_date: date, end_date: date, ledgers: set, errors: List[str], counts: Dict[str, int], vouchers: List[str]):
    header_row, hm = find_header_map(ws, ["Date", "Paid From Ledger", "Paid To Ledger", "Amount"])

    for r in range(header_row + 1, ws.max_row + 1):
        raw_date = get_cell(ws, r, hm, "Date")
        if not raw_date:
            continue
        row_errors: List[str] = []
        try:
            vdate = parse_date_for_selected_range(raw_date, start_date, end_date)
        except Exception as e:
            errors.append(f"{ws.title} row {r}: {e}")
            continue
        if not (start_date <= vdate <= end_date):
            continue

        paid_from = clean(get_cell(ws, r, hm, "Paid From Ledger"))
        paid_to = clean(get_cell(ws, r, hm, "Paid To Ledger"))
        amount = D(get_cell(ws, r, hm, "Amount"))
        narration = clean(get_cell(ws, r, hm, "Narration")) or "Imported Payment from Excel"

        where = f"{ws.title} row {r}"
        validate_ledger(paid_from, ledgers, row_errors, where + " Paid From Ledger")
        validate_ledger(paid_to, ledgers, row_errors, where + " Paid To Ledger")
        if amount <= 0:
            row_errors.append(f"{where}: amount must be greater than zero")

        if row_errors:
            errors.extend(row_errors)
        else:
            # Payment: Paid To Dr, Paid From Cr
            vouchers.append(payment_or_receipt_voucher(vdate, "Payment", paid_to, paid_from, amount, narration))
            counts[ws.title] += 1


def process_receipt_sheet(ws, start_date: date, end_date: date, ledgers: set, errors: List[str], counts: Dict[str, int], vouchers: List[str]):
    header_row, hm = find_header_map(ws, ["Date", "Received In Ledger", "Received From Ledger", "Amount"])

    for r in range(header_row + 1, ws.max_row + 1):
        raw_date = get_cell(ws, r, hm, "Date")
        if not raw_date:
            continue
        row_errors: List[str] = []
        try:
            vdate = parse_date_for_selected_range(raw_date, start_date, end_date)
        except Exception as e:
            errors.append(f"{ws.title} row {r}: {e}")
            continue
        if not (start_date <= vdate <= end_date):
            continue

        received_in = clean(get_cell(ws, r, hm, "Received In Ledger"))
        received_from = clean(get_cell(ws, r, hm, "Received From Ledger"))
        amount = D(get_cell(ws, r, hm, "Amount"))
        narration = clean(get_cell(ws, r, hm, "Narration")) or "Imported Receipt from Excel"

        where = f"{ws.title} row {r}"
        validate_ledger(received_in, ledgers, row_errors, where + " Received In Ledger")
        validate_ledger(received_from, ledgers, row_errors, where + " Received From Ledger")
        if amount <= 0:
            row_errors.append(f"{where}: amount must be greater than zero")

        if row_errors:
            errors.extend(row_errors)
        else:
            # Receipt: Received In Dr, Received From Cr
            vouchers.append(payment_or_receipt_voucher(vdate, "Receipt", received_in, received_from, amount, narration))
            counts[ws.title] += 1

def build_meter_previous_closing_map(ws, hm: Dict[str, int], header_row: int) -> Dict[Tuple[str, date, int], Decimal]:
    """Returns previous closing for each meter before each row.

    Key: (meter, date, row_no) -> previous closing for that same meter from an earlier row/date.
    This allows the converter to use yesterday closing as today's opening when Opening is blank/zero.
    """
    records: List[Tuple[date, int, str, Decimal]] = []
    for r in range(header_row + 1, ws.max_row + 1):
        raw_date = get_cell(ws, r, hm, "Date")
        meter = clean(get_cell(ws, r, hm, "Pump Meter"))
        closing = D(get_cell(ws, r, hm, "Closing"))
        if not raw_date or not meter or closing <= 0:
            continue
        try:
            d = parse_date(raw_date)
        except Exception:
            continue
        records.append((d, r, meter, closing))
    records.sort(key=lambda t: (t[0], t[1]))
    latest: Dict[str, Decimal] = {}
    prev_map: Dict[Tuple[str, date, int], Decimal] = {}
    for d, r, meter, closing in records:
        if meter in latest:
            prev_map[(meter, d, r)] = latest[meter]
        latest[meter] = closing
    return prev_map


def process_meter_reading_sheet(ws, start_date: date, end_date: date, meter_master: Dict[str, Dict[str, Any]], errors: List[str], counts: Dict[str, int], vouchers: List[str], meter_opening_notes: List[str], meter_opening_warnings: List[str]):
    header_row, hm = find_header_map(ws, ["Date", "Pump Meter", "Opening", "Closing", "Testing", "Net Sale", "Rate", "Amount"])
    groups: Dict[Tuple[date, str], List[Dict[str, Any]]] = defaultdict(list)
    previous_closing_map = build_meter_previous_closing_map(ws, hm, header_row)

    for r in range(header_row + 1, ws.max_row + 1):
        raw_date = get_cell(ws, r, hm, "Date")
        if not raw_date:
            continue
        row_errors: List[str] = []
        try:
            vdate = parse_date_for_selected_range(raw_date, start_date, end_date)
        except Exception as e:
            errors.append(f"{ws.title} row {r}: {e}")
            continue
        if not (start_date <= vdate <= end_date):
            continue

        meter = clean(get_cell(ws, r, hm, "Pump Meter"))
        master = meter_master.get(meter, {}) if meter else {}
        mpd_name = clean(get_cell(ws, r, hm, "MPD Name")) or clean(master.get("mpd"))
        product = clean(get_cell(ws, r, hm, "Product")) or clean(master.get("product"))
        unit = clean(get_cell(ws, r, hm, "Unit")) or clean(master.get("unit"))
        opening_raw = get_cell(ws, r, hm, "Opening")
        opening = D(opening_raw)
        closing = D(get_cell(ws, r, hm, "Closing"))
        testing = D(get_cell(ws, r, hm, "Testing"))
        rate = D(get_cell(ws, r, hm, "Rate")) or D(master.get("rate"))

        prev_closing = previous_closing_map.get((meter, vdate, r)) if meter else None
        if prev_closing is not None:
            # If opening is blank or zero, use previous closing automatically.
            if opening_raw in (None, "") or opening == 0:
                opening = prev_closing
                meter_opening_notes.append(f"{ws.title} row {r} {human_date(vdate)} {meter}: opening auto-used from previous closing {qty_text(prev_closing)}")
            elif abs(opening - prev_closing) > Decimal("0.01"):
                meter_opening_warnings.append(f"{ws.title} row {r} {human_date(vdate)} {meter}: opening {qty_text(opening)} does not match previous closing {qty_text(prev_closing)}")

        net_sale = D(get_cell(ws, r, hm, "Net Sale"))
        if net_sale == 0 and closing and opening:
            net_sale = q2(closing - opening - testing)
        amount = D(get_cell(ws, r, hm, "Amount"))
        if amount == 0 and net_sale and rate:
            amount = q2(net_sale * rate)

        where = f"{ws.title} row {r}"
        if not meter:
            row_errors.append(f"{where}: Pump Meter is blank")
        elif meter not in meter_master:
            row_errors.append(f"{where}: Pump Meter not found in METER MASTER -> '{meter}'")
        if not mpd_name:
            row_errors.append(f"{where}: MPD Name is blank")
        if not product:
            row_errors.append(f"{where}: Product is blank")
        if not unit:
            row_errors.append(f"{where}: Unit is blank")
        if closing <= 0:
            row_errors.append(f"{where}: Closing must be greater than zero")
        if rate <= 0:
            row_errors.append(f"{where}: Rate must be greater than zero")
        if net_sale < 0:
            row_errors.append(f"{where}: Net Sale is negative. Check Opening/Closing/Testing.")
        if amount < 0:
            row_errors.append(f"{where}: Amount is negative. Check readings/rate.")

        if row_errors:
            errors.extend(row_errors)
        else:
            groups[(vdate, mpd_name)].append({
                "pump_meter": meter,
                "mpd_name": mpd_name,
                "product": product,
                "opening": opening,
                "closing": closing,
                "testing": testing,
                "net_sale": net_sale,
                "unit": unit,
                "rate": rate,
                "amount": amount,
            })

    for (vdate, mpd_name), rows in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        vouchers.append(meter_reading_voucher(vdate, mpd_name, rows))
        counts[ws.title] += 1
        counts[ws.title + " item rows"] += len(rows)



# -----------------------------
# DUPLICATE WARNING / IMPORT HISTORY
# -----------------------------
def date_ranges_overlap(start1: date, end1: date, start2: date, end2: date) -> bool:
    return start1 <= end2 and end1 >= start2


def history_date(value: str) -> Optional[date]:
    try:
        return parse_date(value)
    except Exception:
        return None


def read_import_history(history_path: str) -> List[Dict[str, str]]:
    if not os.path.exists(history_path):
        return []
    rows: List[Dict[str, str]] = []
    try:
        with open(history_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row:
                    normalized = {k: clean(v) for k, v in row.items()}
                    for field in ("From Date", "To Date"):
                        parsed = history_date(normalized.get(field, ""))
                        if parsed:
                            normalized[field] = human_date(parsed)
                    raw_time = normalized.get("Run Time", "")
                    if raw_time:
                        try:
                            day = parse_date(raw_time)
                            clock = raw_time.split(" ", 1)[1] if " " in raw_time else ""
                            normalized["Run Time"] = human_date(day) + (" " + clock if clock else "")
                        except ValueError:
                            pass
                    rows.append(normalized)
    except Exception:
        # Bad/corrupted history should not block XML creation.
        return []
    return rows


def duplicate_warnings_from_history(sheet_ranges: Dict[str, Tuple[date, date]], history_path: str) -> List[str]:
    history = read_import_history(history_path)
    warnings: List[str] = []
    if not history:
        return warnings

    for sheet_name, (start, end) in sheet_ranges.items():
        for old in history:
            old_sheet = clean(old.get("Sheet Name"))
            if old_sheet.upper() != sheet_name.upper():
                continue
            old_start = history_date(clean(old.get("From Date")))
            old_end = history_date(clean(old.get("To Date")))
            if not old_start or not old_end:
                continue
            if date_ranges_overlap(start, end, old_start, old_end):
                xml_name = clean(old.get("XML File")) or "previous XML"
                run_time = clean(old.get("Run Time")) or "previous run"
                status = clean(old.get("Status")) or "XML_CREATED"
                warnings.append(
                    f"{sheet_name}: selected {human_date(start)} to {human_date(end)} overlaps "
                    f"previous {human_date(old_start)} to {human_date(old_end)} | {xml_name} | {status} | {run_time}"
                )
    return warnings


def write_folder_readme(folder: str, filename: str, content: str) -> None:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def ensure_output_folder(month_date: Optional[date] = None) -> str:
    folder = OUTPUT_FOLDER
    if month_date:
        folder = os.path.join(OUTPUT_FOLDER, month_folder_name(month_date))
    os.makedirs(folder, exist_ok=True)
    write_folder_readme(folder, "README_XML_OUTPUT.txt", XML_FOLDER_README)
    return folder


def month_folder_name(d: date) -> str:
    """e.g. date(2026,7,17) -> 'JULY 2026' (matches the SFM_TALLY_IMPORT folder naming)."""
    return d.strftime("%B %Y").upper()


def earliest_range_date(sheet_ranges: Dict[str, Tuple[date, date]]) -> Optional[date]:
    if not sheet_ranges:
        return None
    return min(s for s, _e in sheet_ranges.values())


def ensure_csv_folder() -> str:
    os.makedirs(CSV_FOLDER, exist_ok=True)
    write_folder_readme(CSV_FOLDER, "README_CSV_OUTPUT.txt", CSV_FOLDER_README)
    return CSV_FOLDER


def default_history_path() -> str:
    return os.path.join(ensure_csv_folder(), IMPORT_HISTORY_FILE)


def out_path(filename: str, month_date: Optional[date] = None) -> str:
    return os.path.join(ensure_output_folder(month_date), filename)


def write_duplicate_warning_report(duplicate_warnings: List[str], sheet_ranges: Dict[str, Tuple[date, date]]) -> str:
    warning_file = out_path(f"SFM_Duplicate_Warning_{datetime.now().strftime('%d-%m-%Y_%H%M%S')}.txt", earliest_range_date(sheet_ranges))
    with open(warning_file, "w", encoding="utf-8") as f:
        f.write("SFM DUPLICATE WARNING REPORT\n")
        f.write("============================\n\n")
        f.write("Selected sheets/date ranges:\n")
        for name in PROCESS_ORDER:
            if name in sheet_ranges:
                s, e = sheet_ranges[name]
                f.write(f"- {name}: {human_date(s)} to {human_date(e)}\n")
        f.write("\nPossible duplicate/overlap found:\n")
        for warning in duplicate_warnings:
            f.write(f"- {warning}\n")
        f.write("\nMeaning:\n")
        f.write("This selected sheet/date range was already used earlier to create an XML.\n")
        f.write("If that XML was already imported in Tally, importing again can create duplicate vouchers.\n")
    return warning_file


def count_for_sheet(sheet_name: str, counts: Dict[str, int], available: Dict[str, Any]) -> int:
    if sheet_name in counts:
        return counts[sheet_name]
    ws = available.get(sheet_name)
    if ws is not None and ws.title in counts:
        return counts[ws.title]
    # Case-insensitive fallback.
    for key, val in counts.items():
        if key.upper() == sheet_name.upper():
            return val
    return 0


def append_import_history(history_path: str, xml_file: str, mode: str, sheet_ranges: Dict[str, Tuple[date, date]], counts: Dict[str, int], available: Dict[str, Any]) -> None:
    history_dir = os.path.dirname(history_path)
    if history_dir:
        os.makedirs(history_dir, exist_ok=True)
    fieldnames = ["Run Time", "XML File", "Date Mode", "Sheet Name", "From Date", "To Date", "Voucher Count", "Status"]
    exists = os.path.exists(history_path) and os.path.getsize(history_path) > 0
    with open(history_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        run_time = human_timestamp()
        for name in PROCESS_ORDER:
            if name not in sheet_ranges:
                continue
            voucher_count = count_for_sheet(name, counts, available)
            if voucher_count <= 0:
                continue
            start, end = sheet_ranges[name]
            writer.writerow({
                "Run Time": run_time,
                "XML File": xml_file,
                "Date Mode": "Common" if mode == "common" else "Sheet-wise",
                "Sheet Name": name,
                "From Date": human_date(start),
                "To Date": human_date(end),
                "Voucher Count": voucher_count,
                "Status": "XML_CREATED_CHECK_BEFORE_IMPORT",
            })

# -----------------------------
# INTERACTIVE INPUTS
# -----------------------------
def ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    ans = input(f"{prompt} {suffix}: ").strip().lower()
    if not ans:
        return default_yes
    return ans.startswith("y")


def ask_date(prompt: str) -> date:
    while True:
        text = input(prompt).strip()
        try:
            return parse_input_date(text)
        except Exception:
            print("Invalid date. Use d/m/yyyy, e.g. 9/7/2026 = 9 July 2026.")


def ask_date_range(label: str) -> Tuple[date, date]:
    while True:
        print(f"\n{label}")
        start = ask_date("From date (dd/mm/yyyy): ")
        end = ask_date("To date   (dd/mm/yyyy): ")
        if start <= end:
            return start, end
        print("From date cannot be after To date. Enter again.")


def build_available_sheets(wb) -> Dict[str, Any]:
    available: Dict[str, Any] = {}
    for sheet_name in FUEL_SHEETS:
        ws = find_sheet(wb, [sheet_name])
        if ws:
            available[sheet_name] = ws
    lube_ws = find_sheet(wb, [LUBE_SHEET])
    if lube_ws:
        available[LUBE_SHEET] = lube_ws
    meter_ws = find_sheet(wb, [METER_READING_SHEET])
    if meter_ws:
        available[METER_READING_SHEET] = meter_ws
    payment_ws = find_sheet(wb, PAYMENT_SHEET_NAMES)
    if payment_ws:
        available["PAYMENT"] = payment_ws
    receipt_ws = find_sheet(wb, RECEIPT_SHEET_NAMES)
    if receipt_ws:
        available["RECEIPT"] = receipt_ws
    return available


def get_sheet_date_ranges_interactive(available: Dict[str, Any]) -> Tuple[Dict[str, Tuple[date, date]], str]:
    print("\nAvailable sheets:")
    for name in PROCESS_ORDER:
        if name in available:
            print(f" - {name}")

    if ask_yes_no("Use ONE COMMON date range for all sheets?", True):
        start, end = ask_date_range("Common date range for all selected sheets")
        ranges: Dict[str, Tuple[date, date]] = {}
        if ask_yes_no("Convert ALL available sheets in this common date range?", True):
            for name in PROCESS_ORDER:
                if name in available:
                    ranges[name] = (start, end)
        else:
            for name in PROCESS_ORDER:
                if name in available and ask_yes_no(f"Include {name}?", True):
                    ranges[name] = (start, end)
        return ranges, "common"

    print("\nSheet-wise date selection")
    print("Enter date range separately for each sheet you want to convert.")
    ranges = {}
    for name in PROCESS_ORDER:
        if name not in available:
            continue
        if ask_yes_no(f"Include {name}?", True):
            ranges[name] = ask_date_range(f"Date range for {name}")
    return ranges, "sheetwise"


def parse_cli_sheets(text: str, available: Dict[str, Any]) -> List[str]:
    mode = text.strip().lower()
    if mode == "all":
        return [name for name in PROCESS_ORDER if name in available]
    if mode == "ask":
        return []
    result = []
    for part in text.split(","):
        name = part.strip().upper()
        # normalize common spellings
        if name == "RECEIVE":
            name = "RECEIPT"
        if name in available:
            result.append(name)
        else:
            print(f"Warning: sheet not found or unsupported: {part.strip()}")
    return result


# -----------------------------
# V9/V10 ADVANCED HELPERS
# -----------------------------
def print_preimport_summary(sheet_ranges: Dict[str, Tuple[date, date]], counts: Dict[str, int], available: Dict[str, Any], duplicate_warnings: List[str], meter_opening_notes: List[str], meter_opening_warnings: List[str]) -> int:
    print("\nPRE-IMPORT SUMMARY")
    print("==================")
    total = 0
    for name in PROCESS_ORDER:
        if name not in sheet_ranges:
            continue
        s, e = sheet_ranges[name]
        cnt = count_for_sheet(name, counts, available)
        total += cnt
        print(f"{name:<16} {human_date(s)} to {human_date(e)}   vouchers: {cnt}")
        if name == METER_READING_SHEET:
            item_rows = counts.get(METER_READING_SHEET + " item rows", 0)
            print(f"{'':<16} meter item rows: {item_rows}")
    print(f"\nTOTAL VOUCHERS: {total}")
    if duplicate_warnings:
        print("\nDuplicate/overlap warning found. Check carefully before importing.")
    if meter_opening_notes:
        print(f"\nAuto meter opening used in {len(meter_opening_notes)} row(s).")
    if meter_opening_warnings:
        print(f"\nMeter opening mismatch warning in {len(meter_opening_warnings)} row(s).")
    return total


def write_import_checklist(range_name: str, sheet_ranges: Dict[str, Tuple[date, date]], counts: Dict[str, int], available: Dict[str, Any], duplicate_warnings: List[str], meter_opening_notes: List[str], meter_opening_warnings: List[str]) -> str:
    checklist_file = out_path(f"SFM_Import_Checklist_{range_name}.txt", earliest_range_date(sheet_ranges))
    with open(checklist_file, "w", encoding="utf-8") as f:
        f.write("SFM PRE-IMPORT CHECKLIST\n")
        f.write("========================\n\n")
        f.write("Before importing in Tally:\n")
        f.write("1. Take Tally company backup.\n")
        f.write("2. Open the import report and confirm STATUS: SUCCESS.\n")
        f.write("3. Check voucher count below.\n")
        f.write("4. Import XML in Tally: O: Import -> Transactions/Vouchers.\n")
        f.write("5. After import, check Day Book date-wise.\n\n")
        f.write("Selected sheets:\n")
        total = 0
        for name in PROCESS_ORDER:
            if name in sheet_ranges:
                s, e = sheet_ranges[name]
                cnt = count_for_sheet(name, counts, available)
                total += cnt
                f.write(f"- {name}: {human_date(s)} to {human_date(e)} | vouchers: {cnt}\n")
                if name == METER_READING_SHEET:
                    f.write(f"  Meter item rows: {counts.get(METER_READING_SHEET + ' item rows', 0)}\n")
        f.write(f"\nTotal vouchers expected: {total}\n")
        if duplicate_warnings:
            f.write("\nDUPLICATE/OVERLAP WARNINGS:\n")
            for w in duplicate_warnings:
                f.write(f"- {w}\n")
        if meter_opening_notes:
            f.write("\nAUTO METER OPENING USED:\n")
            for n in meter_opening_notes:
                f.write(f"- {n}\n")
        if meter_opening_warnings:
            f.write("\nMETER OPENING MISMATCH WARNINGS:\n")
            for w in meter_opening_warnings:
                f.write(f"- {w}\n")
    return checklist_file


def open_folder(folder: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(os.path.abspath(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{os.path.abspath(folder)}"')
        else:
            os.system(f'xdg-open "{os.path.abspath(folder)}" >/dev/null 2>&1')
    except Exception:
        pass



# -----------------------------
# DATE DEBUG HELPERS
# -----------------------------
def debug_date_rows(available: Dict[str, Any], sheet_ranges: Dict[str, Tuple[date, date]]) -> List[str]:
    lines: List[str] = []
    lines.append("DATE DEBUG - what converter can see in Excel")
    lines.append("===========================================")
    for sheet_name in PROCESS_ORDER:
        if sheet_name not in sheet_ranges or sheet_name not in available:
            continue
        ws = available[sheet_name]
        start, end = sheet_ranges[sheet_name]
        lines.append(f"\n{sheet_name}: selected {human_date(start)} to {human_date(end)}")
        try:
            if sheet_name in FUEL_SHEETS:
                header_row, hm = find_header_map(ws, ["Date", "Party Name", FUEL_SHEETS[sheet_name]["qty_header"], "Rate", "Amount"])
                qty_h = FUEL_SHEETS[sheet_name]["qty_header"]
                seen = 0
                for r in range(header_row + 1, ws.max_row + 1):
                    raw_date = get_cell(ws, r, hm, "Date")
                    if not raw_date:
                        continue
                    try:
                        d1 = parse_date(raw_date)
                        d2 = parse_date_for_selected_range(raw_date, start, end)
                        in_range = start <= d2 <= end
                        if in_range or seen < 12:
                            lines.append(f"row {r}: raw={raw_date!r} parsed={human_date(d1)} used={human_date(d2)} in_range={in_range} party={clean(get_cell(ws,r,hm,'Party Name'))!r} qty={get_cell(ws,r,hm,qty_h)!r} rate={get_cell(ws,r,hm,'Rate')!r} amount={get_cell(ws,r,hm,'Amount')!r}")
                            seen += 1
                    except Exception as e:
                        lines.append(f"row {r}: raw={raw_date!r} date error={e}")
                if seen == 0:
                    lines.append("No nonblank date rows found.")
            elif sheet_name == METER_READING_SHEET:
                header_row, hm = find_header_map(ws, ["Date", "Pump Meter", "Opening", "Closing", "Testing", "Net Sale", "Rate", "Amount"])
                seen = 0
                for r in range(header_row + 1, ws.max_row + 1):
                    raw_date = get_cell(ws, r, hm, "Date")
                    if not raw_date:
                        continue
                    try:
                        d1 = parse_date(raw_date)
                        d2 = parse_date_for_selected_range(raw_date, start, end)
                        in_range = start <= d2 <= end
                        if in_range or seen < 12:
                            lines.append(f"row {r}: raw={raw_date!r} parsed={human_date(d1)} used={human_date(d2)} in_range={in_range} meter={clean(get_cell(ws,r,hm,'Pump Meter'))!r} closing={get_cell(ws,r,hm,'Closing')!r} amount={get_cell(ws,r,hm,'Amount')!r}")
                            seen += 1
                    except Exception as e:
                        lines.append(f"row {r}: raw={raw_date!r} date error={e}")
                if seen == 0:
                    lines.append("No nonblank date rows found.")
            elif sheet_name == LUBE_SHEET:
                header_row, hm = find_header_map(ws, ["Date", "Party Name", "Item Name", "Qty", "Rate Incl Tax", "Amount"])
                seen = 0
                for r in range(header_row + 1, ws.max_row + 1):
                    raw_date = get_cell(ws, r, hm, "Date")
                    if not raw_date:
                        continue
                    try:
                        d1 = parse_date(raw_date)
                        d2 = parse_date_for_selected_range(raw_date, start, end)
                        in_range = start <= d2 <= end
                        if in_range or seen < 12:
                            lines.append(f"row {r}: raw={raw_date!r} parsed={human_date(d1)} used={human_date(d2)} in_range={in_range} party={clean(get_cell(ws,r,hm,'Party Name'))!r} item={clean(get_cell(ws,r,hm,'Item Name'))!r} qty={get_cell(ws,r,hm,'Qty')!r} amount={get_cell(ws,r,hm,'Amount')!r}")
                            seen += 1
                    except Exception as e:
                        lines.append(f"row {r}: raw={raw_date!r} date error={e}")
                if seen == 0:
                    lines.append("No nonblank date rows found.")
            elif sheet_name == "PAYMENT":
                header_row, hm = find_header_map(ws, ["Date", "Paid From Ledger", "Paid To Ledger", "Amount"])
                seen = 0
                for r in range(header_row + 1, ws.max_row + 1):
                    raw_date = get_cell(ws, r, hm, "Date")
                    if not raw_date:
                        continue
                    try:
                        d1 = parse_date(raw_date)
                        d2 = parse_date_for_selected_range(raw_date, start, end)
                        in_range = start <= d2 <= end
                        if in_range or seen < 12:
                            lines.append(f"row {r}: raw={raw_date!r} parsed={human_date(d1)} used={human_date(d2)} in_range={in_range} amount={get_cell(ws,r,hm,'Amount')!r}")
                            seen += 1
                    except Exception as e:
                        lines.append(f"row {r}: raw={raw_date!r} date error={e}")
                if seen == 0:
                    lines.append("No nonblank date rows found.")
            elif sheet_name == "RECEIPT":
                header_row, hm = find_header_map(ws, ["Date", "Received In Ledger", "Received From Ledger", "Amount"])
                seen = 0
                for r in range(header_row + 1, ws.max_row + 1):
                    raw_date = get_cell(ws, r, hm, "Date")
                    if not raw_date:
                        continue
                    try:
                        d1 = parse_date(raw_date)
                        d2 = parse_date_for_selected_range(raw_date, start, end)
                        in_range = start <= d2 <= end
                        if in_range or seen < 12:
                            lines.append(f"row {r}: raw={raw_date!r} parsed={human_date(d1)} used={human_date(d2)} in_range={in_range} amount={get_cell(ws,r,hm,'Amount')!r}")
                            seen += 1
                    except Exception as e:
                        lines.append(f"row {r}: raw={raw_date!r} date error={e}")
                if seen == 0:
                    lines.append("No nonblank date rows found.")
        except Exception as e:
            lines.append(f"Could not debug {sheet_name}: {e}")
    return lines

# -----------------------------
# MAIN
# -----------------------------
def main():
    parser = argparse.ArgumentParser(description="SFM V16 day-first Excel to Tally XML converter")
    parser.add_argument("--excel", default=EXCEL_FILE, help="Excel file path")
    parser.add_argument("--from", dest="from_date", help="Common from date, example 21/06/2026")
    parser.add_argument("--to", dest="to_date", help="Common to date, example 25/06/2026")
    parser.add_argument("--sheets", default="ask", help="all, ask, or comma list: HSD SALES,MS SALES")
    parser.add_argument("--force", action="store_true", help="Continue even if duplicate/overlap warning is found")
    parser.add_argument("--history", default=None, help="Import history CSV path")
    parser.add_argument("--yes", action="store_true", help="Do not ask final confirmation before XML creation")
    parser.add_argument("--stored-dates", action="store_true", help="Use native stored Excel dates instead of interpreting SFM displayed numbers day first")
    args = parser.parse_args()
    if not args.history:
        args.history = default_history_path()

    excel_path = args.excel
    if not os.path.exists(excel_path):
        print(f"Excel file not found: {excel_path}")
        print("Keep this converter in the same folder as SFM Daily Readings.xlsx")
        sys.exit(1)

    print("SFM Excel to Tally XML Converter - V16 DAY-FIRST")
    print("================================")
    print(f"Excel file: {excel_path}")
    print(f"XML output folder: {OUTPUT_FOLDER}")
    print(f"CSV history folder: {CSV_FOLDER}")

    from sfm_date_reader import load_sales_workbook, write_date_audit, POLICIES
    wb = load_sales_workbook(excel_path, POLICIES[1] if args.stored_dates else POLICIES[0])
    ledgers, ledger_errors = load_ledger_master(wb)
    items, item_units, item_gst, item_errors = load_item_master(wb)
    meter_master, meter_errors = load_meter_master(wb)
    available = build_available_sheets(wb)

    if not available:
        print("No supported sheets found.")
        sys.exit(1)

    # CLI date mode: use same date range for selected sheets.
    if args.from_date and args.to_date:
        start, end = parse_date(args.from_date), parse_date(args.to_date)
        if start > end:
            print("From date cannot be after To date.")
            sys.exit(1)
        selected = parse_cli_sheets(args.sheets if args.sheets != "ask" else "all", available)
        sheet_ranges = {name: (start, end) for name in selected}
        mode = "common"
    else:
        sheet_ranges, mode = get_sheet_date_ranges_interactive(available)

    if not sheet_ranges:
        print("No sheet selected. Nothing to convert.")
        sys.exit(0)

    duplicate_warnings = duplicate_warnings_from_history(sheet_ranges, args.history)
    if duplicate_warnings:
        print("\nWARNING: Possible duplicate import range found.")
        for warning in duplicate_warnings:
            print(f"- {warning}")
        print("\nIf these XML files were already imported in Tally, importing again can create duplicate vouchers.")
        if not args.force:
            if not ask_yes_no("Continue and create XML anyway?", False):
                warning_file = write_duplicate_warning_report(duplicate_warnings, sheet_ranges)
                print(f"Stopped. Duplicate warning report created: {warning_file}")
                sys.exit(0)

    errors: List[str] = []
    errors.extend(ledger_errors)
    errors.extend(item_errors)
    if METER_READING_SHEET in sheet_ranges:
        errors.extend(meter_errors)
    counts: Dict[str, int] = defaultdict(int)
    vouchers: List[str] = []
    meter_opening_notes: List[str] = []
    meter_opening_warnings: List[str] = []

    reconciliation = []

    # Process sheets in fixed order.
    for sheet_name in PROCESS_ORDER:
        if sheet_name not in sheet_ranges:
            continue
        ws = available.get(sheet_name)
        if ws is None:
            continue
        start, end = sheet_ranges[sheet_name]
        try:
            if sheet_name in FUEL_SHEETS:
                process_fuel_sheet(ws, FUEL_SHEETS[sheet_name], start, end, ledgers, items, errors, counts, vouchers)
            elif sheet_name == LUBE_SHEET:
                process_lube_sheet(ws, start, end, ledgers, items, item_units, item_gst, errors, counts, vouchers)
            elif sheet_name == METER_READING_SHEET:
                process_meter_reading_sheet(ws, start, end, meter_master, errors, counts, vouchers, meter_opening_notes, meter_opening_warnings)
            elif sheet_name == "PAYMENT":
                process_payment_sheet(ws, start, end, ledgers, errors, counts, vouchers)
            elif sheet_name == "RECEIPT":
                process_receipt_sheet(ws, start, end, ledgers, errors, counts, vouchers)
        except Exception as e:
            errors.append(f"{sheet_name}: {e}")

    from sfm_date_reader import reconcile_sheet
    import xml.etree.ElementTree as ET
    type_map = {name: cfg['voucher_type'] for name,cfg in FUEL_SHEETS.items()}
    type_map.update({'LUBE SALES': LUBE_VOUCHER_TYPE, 'METER READING': METER_VOUCHER_TYPE, 'PAYMENT':'Payment', 'RECEIPT':'Receipt'})
    for name,(start,end) in sheet_ranges.items():
        try:
            subset=[v for v in vouchers if ET.fromstring(v.replace('&#4;','')).find('VOUCHER/VOUCHERTYPENAME').text==type_map[name]]
            reconciliation.extend(reconcile_sheet(available[name],start,end,subset,errors))
        except Exception as e:
            errors.append(f'{name}: reconciliation could not finish: {e}')

    if mode == "common" and sheet_ranges:
        all_ranges = list(sheet_ranges.values())
        common_start, common_end = all_ranges[0]
        range_name = f"{file_date(common_start)}_to_{file_date(common_end)}"
    else:
        range_name = f"Sheetwise_{datetime.now().strftime('%d-%m-%Y_%H%M%S')}"

    # Create XML and report inside XML_OUTPUT folder, grouped in a month-wise subfolder (e.g. "JULY 2026")
    month_date = earliest_range_date(sheet_ranges)
    xml_file = out_path(f"SFM_Tally_Import_{range_name}.xml", month_date)
    report_file = out_path(f"SFM_Tally_Import_Report_{range_name}.txt", month_date)
    write_date_audit(wb, report_file.replace('.txt','_Dates.csv'), sheet_ranges)
    with open(report_file.replace('.txt','_Reconciliation.txt'),'w',encoding='utf-8') as audit:
        audit.write('\n'.join(reconciliation))

    with open(report_file, "w", encoding="utf-8") as rep:
        rep.write("SFM TALLY XML IMPORT REPORT\n")
        rep.write("===========================\n")
        rep.write(f"Excel File: {excel_path}\n")
        rep.write(f"Date Mode: {'Common date range' if mode == 'common' else 'Sheet-wise date ranges'}\n\n")
        rep.write("Selected sheets and date ranges:\n")
        for name in PROCESS_ORDER:
            if name in sheet_ranges:
                s, e = sheet_ranges[name]
                rep.write(f"- {name}: {human_date(s)} to {human_date(e)}\n")
        rep.write("\n")

        if duplicate_warnings:
            rep.write("DUPLICATE / OVERLAP WARNING:\n")
            for warning in duplicate_warnings:
                rep.write(f"- {warning}\n")
            rep.write("\n")

        if meter_opening_notes:
            rep.write("AUTO METER OPENING USED:\n")
            for note in meter_opening_notes:
                rep.write(f"- {note}\n")
            rep.write("\n")

        if meter_opening_warnings:
            rep.write("METER OPENING MISMATCH WARNINGS:\n")
            for warning in meter_opening_warnings:
                rep.write(f"- {warning}\n")
            rep.write("\n")

        if errors:
            rep.write("STATUS: ERROR - XML NOT CREATED\n\n")
            rep.write("Fix these errors first:\n")
            for err in errors:
                rep.write(f"- {err}\n")
        elif not vouchers:
            rep.write("STATUS: NO DATA\n\n")
            rep.write("No vouchers found in selected date range.\n\n")
            rep.write("IMPORTANT CHECKS:\n")
            rep.write("1. Save and close Excel before running converter.\n")
            rep.write("2. Make sure this is the same SFM Daily Readings.xlsx kept in converter folder.\n")
            rep.write("3. Check Date Debug below. Dates are day/month/year and are never swapped to fit a range. Correct any wrongly stored date in Excel explicitly.\n\n")
            for line in debug_date_rows(available, sheet_ranges):
                rep.write(line + "\n")
        else:
            rep.write("STATUS: SUCCESS\n\n")
            rep.write("Voucher counts:\n")
            total_vouchers = 0
            for key in sorted(counts.keys()):
                rep.write(f"- {key}: {counts[key]}\n")
                if not key.endswith("item rows"):
                    total_vouchers += counts[key]
            rep.write(f"\nTotal vouchers created: {total_vouchers}\n")
            rep.write(f"XML File: {xml_file}\n")

    if errors:
        print("\nERROR: XML was not created because validation errors were found.")
        print(f"Open report: {report_file}")
        sys.exit(1)

    if not vouchers:
        print("\nNo vouchers found in selected date range.")
        print(f"Open report: {report_file}")
        sys.exit(0)

    total_vouchers = print_preimport_summary(sheet_ranges, counts, available, duplicate_warnings, meter_opening_notes, meter_opening_warnings)
    checklist_file = write_import_checklist(range_name, sheet_ranges, counts, available, duplicate_warnings, meter_opening_notes, meter_opening_warnings)
    print(f"Checklist created: {checklist_file}")
    if not args.yes:
        if not ask_yes_no("Create XML now?", True):
            print("Stopped before XML creation.")
            sys.exit(0)

    xml_text_to_write = envelope(vouchers)
    with open(xml_file, "w", encoding="utf-8") as f:
        f.write(xml_text_to_write)

    try:
        append_import_history(args.history, xml_file, mode, sheet_ranges, counts, available)
        history_status = f"Import history updated: {args.history}"
    except Exception as e:
        history_status = f"Import history not updated: {e}"

    print("\nSUCCESS")
    print(f"XML created   : {xml_file}")
    print(f"Report created: {report_file}")
    print(history_status)
    print("\nSelected sheets:")
    for name in PROCESS_ORDER:
        if name in sheet_ranges:
            s, e = sheet_ranges[name]
            print(f"- {name}: {human_date(s)} to {human_date(e)}")
    print("\nVoucher counts:")
    for key in sorted(counts.keys()):
        print(f"- {key}: {counts[key]}")
    print("\nImport in Tally: O: Import -> Transactions/Vouchers -> select the XML file")


if __name__ == "__main__":
    main()
