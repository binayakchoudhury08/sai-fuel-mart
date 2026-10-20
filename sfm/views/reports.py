from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request, send_file

from .. import db
from ..exports import workbook_bytes
from ..security import audit, login_required
from ..services import PARTY_KIND_LABEL, fuels, lube_stock, party_balances, range_summary
from ..util import ZERO, UserError, now_ist, parse_date, today

bp = Blueprint("reports", __name__)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _range(default_days=None):
    t = today()
    s = request.args.get("from")
    e = request.args.get("to")
    start = parse_date(s, "From date") if s else (t - timedelta(days=default_days) if default_days else t.replace(day=1))
    end = parse_date(e, "To date") if e else t
    if end < start:
        start, end = end, start
    if (end - start).days > 400:
        raise UserError("Choose a range of 400 days or less.")
    return start, end


@bp.route("/reports")
@login_required("manager")
def index():
    start, end = _range()
    with db.tx() as cur:
        rows = range_summary(cur, start, end)
        fl = fuels(cur, active_only=False)
    keys = ["fuel_total", "lube_total", "gross_sale", "digital", "credit", "payments", "cash_advance",
            "expenses", "staff_cash", "expected_cash"]
    tot = {k: sum((r[k] for r in rows), ZERO) for k in keys}
    tot["qty"] = {f["code"]: sum((r["qty"].get(f["code"], ZERO) for r in rows), ZERO) for f in fl}
    tot["difference"] = sum((r["difference"] for r in rows if r["difference"] is not None), ZERO)
    tot["short_days"] = sum(1 for r in rows if r["difference"] is not None and r["difference"] < 0)
    if request.args.get("format") == "xlsx":
        heads = ["Date", "Status"] + [f"{f['code']} qty" for f in fl] + [
            "Fuel sale", "Lube sale", "Total sale", "Digital", "Credit given", "Payments recd", "Cash to drivers",
            "Expenses", "Staff cash", "Expected cash", "Counted cash", "Short(-)/Excess(+)"]
        data = [[r["date"], r["status"]] + [r["qty"].get(f["code"], ZERO) for f in fl] +
                [r["fuel_total"], r["lube_total"], r["gross_sale"], r["digital"], r["credit"], r["payments"],
                 r["cash_advance"], r["expenses"], r["staff_cash"], r["expected_cash"], r["counted"],
                 r["difference"]] for r in rows]
        n = len(fl)
        money_cols = tuple(range(3, 3 + n + 12))
        return send_file(workbook_bytes([("Daily sales", heads, data, money_cols)]), mimetype=XLSX,
                         as_attachment=True, download_name=f"Sales {start:%d%m%y}-{end:%d%m%y}.xlsx")
    return render_template("reports/index.html", rows=rows, tot=tot, start=start, end=end, fuels=fl)


@bp.route("/reports/outstanding")
@login_required("manager")
def outstanding():
    with db.tx() as cur:
        rows = party_balances(cur)
    rows = sorted(rows, key=lambda r: -Decimal(r["balance"]))
    t = today()
    for r in rows:
        r["days_since_payment"] = (t - r["last_payment"]).days if r["last_payment"] else None
    if request.args.get("format") == "xlsx":
        data = [[r["name"], r["mobile"], "Active" if r["active"] else "Inactive", r["total_debit"],
                 r["total_credit"], r["balance"], r["credit_limit"] or None, r["last_payment"],
                 r["days_since_payment"]] for r in rows]
        return send_file(workbook_bytes([("Outstanding", ["Party", "Mobile", "Status", "Total debit",
                                          "Total credit", "Balance", "Credit limit", "Last payment",
                                          "Days since payment"], data, (4, 5, 6, 7))]),
                         mimetype=XLSX, as_attachment=True, download_name=f"Outstanding {t:%d%m%y}.xlsx")
    return render_template("reports/outstanding.html", rows=rows)


