from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from .. import db
from ..exports import party_bill_pdf, party_bill_xlsx
from ..security import (assert_writable, audit, can, claim_form_token, current_user, login_required,
                        next_receipt_no, role)
from ..services import (PARTY_KIND_LABEL, PAY_MODES, fuels, party_balance, party_balances,
                        party_statement, party_statement_page, rates_for, settings_map)
from ..util import ZERO, UserError, clean, inr, money, parse_date, to_dec, today


def _deliver_compat(buf, mimetype=None, as_attachment=True, download_name=None, **kw):
    from ..storage import deliver
    return deliver(buf, download_name, mimetype)


bp = Blueprint("parties", __name__)


@bp.route("/parties")
@login_required("accounts_view")
def index():
    with db.tx() as cur:
        rows = party_balances(cur)
    total_due = sum((Decimal(r["balance"]) for r in rows if Decimal(r["balance"]) > 0), ZERO)
    advance = sum((-Decimal(r["balance"]) for r in rows if Decimal(r["balance"]) < 0), ZERO)
    return render_template("parties/index.html", rows=rows, total_due=total_due, advance=advance)


def _party_form():
    name = clean(request.form.get("name"), 100)
    if not name:
        raise UserError("Party name is required.")
    mobile = "".join(ch for ch in request.form.get("mobile", "") if ch.isdigit())[-10:]
    if mobile and len(mobile) != 10:
        raise UserError("Mobile number should have 10 digits.")
    return {
        "name": name, "mobile": mobile,
        "vehicles": clean(request.form.get("vehicles"), 400).upper(),
        "address": clean(request.form.get("address"), 300),
        "gstin": clean(request.form.get("gstin"), 15).upper(),
        "credit_limit": to_dec(request.form.get("credit_limit"), "Credit limit"),
    }


@bp.route("/parties/new", methods=["POST"])
@login_required("parties_manage")
def new():
    f = _party_form()
    opening = to_dec(request.form.get("opening_balance"), "Opening balance", allow_negative=True)
    od = parse_date(request.form.get("opening_date") or today().isoformat(), "Opening date")
    with db.tx() as cur:
        claim_form_token(cur)
        cur.execute("SELECT id FROM parties WHERE LOWER(TRIM(name))=LOWER(TRIM(%s))", (f["name"],))
        if cur.fetchone():
            raise UserError(f"A party named '{f['name']}' already exists.")
        cur.execute("""INSERT INTO parties (name, mobile, vehicles, address, gstin, credit_limit)
                       VALUES (%(name)s,%(mobile)s,%(vehicles)s,%(address)s,%(gstin)s,%(credit_limit)s)
                       RETURNING *""", f)
        p = cur.fetchone()
        if opening:
            assert_writable(cur, od)
            cur.execute("""INSERT INTO party_txns (party_id, business_date, kind, debit, credit, remarks, created_by)
                           VALUES (%s,%s,'opening',%s,%s,'Opening balance',%s)""",
                        (p["id"], od, max(opening, ZERO), max(-opening, ZERO), current_user()["username"]))
        audit(cur, "create", "party", p["id"], f"Added party '{p['name']}', opening ₹{opening}", after=p)
    flash(f"Party '{f['name']}' added.", "success")
    return redirect(url_for("parties.detail", pid=p["id"]))


@bp.route("/parties/<int:pid>/edit", methods=["POST"])
@login_required("parties_manage")
def edit(pid):
    f = _party_form()
    f["active"] = request.form.get("active") == "1"
    f["id"] = pid
    if "discount_per_litre" in request.form and can("parties_manage"):
        f["dpl"] = to_dec(request.form.get("discount_per_litre"), "Discount per litre")
    else:
        f["dpl"] = None
    with db.tx() as cur:
        cur.execute("SELECT * FROM parties WHERE id=%s FOR UPDATE", (pid,))
        old = cur.fetchone()
        if not old:
            abort(404)
        cur.execute("SELECT id FROM parties WHERE LOWER(TRIM(name))=LOWER(TRIM(%s)) AND id<>%s", (f["name"], pid))
        if cur.fetchone():
            raise UserError(f"Another party is already named '{f['name']}'.")
        cur.execute("""UPDATE parties SET name=%(name)s, mobile=%(mobile)s, vehicles=%(vehicles)s,
                       address=%(address)s, gstin=%(gstin)s, credit_limit=%(credit_limit)s, active=%(active)s,
                       discount_per_litre=COALESCE(%(dpl)s, discount_per_litre)
                       WHERE id=%(id)s RETURNING *""", f)
        audit(cur, "update", "party", pid, f"Edited party '{f['name']}'", before=old, after=cur.fetchone())
    flash("Party details saved.", "success")
    return redirect(url_for("parties.detail", pid=pid))


