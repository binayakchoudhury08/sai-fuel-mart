"""Credit Transport: the daily register of diesel/cash given to transporters,
bills in the original format (with per-litre discount), and daily bill ZIPs."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from .. import db
from ..exports import (bill_filename, transport_bill_pdf, transport_bill_xlsx, transport_zip, workbook_bytes,
                       report_pdf)
from ..security import (assert_transport_writable, assert_writable, audit, can, claim_form_token, current_user, login_required, role)
from ..services import (PAY_MODES, fuels, next_sl_no, party_balance, party_balances, rates_for,
                        register_totals, settings_map, transport_register)
from ..util import ZERO, UserError, clean, inr, money, parse_date, rupee, to_dec, today


def _deliver_compat(buf, mimetype=None, as_attachment=True, download_name=None, **kw):
    from ..storage import deliver
    return deliver(buf, download_name, mimetype)


bp = Blueprint("transport", __name__)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _filters(default_days=0):
    t = today()
    s, e = request.args.get("from"), request.args.get("to")
    start = parse_date(s, "From date") if s else t - timedelta(days=default_days)
    end = parse_date(e, "To date") if e else t
    if end < start:
        start, end = end, start
    if (end - start).days > 400:
        raise UserError("Choose 400 days or less.")
    pid = request.args.get("party") or None
    try:
        pid = int(pid) if pid else None
    except ValueError:
        pid = None
    return start, end, pid, clean(request.args.get("q"), 60)


def _month(ym):
    from ..util import month_bounds
    try:
        return month_bounds(ym)
    except Exception:
        return month_bounds(today().strftime("%Y-%m"))


@bp.route("/transport")
@login_required()
def index():
    d = parse_date(request.args.get("d") or today().isoformat())
    ym = request.args.get("month") or d.strftime("%Y-%m")
    m1, m2 = _month(ym)
    pid = request.args.get("party") or None
    pid = int(pid) if pid and str(pid).isdigit() else None
    q = clean(request.args.get("q"), 60)
    with db.tx() as cur:
        cur.execute("SELECT id, name, vehicles, active FROM parties ORDER BY active DESC, name")
        parties = cur.fetchall()
        rows = transport_register(cur, d, d, pid, q)
        cur.execute("""SELECT p.id, p.name, COUNT(t.id) AS entries,
                              COALESCE(SUM(t.qty) FILTER (WHERE t.kind='fuel'),0) AS qty,
                              COALESCE(SUM(t.debit - t.cash_taken) FILTER (WHERE t.kind='fuel'),0) AS fuel_amount,
                              COALESCE(SUM(t.cash_taken) FILTER (WHERE t.kind='fuel'),0)
                                + COALESCE(SUM(t.debit) FILTER (WHERE t.kind='cash_advance'),0) AS cash,
                              COALESCE(SUM(t.debit),0) AS total, COUNT(DISTINCT t.business_date) AS days
                       FROM parties p JOIN party_txns t ON t.party_id=p.id
                       WHERE NOT t.voided AND t.kind IN ('fuel','cash_advance') AND t.business_date BETWEEN %s AND %s
                       GROUP BY p.id, p.name ORDER BY p.name""", (m1, m2))
        month = cur.fetchall()
        rate = rates_for(cur, d).get("HSD")
    tot = register_totals(rows)
    mtot = {k: sum((Decimal(r[k]) for r in month), ZERO) for k in ("qty", "fuel_amount", "cash", "total")}
    from ..security import TRANSPORT_DAYS
    show_deleted = request.args.get("deleted") == "1"
    deleted_count = sum(1 for r in rows if r["voided"])
    return render_template("transport/index.html", parties=parties, rows=rows, tot=tot, d=d, ym=m1.strftime("%Y-%m"),
                           show_deleted=show_deleted, deleted_count=deleted_count,
                           min_date=today() - timedelta(days=3650),
                           month=month, mtot=mtot, m1=m1, pid=pid, q=q, rate=rate, PAY_MODES=PAY_MODES,
                           prev_day=d - timedelta(days=1), next_day=d + timedelta(days=1) if d < today() else None)


@bp.route("/transport/download")
@login_required("bills")
def download():
    """One place for every bill: transporter (or all), day or month, PDF / Excel / ZIP."""
    period = request.args.get("period", "day")
    fmt = request.args.get("fmt", "pdf")
    if fmt not in ("pdf", "xlsx", "zip") or period not in ("day", "month"):
        abort(404)
    if period == "day":
        start = end = parse_date(request.args.get("d") or today().isoformat())
    else:
        start, end = _month(request.args.get("month") or today().strftime("%Y-%m"))
    pid = request.args.get("party") or ""
    with db.tx() as cur:
        st = settings_map(cur)
        if pid and pid.isdigit():
            cur.execute("SELECT * FROM parties WHERE id=%s", (int(pid),))
            plist = cur.fetchall()
        else:
            cur.execute("""SELECT DISTINCT p.* FROM parties p JOIN party_txns t ON t.party_id=p.id
                           WHERE NOT t.voided AND t.kind IN ('fuel','cash_advance') AND t.business_date BETWEEN %s AND %s
                           ORDER BY p.name""", (start, end))
            plist = cur.fetchall()
        pairs = [(p, transport_register(cur, start, end, p["id"], include_voided=False)) for p in plist]
    pairs = [x for x in pairs if x[1]]
    label = start.strftime("%d %b %Y") if period == "day" else start.strftime("%B %Y")
    if not pairs:
        flash(f"No transport entries for {label}" + (" for that transporter" if pid else "") + ".", "info")
        return redirect(url_for("transport.index", d=start.isoformat(), month=start.strftime("%Y-%m")))
    if fmt in ("pdf", "xlsx") and len(pairs) == 1:
        p, rows = pairs[0]
        maker = transport_bill_pdf if fmt == "pdf" else transport_bill_xlsx
        return _deliver_compat(maker(p, rows, start, end, st), as_attachment=True,
                         mimetype="application/pdf" if fmt == "pdf" else XLSX,
                         download_name=bill_filename(p["name"], start, fmt, end))
    # ZIP (or several transporters): each transporter in its own folder
    if fmt == "zip":
        buf, n = transport_zip(pairs, st, start, end)
    else:
        import zipfile
        from io import BytesIO
        buf = BytesIO()
        maker = transport_bill_pdf if fmt == "pdf" else transport_bill_xlsx
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p, rows in pairs:
                zf.writestr(bill_filename(p["name"], start, fmt, end), maker(p, rows, start, end, st).getvalue())
        buf.seek(0)
    who = pairs[0][0]["name"] if (pid and len(pairs) == 1) else "All transporters"
    kind = {"pdf": "PDF", "xlsx": "Excel", "zip": "PDF+Excel"}[fmt]
    return _deliver_compat(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{''.join(c if c.isalnum() else ' ' for c in who).strip()} Bills {label} {kind}.zip")


def create_entry(cur, form, d, sl_no=None, corrected_from=None):
    """Insert one register row (diesel + cash taken). Returns the new row."""
    try:
        pid = int(form.get("party") or 0)
    except ValueError:
        pid = 0
    cur.execute("SELECT * FROM parties WHERE id=%s FOR UPDATE", (pid,))
    p = cur.fetchone()
    if not p:
        raise UserError("Choose the transporter.")
    if not p["active"]:
        raise UserError(f"{p['name']} is inactive. Activate the party before giving credit.")
    code = form.get("fuel_code") or "HSD"
    if form.get("kind") == "cash_advance":
        # "cash given to driver" only: the amount is cash, never diesel
        q, amt_in = ZERO, ZERO
        cash = rupee(to_dec(form.get("cash_taken") or form.get("amount"), "Cash amount"))
    else:
        q = to_dec(form.get("qty"), "Quantity")
        cash = rupee(to_dec(form.get("cash_taken"), "Cash taken"))
        amt_in = to_dec(form.get("amount"), "Amount")
    rate = ZERO
    if q > 0 or amt_in > 0:
        rates = rates_for(cur, d)
        typed = form.get("rate") and can("rate_override")
        if not rates.get(code) and not typed:
            raise UserError(f"No {code} rate is set for {d.strftime('%d %b %Y')}. Set it in Settings → Fuel rates.")
        rate = rates.get(code) or ZERO
        if typed:
            rate = to_dec(form.get("rate"), "Rate")
            if rate <= 0:
                raise UserError("Rate must be more than zero.")
        if q <= 0:
            q = (amt_in / rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if q <= 0 and cash <= 0:
        raise UserError("Enter litres (or amount) and/or cash taken.")
    if q > 20000:
        raise UserError("That quantity looks like a typing mistake.")
    # every slip is kept in whole rupees (nearest rupee), so bills, balances and the closing check agree
    fuel_amt = rupee(amt_in) if (amt_in > 0 and to_dec(form.get("qty"), "Quantity") <= 0) else rupee(q * rate)
    total = fuel_amt + cash
    vehicle = clean(form.get("vehicle_no"), 20).upper().replace(" ", "")
    challan = clean(form.get("challan_no"), 30)
    slip = clean(form.get("slip_no"), 30)
    if Decimal(p["credit_limit"]) > 0 and not can("credit_limit_override"):
        new_bal = party_balance(cur, pid) + total
        if new_bal > Decimal(p["credit_limit"]):
            raise UserError(f"Credit limit crossed: {p['name']} would owe {inr(new_bal)} "
                            f"(limit {inr(p['credit_limit'])}). Only the owner can allow this.")
    kind = "fuel" if q > 0 else "cash_advance"
    cur.execute("""INSERT INTO party_txns (party_id, business_date, kind, fuel_code, qty, rate, debit, cash_taken,
                       vehicle_no, challan_no, slip_no, remarks, sl_no, corrected_from, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (pid, d, kind, code if kind == "fuel" else None, q if kind == "fuel" else ZERO, rate, total,
                 cash if kind == "fuel" else ZERO, vehicle, challan, slip, clean(form.get("remarks"), 200),
                 sl_no or next_sl_no(cur, d), corrected_from, current_user()["username"]))
    return cur.fetchone(), p


