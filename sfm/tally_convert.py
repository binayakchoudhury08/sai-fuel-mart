"""SFM Converter Studio V16 (the owner's own converter), run inside the website — same steps as its GUI:
load with 'SFM displayed dates', check masters, process sheets in order, reconcile per date, build the XML."""
import os
import sys
import tempfile
from collections import defaultdict

_HERE = os.path.join(os.path.dirname(__file__), "tallyconv")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def convert(xlsx_bytes, start, end, sheets=None):
    """→ {"ok", "xml", "errors", "counts", "reconciliation", "name"}; any error blocks the XML (like the converter)."""
    import sfm_converter_core as core
    from sfm_date_reader import load_sales_workbook, reconcile_sheet
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        f.write(xlsx_bytes)
        path = f.name
    try:
        wb = load_sales_workbook(path)
    finally:
        os.unlink(path)
    ledgers, ledger_errors = core.load_ledger_master(wb)
    items, item_units, item_gst, item_errors = core.load_item_master(wb)
    meter_master, meter_errors = core.load_meter_master(wb)
    available = core.build_available_sheets(wb)
    chosen = [s for s in core.PROCESS_ORDER if s in available and (not sheets or s in sheets)]
    errors = list(ledger_errors) + list(item_errors) + (list(meter_errors) if core.METER_READING_SHEET in chosen else [])
    counts, vouchers, recon, notes, warns = defaultdict(int), [], [], [], []
    for name in chosen:
        ws = available[name]
        before = len(vouchers)
        try:
            if name in core.FUEL_SHEETS:
                core.process_fuel_sheet(ws, core.FUEL_SHEETS[name], start, end, ledgers, items, errors, counts, vouchers)
            elif name == core.LUBE_SHEET:
                core.process_lube_sheet(ws, start, end, ledgers, items, item_units, item_gst, errors, counts, vouchers)
            elif name == core.METER_READING_SHEET:
                core.process_meter_reading_sheet(ws, start, end, meter_master, errors, counts, vouchers, notes, warns)
            elif name == "PAYMENT":
                core.process_payment_sheet(ws, start, end, ledgers, errors, counts, vouchers)
            elif name == "RECEIPT":
                core.process_receipt_sheet(ws, start, end, ledgers, errors, counts, vouchers)
        except Exception as e:
            errors.append(f"{name}: {e}")
        try:
            recon.extend(reconcile_sheet(ws, start, end, vouchers[before:], errors))
        except Exception as e:
            errors.append(f"{name}: reconciliation could not finish: {e}")
    name = f"SFM_Tally_Import_{core.file_date(start)}_to_{core.file_date(end)}.xml"
    ok = not errors and bool(vouchers)
    return {"ok": ok, "xml": core.envelope(vouchers) if ok else None, "errors": errors, "counts": dict(counts),
            "reconciliation": recon, "notes": notes + warns, "name": name, "vouchers": len(vouchers)}