def _period():
    t = today()
    start = request.args.get("from")
    end = request.args.get("to")
    start = parse_date(start, "From date") if start else t.replace(day=1)
    end = parse_date(end, "To date") if end else t
    if end < start:
        start, end = end, start
    return start, end


@bp.route("/parties/<int:pid>")
@login_required("accounts_view")
def detail(pid):
    start, end = _period()
    with db.tx() as cur:
        cur.execute("SELECT * FROM parties WHERE id=%s", (pid,))
        p = cur.fetchone()
        if not p:
            abort(404)
        try:
            pg = int(request.args["page"]) if request.args.get("page") else None
        except ValueError:
            pg = None
        stmt = party_statement_page(cur, pid, start, end, page=pg)
        balance = party_balance(cur, pid)
        rates = rates_for(cur, today())
        fl = fuels(cur)
        st = settings_map(cur)
    wa = _whatsapp_link(p, balance, st)
    return render_template("parties/detail.html", p=p, stmt=stmt, balance=balance, start=start, end=end,
                           rates=rates, fuels=fl, PAY_MODES=PAY_MODES, KIND=PARTY_KIND_LABEL, wa=wa)


def _whatsapp_link(p, balance, st):
    if not p["mobile"]:
        return None
    msg = (f"Dear {p['name']},\nYour outstanding balance with {st.get('station_name', 'us')} as on "
           f"{today().strftime('%d %b %Y')} is {inr(balance)}.\nThank you.")
    return f"https://wa.me/91{p['mobile']}?text={quote(msg)}"