@bp.route("/reports/credit-register")
@login_required("manager")
def credit_register():
    start, end = _range()
    kind = request.args.get("kind") or ""
    with db.tx() as cur:
        sql = """SELECT t.*, p.name AS party_name, lp.name AS lube_name FROM party_txns t
                 JOIN parties p ON p.id=t.party_id LEFT JOIN lube_products lp ON lp.id=t.lube_product_id
                 WHERE t.business_date BETWEEN %s AND %s"""
        params = [start, end]
        if kind in PARTY_KIND_LABEL:
            sql += " AND t.kind=%s"
            params.append(kind)
        sql += " ORDER BY t.business_date, t.id"
        cur.execute(sql, params)
        rows = cur.fetchall()
    live = [r for r in rows if not r["voided"]]
    tot = {"debit": sum((Decimal(r["debit"]) for r in live), ZERO),
           "credit": sum((Decimal(r["credit"]) for r in live), ZERO)}
    if request.args.get("format") == "xlsx":
        data = [[r["business_date"], r["party_name"], PARTY_KIND_LABEL[r["kind"]], r["fuel_code"] or r["lube_name"],
                 r["qty"] or None, r["rate"] or None, r["debit"] or None, r["credit"] or None, r["pay_mode"],
                 r["receipt_no"], r["vehicle_no"], r["challan_no"], r["slip_no"], r["reference"], r["remarks"],
                 "CANCELLED: " + (r["void_reason"] or "") if r["voided"] else "", r["created_by"]] for r in rows]
        heads = ["Date", "Party", "Type", "Item", "Qty", "Rate", "Debit", "Credit", "Mode", "Receipt", "Vehicle",
                 "Challan", "Slip", "Reference", "Remarks", "Cancelled", "Entered by"]
        return send_file(workbook_bytes([("Credit register", heads, data, (6, 7, 8))]), mimetype=XLSX,
                         as_attachment=True, download_name=f"Credit register {start:%d%m%y}-{end:%d%m%y}.xlsx")
    return render_template("reports/credit_register.html", rows=rows, tot=tot, start=start, end=end, kind=kind,
                           KIND=PARTY_KIND_LABEL)


@bp.route("/reports/lube.xlsx")
@login_required("manager")
def lube_xlsx():
    with db.tx() as cur:
        prods = lube_stock(cur)
        cur.execute("""SELECT m.*, p.name FROM lube_moves m JOIN lube_products p ON p.id=m.product_id
                       ORDER BY m.business_date, m.id""")
        moves = cur.fetchall()
    s1 = [[p["name"], p["unit"], p["sale_rate"], p["stock"], Decimal(p["stock"]) * Decimal(p["sale_rate"]),
           p["reorder_level"], "Active" if p["active"] else "Inactive"] for p in prods]
    s2 = [[m["business_date"], m["name"], m["kind"], m["qty"], m["rate"], m["amount"], m["pay_mode"], m["remarks"],
           "CANCELLED" if m["voided"] else "", m["created_by"]] for m in moves]
    return send_file(workbook_bytes([
        ("Stock", ["Product", "Unit", "Sale rate", "Stock", "Stock value", "Reorder at", "Status"], s1, (3, 5)),
        ("Movements", ["Date", "Product", "Type", "Qty", "Rate", "Amount", "Mode", "Remarks", "Cancelled", "By"],
         s2, (5, 6))]), mimetype=XLSX, as_attachment=True, download_name=f"Lube stock {today():%d%m%y}.xlsx")


BACKUP_TABLES = ["settings", "users", "fuels", "fuel_rates", "tanks", "nozzles", "days", "nozzle_readings",
                 "collection_modes", "day_collections", "expenses", "parties", "party_txns", "lube_products",
                 "lube_moves", "tank_dips", "fuel_receipts", "staff", "attendance", "staff_payments", "proofs",
                 "audit_log"]


@bp.route("/reports/backup.xlsx")
@login_required("owner")
def backup():
    sheets = []
    with db.tx() as cur:
        for t in BACKUP_TABLES:
            cur.execute(f"SELECT * FROM {t} ORDER BY 1")
            rows = cur.fetchall()
            cols = [c.name for c in cur.description]
            if t == "users":
                cols = [c for c in cols if c != "password_hash"]
            data = [[(str(r[c]) if isinstance(r[c], (dict, list)) else r[c]) for c in cols] for r in rows]
            sheets.append((t, cols, data, ()))
        audit(cur, "export", "backup", None, "Downloaded full backup")
    return send_file(workbook_bytes(sheets), mimetype=XLSX, as_attachment=True,
                     download_name=f"SaiFuelMart backup {now_ist():%Y-%m-%d %H%M}.xlsx")