@bp.route("/transport/entry", methods=["POST"])
@login_required("transport_enter")
def add_entry(party_id=None):
    d = parse_date(request.form.get("business_date") or today().isoformat())
    form = request.form.to_dict()
    if party_id and not form.get("party"):
        form["party"] = str(party_id)
    with db.tx() as cur:
        claim_form_token(cur)
        assert_transport_writable(cur, d)
        cur.execute("INSERT INTO days (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
        r, p = create_entry(cur, form, d)
        audit(cur, "create", "transport_entry", r["id"],
              f"SL {r['sl_no']} {p['name']}: {r['qty']} {r['fuel_code'] or ''} @ ₹{r['rate']} "
              f"+ cash ₹{r['cash_taken'] if r['kind'] == 'fuel' else r['debit']} = ₹{r['debit']}",
              after=r, business_date=d)
    flash(f"Entry SL {r['sl_no']} saved for {p['name']}: {inr(r['debit'])}.", "success")
    back = request.form.get("_back")
    if back and back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect(url_for("transport.index", **{"from": d.isoformat(), "to": d.isoformat()}))


@bp.route("/transport/entry/<int:tid>/correct", methods=["POST"])
@login_required("cancel_entries")
def correct_entry(tid):
    """Fix an amount/qty mistake: the old row is cancelled (kept, struck through) and a
    corrected row takes the same SL No — one step, fully audited."""
    reason = clean(request.form.get("reason"), 200)
    if not reason:
        raise UserError("Write what was wrong with the entry.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM party_txns WHERE id=%s FOR UPDATE", (tid,))
        old = cur.fetchone()
        if not old or old["voided"] or old["kind"] not in ("fuel", "cash_advance"):
            raise UserError("Entry not found or already cancelled.")
        d = old["business_date"]
        assert_transport_writable(cur, d)
        cur.execute("""UPDATE party_txns SET voided=TRUE, void_reason=%s, voided_by=%s, voided_at=NOW() WHERE id=%s""",
                    (f"Corrected: {reason}", current_user()["username"], tid))
        form = dict(request.form)
        form["party"] = str(old["party_id"])
        new, p = create_entry(cur, form, d, sl_no=old["sl_no"], corrected_from=tid)
        audit(cur, "correct", "transport_entry", new["id"],
              f"SL {old['sl_no']} {p['name']} corrected ₹{old['debit']} → ₹{new['debit']}: {reason}",
              before=old, after=new, business_date=d)
    flash(f"SL {old['sl_no']} corrected: {inr(old['debit'])} → {inr(new['debit'])}. The old entry stays visible.",
          "success")
    return redirect(request.form.get("_back") or url_for("transport.index"))


# old links keep working
@bp.route("/transport/bill")
@bp.route("/transport/bill.<fmt>")
@bp.route("/transport/bills.zip")
@login_required("bills")
def bill(fmt=None):
    args = dict(request.args)
    p = args.get("party") or ""
    if fmt is None and request.path.endswith(".zip"):
        fmt = "zip"
    if not fmt:
        return redirect(url_for("transport.index", party=p or None) + "#download")
    start = parse_date(args.get("from") or today().isoformat())
    end = parse_date(args.get("to") or start.isoformat())
    if start == end:
        return redirect(url_for("transport.download", party=p, period="day", d=start.isoformat(), fmt=fmt))
    return redirect(url_for("transport.download", party=p, period="month", month=start.strftime("%Y-%m"), fmt=fmt))


@bp.route("/transport/register.<fmt>")
@login_required("bills")
def register_file(fmt):
    start, end, pid, q = _filters(default_days=0)
    with db.tx() as cur:
        rows = transport_register(cur, start, end, pid, q)
        st = settings_map(cur)
    tot = register_totals(rows)
    data = [[r["business_date"], r["sl_no"], r["party_name"], r["challan_no"], r["vehicle_no"], r["slip_no"],
             r["fuel_code"] or "Cash", Decimal(r["qty"]) if r["kind"] == "fuel" else None,
             Decimal(r["rate"]) if r["kind"] == "fuel" else None, Decimal(r["fuel_amount"]), r["cash"],
             Decimal(r["debit"]), ("CANCELLED: " + (r["void_reason"] or "")) if r["voided"] else "", r["created_by"]]
            for r in rows]
    heads = ["Date", "SL", "Transporter", "Challan", "Vehicle", "Slip", "Fuel", "Qty", "Rate", "Fuel amt",
             "Cash taken", "Total", "Status", "By"]
    name = f"Transport register {start:%d%m%y}-{end:%d%m%y}"
    if fmt == "xlsx":
        data.append([None, None, "TOTAL (not counting cancelled)", None, None, None, None, tot["qty"], None,
                     tot["fuel_amount"], tot["cash"], tot["total"], None, None])
        return _deliver_compat(workbook_bytes([("Transport register", heads, data, (8, 9, 10, 11, 12))]), mimetype=XLSX,
                         as_attachment=True, download_name=name + ".xlsx")
    if fmt == "pdf":
        pdf_rows = [r[:12] + [r[12][:40]] for r in data]
        return _deliver_compat(report_pdf(st, "Credit transport register",
                                    f"{start:%d-%m-%Y} to {end:%d-%m-%Y}", heads[:13], pdf_rows,
                                    right_cols=range(7, 12),
                                    totals=["", "", "TOTAL", "", "", "", "", tot["qty"], "", tot["fuel_amount"],
                                            tot["cash"], tot["total"], ""],
                                    widths=[52, 24, 110, 55, 65, 45, 30, 45, 40, 60, 55, 62, 70]),
                         mimetype="application/pdf", as_attachment=True, download_name=name + ".pdf")
    abort(404)