# ------------------------------------------------------------------ entries
@bp.route("/parties/<int:pid>/txn", methods=["POST"])
@login_required("payments")
def add_txn(pid):
    kind = request.form.get("kind")
    if kind in ("fuel", "cash_advance"):
        from .transport import add_entry
        return add_entry.__wrapped__(party_id=pid)
    if kind not in ("fuel", "cash_advance", "payment", "discount", "adjustment"):
        raise UserError("Unknown entry type.")
    if kind == "discount" and not can("parties_manage"):
        abort(403)
    if kind == "adjustment" and not can("adjustments"):
        raise UserError("You are not allowed to post balance adjustments.")
    d = parse_date(request.form.get("business_date") or today().isoformat())
    user = current_user()["username"]
    receipt = None
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        cur.execute("SELECT * FROM parties WHERE id=%s FOR UPDATE", (pid,))
        p = cur.fetchone()
        if not p:
            abort(404)
        if not p["active"] and kind in ("fuel", "cash_advance"):
            raise UserError(f"{p['name']} is marked inactive. Activate the party before giving credit.")
        cur.execute("INSERT INTO days (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
        row = {"party_id": pid, "business_date": d, "kind": kind, "fuel_code": None, "qty": ZERO,
               "rate": ZERO, "debit": ZERO, "credit": ZERO, "pay_mode": None,
               "reference": clean(request.form.get("reference"), 60),
               "vehicle_no": clean(request.form.get("vehicle_no"), 20).upper().replace(" ", ""),
               "challan_no": clean(request.form.get("challan_no"), 30),
               "slip_no": clean(request.form.get("slip_no"), 30),
               "remarks": clean(request.form.get("remarks"), 200), "receipt_no": None, "pay_for": None,
               "created_by": user}

        if kind == "fuel":
            code = request.form.get("fuel_code")
            rates = rates_for(cur, d)
            if code not in rates or not rates[code]:
                raise UserError(f"No rate set for {code or 'that fuel'} on {d.strftime('%d %b')}.")
            rate = rates[code]
            if request.form.get("rate") and can("rate_override"):
                rate = to_dec(request.form.get("rate"), "Rate")
                if rate <= 0:
                    raise UserError("Rate must be more than zero.")
            q = to_dec(request.form.get("qty"), "Quantity")
            amt = to_dec(request.form.get("amount"), "Amount")
            if q > 0:
                amt = money(q * rate)
            elif amt > 0:
                q = (amt / rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
            else:
                raise UserError("Enter litres or amount.")
            if q > 20000:
                raise UserError("That quantity looks like a typing mistake.")
            row.update(fuel_code=code, qty=q, rate=rate, debit=amt)
        elif kind == "cash_advance":
            amt = to_dec(request.form.get("amount"), "Cash amount", allow_blank=False)
            if amt <= 0:
                raise UserError("Cash amount must be more than zero.")
            row.update(debit=amt)
        elif kind == "payment":
            amt = to_dec(request.form.get("amount"), "Amount received", allow_blank=False)
            if amt <= 0:
                raise UserError("Amount received must be more than zero.")
            mode = request.form.get("pay_mode", "Cash")
            if mode not in PAY_MODES:
                raise UserError("Choose how the payment was received.")
            if mode in ("Bank transfer", "Cheque") and not row["reference"]:
                raise UserError("Enter the UTR / cheque number for bank and cheque payments.")
            receipt = next_receipt_no(cur, d)
            pay_for = request.form.get("pay_for") if request.form.get("pay_for") in ("Fuel", "Lube", "Fuel + Lube") else None
            row.update(credit=amt, pay_mode=mode, receipt_no=receipt, pay_for=pay_for)
        elif kind == "discount":
            amt = to_dec(request.form.get("amount"), "Discount", allow_blank=False)
            if amt <= 0:
                raise UserError("Discount must be more than zero.")
            if not row["remarks"]:
                raise UserError("Write why the discount is given.")
            row.update(credit=amt)
        elif kind == "adjustment":
            amt = to_dec(request.form.get("amount"), "Amount", allow_blank=False)
            if amt <= 0:
                raise UserError("Amount must be more than zero.")
            if not row["remarks"]:
                raise UserError("Write why this adjustment is needed.")
            if request.form.get("direction") == "increase":
                row.update(debit=amt)
            else:
                row.update(credit=amt)

        if row["debit"] > 0 and Decimal(p["credit_limit"]) > 0:
            new_bal = party_balance(cur, pid) + row["debit"]
            if new_bal > Decimal(p["credit_limit"]) and not can("credit_limit_override"):
                raise UserError(f"Credit limit crossed: {p['name']} would owe {inr(new_bal)} "
                                f"(limit {inr(p['credit_limit'])}). Only the owner can allow this.")

        cur.execute("""INSERT INTO party_txns (party_id, business_date, kind, fuel_code, qty, rate, debit, credit,
                                               pay_mode, reference, vehicle_no, challan_no, slip_no, remarks,
                                               receipt_no, pay_for, created_by)
                       VALUES (%(party_id)s,%(business_date)s,%(kind)s,%(fuel_code)s,%(qty)s,%(rate)s,%(debit)s,
                               %(credit)s,%(pay_mode)s,%(reference)s,%(vehicle_no)s,%(challan_no)s,%(slip_no)s,
                               %(remarks)s,%(receipt_no)s,%(pay_for)s,%(created_by)s) RETURNING *""", row)
        t = cur.fetchone()
        amount = t["debit"] or t["credit"]
        audit(cur, "create", "party_txn", t["id"],
              f"{PARTY_KIND_LABEL[kind]} ₹{amount} – {p['name']}"
              + (f" ({t['qty']} {t['fuel_code']} @ ₹{t['rate']})" if kind == "fuel" else "")
              + (f" via {t['pay_mode']} #{receipt}" if receipt else ""),
              after=t, business_date=d)
    if receipt:
        flash(f"Payment of {inr(amount)} recorded. Receipt {receipt}.", "success")
        if request.form.get("print_receipt"):
            return redirect(url_for("parties.receipt", tid=t["id"]))
    else:
        flash(f"{PARTY_KIND_LABEL[kind]} of {inr(amount)} recorded for {p['name']}.", "success")
    back = request.form.get("_back")
    if back and back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect(url_for("parties.detail", pid=pid))


@bp.route("/parties/txn", methods=["POST"])
@login_required("payments")
def add_txn_any():
    """Quick-add from the Daily closing page, where the party is chosen in the form."""
    try:
        pid = int(request.form.get("party") or 0)
    except ValueError:
        pid = 0
    if not pid:
        raise UserError("Choose the party.")
    if request.form.get("kind") in ("fuel", "cash_advance"):
        from .transport import add_entry
        return add_entry.__wrapped__()
    return add_txn.__wrapped__(pid)


@bp.route("/party-txn/<int:tid>/void", methods=["POST"])
@login_required("cancel_entries")
def void_txn(tid):
    reason = clean(request.form.get("reason"), 200)
    if not reason:
        raise UserError("Give a reason for cancelling this entry.")
    with db.tx() as cur:
        cur.execute("SELECT t.*, p.name AS party_name FROM party_txns t JOIN parties p ON p.id=t.party_id "
                    "WHERE t.id=%s FOR UPDATE OF t", (tid,))
        t = cur.fetchone()
        if not t or t["voided"]:
            raise UserError("Entry not found or already cancelled.")
        if t["kind"] == "opening" and not can("adjustments"):
            raise UserError("Only the owner can cancel an opening balance.")
        assert_writable(cur, t["business_date"])
        cur.execute("""UPDATE party_txns SET voided=TRUE, void_reason=%s, voided_by=%s, voided_at=NOW()
                       WHERE id=%s""", (reason, current_user()["username"], tid))
        if t["kind"] == "lube":
            cur.execute("""UPDATE lube_moves SET voided=TRUE, void_reason=%s, voided_by=%s, voided_at=NOW()
                           WHERE party_txn_id=%s AND NOT voided""",
                        (f"Credit entry cancelled: {reason}", current_user()["username"], tid))
        audit(cur, "void", "party_txn", tid,
              f"Cancelled {PARTY_KIND_LABEL[t['kind']]} ₹{t['debit'] or t['credit']} – {t['party_name']}: {reason}",
              before=t, business_date=t["business_date"])
    flash("Entry cancelled. It stays on the statement, struck through, and no longer counts.", "success")
    back = request.form.get("_back")
    if back and back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect(url_for("parties.detail", pid=t["party_id"]))


@bp.route("/party-txn/<int:tid>/details", methods=["POST"])
@login_required("cancel_entries")
def edit_txn_details(tid):
    """Fix text fields (vehicle, challan...) without touching money.
    Amount mistakes are fixed by cancelling and re-entering."""
    with db.tx() as cur:
        cur.execute("SELECT * FROM party_txns WHERE id=%s FOR UPDATE", (tid,))
        t = cur.fetchone()
        if not t or t["voided"]:
            raise UserError("Entry not found or cancelled.")
        assert_writable(cur, t["business_date"])
        new = {"vehicle_no": clean(request.form.get("vehicle_no"), 20).upper().replace(" ", ""),
               "challan_no": clean(request.form.get("challan_no"), 30),
               "slip_no": clean(request.form.get("slip_no"), 30),
               "reference": clean(request.form.get("reference"), 60),
               "remarks": clean(request.form.get("remarks"), 200)}
        cur.execute("""UPDATE party_txns SET vehicle_no=%(vehicle_no)s, challan_no=%(challan_no)s,
                       slip_no=%(slip_no)s, reference=%(reference)s, remarks=%(remarks)s,
                       edited_by=%(u)s, edited_at=NOW() WHERE id=%(id)s RETURNING *""",
                    {**new, "u": current_user()["username"], "id": tid})
        audit(cur, "update", "party_txn", tid, "Edited entry details (no amount change)",
              before=t, after=cur.fetchone(), business_date=t["business_date"])
    flash("Details updated.", "success")
    return redirect(url_for("parties.detail", pid=t["party_id"]))


@bp.route("/party-txn/<int:tid>/receipt")
@login_required("payments")
def receipt(tid):
    with db.tx() as cur:
        cur.execute("SELECT t.*, p.name AS party_name, p.mobile FROM party_txns t JOIN parties p ON p.id=t.party_id "
                    "WHERE t.id=%s", (tid,))
        t = cur.fetchone()
        if not t or t["kind"] != "payment":
            abort(404)
        bal = party_balance(cur, t["party_id"])
        st = settings_map(cur)
    wa = None
    if t["mobile"] and not t["voided"]:
        msg = (f"Payment received: {inr(t['credit'])} from {t['party_name']} on "
               f"{t['business_date'].strftime('%d %b %Y')} ({t['pay_mode']}). Receipt {t['receipt_no']}.\n"
               f"Balance now: {inr(bal)}.\n– {st.get('station_name', '')}")
        wa = f"https://wa.me/91{t['mobile']}?text={quote(msg)}"
    return render_template("parties/receipt.html", t=t, bal=bal, st=st, wa=wa)


@bp.route("/parties/<int:pid>/bill.<fmt>")
@login_required("accounts_view")
def bill(pid, fmt):
    if fmt not in ("pdf", "xlsx"):
        abort(404)
    start, end = _period()
    with db.tx() as cur:
        cur.execute("SELECT * FROM parties WHERE id=%s", (pid,))
        p = cur.fetchone()
        if not p:
            abort(404)
        stmt = party_statement(cur, pid, start, end, include_voided=False)
        st = settings_map(cur)
    safe = "".join(c for c in p["name"] if c.isalnum() or c in " -_").strip() or "Party"
    name = f"{safe} {start.strftime('%d%m%y')}-{end.strftime('%d%m%y')}.{fmt}"
    if fmt == "pdf":
        data = party_bill_pdf(p, stmt, start, end, st)
        mime = "application/pdf"
    else:
        data = party_bill_xlsx(p, stmt, start, end, st)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return _deliver_compat(data, mimetype=mime, as_attachment=True, download_name=name)
