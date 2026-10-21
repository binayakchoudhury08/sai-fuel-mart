"""Credit Transport: the daily register of diesel/cash given to transporters,
bills in the original format (with per-litre discount), and daily bill ZIPs."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from .. import db
from ..exports import (bill_figures, bill_filename, bill_number, daily_bills_zip, transport_bill_pdf,
                       transport_bill_xlsx, workbook_bytes, report_pdf)
from ..security import (assert_writable, audit, can, claim_form_token, current_user, login_required, role)
from ..services import (PAY_MODES, fuels, next_sl_no, party_balance, party_balances, rates_for,
                        register_totals, settings_map, transport_register)
from ..util import ZERO, UserError, clean, inr, money, parse_date, to_dec, today

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


@bp.route("/transport")
@login_required()
def index():
    start, end, pid, q = _filters(default_days=0)
    t0 = today()
    with db.tx() as cur:
        parties = party_balances(cur)
        rows = transport_register(cur, start, end, pid, q)
        rates = rates_for(cur, t0)
        fl = fuels(cur)
        cur.execute("""SELECT COUNT(*) AS n FROM party_txns WHERE business_date=%s AND NOT voided
                       AND kind IN ('fuel','cash_advance')""", (t0,))
        today_count = cur.fetchone()["n"]
    tot = register_totals(rows)
    outstanding = sum((Decimal(p["balance"]) for p in parties if Decimal(p["balance"]) > 0), ZERO)
    return render_template("transport/index.html", parties=parties, rows=rows, tot=tot, start=start, end=end,
                           pid=pid, q=q, rates=rates, fuels=fl, today_count=today_count,
                           outstanding=outstanding, PAY_MODES=PAY_MODES,
                           total_credit=sum((Decimal(p["fuel_credit"]) + Decimal(p["lube_credit"]) for p in parties), ZERO))


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
        cash = to_dec(form.get("cash_taken") or form.get("amount"), "Cash amount")
    else:
        q = to_dec(form.get("qty"), "Quantity")
        cash = to_dec(form.get("cash_taken"), "Cash taken")
        amt_in = to_dec(form.get("amount"), "Amount")
    rate = ZERO
    if q > 0 or amt_in > 0:
        rates = rates_for(cur, d)
        if not rates.get(code):
            raise UserError(f"No {code} rate is set for {d.strftime('%d %b %Y')}. Set it in Settings → Fuel rates.")
        rate = rates[code]
        if form.get("rate") and can("manager"):
            rate = to_dec(form.get("rate"), "Rate")
            if rate <= 0:
                raise UserError("Rate must be more than zero.")
        if q <= 0:
            q = (amt_in / rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if q <= 0 and cash <= 0:
        raise UserError("Enter litres (or amount) and/or cash taken.")
    if q > 20000:
        raise UserError("That quantity looks like a typing mistake.")
    fuel_amt = money(q * rate)
    total = fuel_amt + cash
    vehicle = clean(form.get("vehicle_no"), 20).upper().replace(" ", "")
    challan = clean(form.get("challan_no"), 30)
    slip = clean(form.get("slip_no"), 30)
    if Decimal(p["credit_limit"]) > 0 and role() != "owner":
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
@login_required()
def add_entry(party_id=None):
    d = parse_date(request.form.get("business_date") or today().isoformat())
    form = request.form.to_dict()
    if party_id and not form.get("party"):
        form["party"] = str(party_id)
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
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
@login_required("manager")
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
        assert_writable(cur, d)
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


# ------------------------------------------------------------------ bills
def _bill_data(cur, pid, start, end):
    cur.execute("SELECT * FROM parties WHERE id=%s", (pid,))
    p = cur.fetchone()
    if not p:
        raise UserError("Choose the transporter.")
    return p, transport_register(cur, start, end, pid, include_voided=False)


@bp.route("/transport/bill")
@login_required()
def bill():
    start, end, pid, _ = _filters(default_days=0)
    if not request.args.get("from"):
        start = today().replace(day=1)
    with db.tx() as cur:
        parties = party_balances(cur)
        p, rows, f, posted = None, [], None, None
        if pid:
            p, rows = _bill_data(cur, pid, start, end)
            f = bill_figures(p, rows)
            cur.execute("""SELECT * FROM party_txns WHERE party_id=%s AND kind='discount' AND NOT voided
                           AND reference=%s""", (pid, _disc_ref(p, start, end)))
            posted = cur.fetchone()
    return render_template("transport/bill.html", parties=parties, p=p, rows=rows, f=f, start=start, end=end,
                           pid=pid, posted=posted, bill_no=bill_number(p, start, end) if p else None)


def _disc_ref(p, start, end):
    return f"Bill {bill_number(p, start, end)}"


@bp.route("/transport/bill.<fmt>")
@login_required()
def bill_file(fmt):
    start, end, pid, _ = _filters(default_days=0)
    if fmt not in ("pdf", "xlsx"):
        abort(404)
    with db.tx() as cur:
        p, rows = _bill_data(cur, pid, start, end)
        st = settings_map(cur)
    if fmt == "pdf":
        return send_file(transport_bill_pdf(p, rows, start, end, st), mimetype="application/pdf",
                         as_attachment=True, download_name=bill_filename(p["name"], start, "pdf"))
    return send_file(transport_bill_xlsx(p, rows, start, end, st), mimetype=XLSX, as_attachment=True,
                     download_name=bill_filename(p["name"], start, "xlsx"))


@bp.route("/transport/bills.zip")
@login_required()
def bills_zip():
    start, end, pid, _ = _filters(default_days=0)
    with db.tx() as cur:
        st = settings_map(cur)
        if pid:
            p, rows = _bill_data(cur, pid, start, end)
            pairs = [(p, rows)]
        else:
            cur.execute("SELECT * FROM parties ORDER BY name")
            pairs = [(p, transport_register(cur, start, end, p["id"], include_voided=False)) for p in cur.fetchall()]
            pairs = [x for x in pairs if x[1]]
    buf, n = daily_bills_zip(pairs, st)
    if n == 0:
        flash("No entries in that period, so there are no daily bills to download.", "info")
        return redirect(url_for("transport.bill", party=pid or "", **{"from": start.isoformat(), "to": end.isoformat()}))
    who = pairs[0][0]["name"] if pid else "All transporters"
    safe = "".join(c if c.isalnum() else "_" for c in who).strip("_")
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{safe}_Daily_Bills_{start:%d%m%y}_to_{end:%d%m%y}.zip")


@bp.route("/transport/bill/post-discount", methods=["POST"])
@login_required("manager")
def post_discount():
    """Put the bill's per-litre discount into the party's account, once per bill."""
    start = parse_date(request.form.get("from"))
    end = parse_date(request.form.get("to"))
    pid = int(request.form.get("party") or 0)
    d = today()
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        p, rows = _bill_data(cur, pid, start, end)
        f = bill_figures(p, rows)
        if f["discount"] <= 0:
            raise UserError("This bill has no discount to post.")
        ref = _disc_ref(p, start, end)
        cur.execute("SELECT id FROM party_txns WHERE party_id=%s AND kind='discount' AND reference=%s AND NOT voided",
                    (pid, ref))
        if cur.fetchone():
            raise UserError("This bill's discount is already in the account.")
        cur.execute("""INSERT INTO party_txns (party_id, business_date, kind, credit, reference, remarks, created_by)
                       VALUES (%s,%s,'discount',%s,%s,%s,%s) RETURNING *""",
                    (pid, d, f["discount"], ref,
                     f"₹{f['disc_rate']}/L × {f['litres']} L for {start:%d-%m-%Y} to {end:%d-%m-%Y}",
                     current_user()["username"]))
        t = cur.fetchone()
        audit(cur, "create", "party_txn", t["id"], f"Bill discount ₹{f['discount']} posted for {p['name']} ({ref})",
              after=t, business_date=d)
    flash(f"Discount {inr(f['discount'])} posted to {p['name']}'s account.", "success")
    return redirect(url_for("transport.bill", party=pid, **{"from": start.isoformat(), "to": end.isoformat()}))


@bp.route("/transport/discounts", methods=["GET", "POST"])
@login_required("manager")
def discounts():
    if request.method == "POST":
        if role() != "owner":
            abort(403)
        n = 0
        with db.tx() as cur:
            cur.execute("SELECT id, name, discount_per_litre FROM parties FOR UPDATE")
            for p in cur.fetchall():
                key = f"disc_{p['id']}"
                if key not in request.form:
                    continue
                v = to_dec(request.form.get(key), f"{p['name']} discount")
                if v > 20:
                    raise UserError(f"{p['name']}: ₹{v} per litre looks wrong.")
                if v != Decimal(p["discount_per_litre"]):
                    cur.execute("UPDATE parties SET discount_per_litre=%s WHERE id=%s", (v, p["id"]))
                    audit(cur, "update", "party", p["id"],
                          f"{p['name']} bill discount ₹{p['discount_per_litre']}/L → ₹{v}/L")
                    n += 1
        flash(f"{n} discount(s) updated." if n else "No discount changed.", "success" if n else "info")
        return redirect(url_for("transport.discounts"))
    with db.tx() as cur:
        parties = party_balances(cur)
    return render_template("transport/discounts.html", parties=parties)


@bp.route("/transport/register.<fmt>")
@login_required()
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
        return send_file(workbook_bytes([("Transport register", heads, data, (8, 9, 10, 11, 12))]), mimetype=XLSX,
                         as_attachment=True, download_name=name + ".xlsx")
    if fmt == "pdf":
        pdf_rows = [r[:12] + [r[12][:40]] for r in data]
        return send_file(report_pdf(st, "Credit transport register",
                                    f"{start:%d-%m-%Y} to {end:%d-%m-%Y}", heads[:13], pdf_rows,
                                    right_cols=range(7, 12),
                                    totals=["", "", "TOTAL", "", "", "", "", tot["qty"], "", tot["fuel_amount"],
                                            tot["cash"], tot["total"], ""],
                                    widths=[52, 24, 110, 55, 65, 45, 30, 45, 40, 60, 55, 62, 70]),
                         mimetype="application/pdf", as_attachment=True, download_name=name + ".pdf")
    abort(404)
