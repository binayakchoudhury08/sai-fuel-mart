import json
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import db
from ..security import (assert_writable, audit, can, can_unlock, claim_form_token, current_user, login_required,
                        managers_edit_history, role)
from ..services import (DENOMINATIONS, EXPENSE_CATEGORIES, PAY_MODES, compute_day, lube_stock,
                        party_balances, rates_for, settings_map, snapshot_of, fuels)
from ..util import ZERO, UserError, clean, money, parse_date, to_dec, today

bp = Blueprint("day", __name__)

MAX_DAY_QTY = Decimal("50000")  # no single nozzle sells 50,000 L in a day: catches typos


@bp.route("/day")
@login_required()
def day_today():
    return redirect(url_for("day.day_view", d=request.args.get("d") or today().isoformat()))


@bp.route("/day/<d>")
@login_required()
def day_view(d):
    d = parse_date(d)
    with db.tx() as cur:
        t = compute_day(cur, d)
        parties = [p for p in party_balances(cur, active_only=True)]
        products = [p for p in lube_stock(cur) if p["active"]]
        cur.execute("SELECT id, name, emp_code FROM staff WHERE active ORDER BY name")
        staff = cur.fetchall()
        rates = rates_for(cur, d)
        fl = fuels(cur)
        st = settings_map(cur)
        may_lock = can_unlock(cur)
    editable = _editable(d, t["status"], st)
    return render_template("day/day.html", t=t, d=d, parties=parties, products=products, rates=rates, staff=staff,
                           fuels=fl, editable=editable, PAY_MODES=PAY_MODES,
                           DENOMINATIONS=DENOMINATIONS, EXPENSE_CATEGORIES=EXPENSE_CATEGORIES,
                           prev_day=d - timedelta(days=1),
                           next_day=d + timedelta(days=1) if d < today() else None,
                           tolerance=Decimal(st.get("cash_tolerance") or "0"), may_lock=may_lock)


def _editable(d, status, st=None):
    """Same rules as assert_writable, evaluated for display only."""
    if d > today() or status == "locked":
        return False
    if status == "submitted" and not can("cancel_entries"):
        return False
    if not can("day_reopen"):
        try:
            back = int((st or {}).get("backdate_days") or 2)
        except (TypeError, ValueError):
            back = 2
        if d < today() - timedelta(days=back):
            return False
    return True


def _touch_day(cur, d):
    cur.execute("INSERT INTO days (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
    cur.execute("UPDATE days SET updated_at=NOW() WHERE business_date=%s", (d,))


# ------------------------------------------------------------------ readings
@bp.route("/day/<d>/readings", methods=["POST"])
@login_required("closing_enter")
def save_readings(d):
    d = parse_date(d)
    changed = 0
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        _touch_day(cur, d)
        rates = rates_for(cur, d)
        cur.execute("SELECT * FROM nozzles WHERE active ORDER BY id")
        nozzles = cur.fetchall()
        cur.execute("""SELECT DISTINCT ON (nozzle_id) nozzle_id, closing FROM nozzle_readings
                       WHERE business_date < %s ORDER BY nozzle_id, business_date DESC""", (d,))
        prev = {r["nozzle_id"]: Decimal(r["closing"]) for r in cur.fetchall()}
        cur.execute("SELECT * FROM nozzle_readings WHERE business_date=%s", (d,))
        saved = {r["nozzle_id"]: r for r in cur.fetchall()}

        for n in nozzles:
            nid = n["id"]
            raw_close = request.form.get(f"closing_{nid}", "").strip()
            if raw_close == "":
                continue
            closing = to_dec(raw_close, f"{n['name']} closing")
            testing = to_dec(request.form.get(f"testing_{nid}"), f"{n['name']} testing")
            auto_open = prev.get(nid)
            old = saved.get(nid)
            reason = None
            raw_open = request.form.get(f"opening_{nid}", "").strip()

            if auto_open is None:
                # very first reading for this nozzle: opening must be typed once
                if old:
                    opening = Decimal(old["opening"])
                    if raw_open and to_dec(raw_open) != opening:
                        if not can("meter_override"):
                            raise UserError(f"{n['name']}: only the owner can change the first opening reading.")
                        opening = to_dec(raw_open, f"{n['name']} opening")
                        reason = clean(request.form.get(f"reason_{nid}"), 200) or "Owner corrected first reading"
                else:
                    if not raw_open:
                        raise UserError(f"{n['name']}: enter the opening reading (first day for this nozzle).")
                    opening = to_dec(raw_open, f"{n['name']} opening")
                    reason = "First reading for this nozzle"
            else:
                opening = auto_open
                if raw_open and to_dec(raw_open) != auto_open:
                    if not can("meter_override"):
                        raise UserError(f"{n['name']}: opening comes from the previous closing "
                                        f"({auto_open}). Only the owner can override it.")
                    reason = clean(request.form.get(f"reason_{nid}"), 200)
                    if not reason:
                        raise UserError(f"{n['name']}: give a reason for changing the opening reading "
                                        "(e.g. meter replaced, reset).")
                    opening = to_dec(raw_open, f"{n['name']} opening")
                elif old and old["opening_override_reason"] and can("meter_override") and raw_open:
                    reason = old["opening_override_reason"] if Decimal(old["opening"]) == opening else None

            if closing < opening:
                raise UserError(f"{n['name']}: closing {closing} is less than opening {opening}. "
                                "Check the meter reading.")
            if closing - opening > MAX_DAY_QTY:
                raise UserError(f"{n['name']}: {closing - opening} in one day looks like a typing mistake.")
            if testing > closing - opening:
                raise UserError(f"{n['name']}: testing ({testing}) is more than the meter movement.")
            rate = rates.get(n["fuel_code"])
            if not rate:
                raise UserError(f"No {n['fuel_code']} rate is set for {d.strftime('%d %b %Y')}. "
                                "The owner can add it in Settings → Fuel rates.")
            if old and (Decimal(old["opening"]), Decimal(old["closing"]), Decimal(old["testing"]),
                        Decimal(old["rate"])) == (opening, closing, testing, rate):
                continue
            cur.execute("""
                INSERT INTO nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate,
                                             opening_override_reason, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (business_date, nozzle_id) DO UPDATE SET
                    opening=EXCLUDED.opening, closing=EXCLUDED.closing, testing=EXCLUDED.testing,
                    rate=EXCLUDED.rate, opening_override_reason=EXCLUDED.opening_override_reason,
                    updated_by=EXCLUDED.updated_by, updated_at=NOW()
                RETURNING *
            """, (d, nid, opening, closing, testing, rate, reason, current_user()["username"]))
            new = cur.fetchone()
            audit(cur, "update" if old else "create", "nozzle_reading", new["id"],
                  f"{n['name']}: {opening} → {closing} (test {testing}) = "
                  f"{closing - opening - testing} @ ₹{rate}" + (f" | opening override: {reason}" if reason and auto_open is not None else ""),
                  before=old, after=new, business_date=d)
            changed += 1
    flash(f"Meter readings saved ({changed} changed)." if changed else "No reading changed.",
          "success" if changed else "info")
    return redirect(url_for("day.day_view", d=d.isoformat()) + "#readings")


# ------------------------------------------------------------------ collections + cash count
@bp.route("/day/<d>/cash", methods=["POST"])
@login_required("closing_enter")
def save_cash(d):
    d = parse_date(d)
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        _touch_day(cur, d)
        cur.execute("SELECT * FROM collection_modes ORDER BY id")
        modes = cur.fetchall()
        cur.execute("SELECT * FROM day_collections WHERE business_date=%s", (d,))
        old = {r["mode_id"]: r for r in cur.fetchall()}
        changes = []
        for m in modes:
            key = f"mode_{m['id']}"
            if key not in request.form:
                continue
            amt = to_dec(request.form.get(key), m["name"])
            prev = Decimal(old[m["id"]]["amount"]) if m["id"] in old else ZERO
            if amt == prev:
                continue
            cur.execute("""
                INSERT INTO day_collections (business_date, mode_id, amount, updated_by)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (business_date, mode_id) DO UPDATE
                SET amount=EXCLUDED.amount, updated_by=EXCLUDED.updated_by, updated_at=NOW()
            """, (d, m["id"], amt, current_user()["username"]))
            changes.append(f"{m['name']} {prev} → {amt}")

        cur.execute("SELECT counted_cash, denominations, notes FROM days WHERE business_date=%s", (d,))
        day_old = cur.fetchone()
        denoms = {}
        any_denom = False
        for v in DENOMINATIONS:
            raw = request.form.get(f"den_{v}", "").strip()
            if raw:
                any_denom = True
                try:
                    n = int(raw)
                except ValueError:
                    raise UserError(f"₹{v} notes: enter a whole number.")
                if n < 0:
                    raise UserError(f"₹{v} notes cannot be negative.")
                denoms[str(v)] = n
        coins = to_dec(request.form.get("den_coins"), "Loose coins")
        if any_denom or coins:
            counted = money(sum((Decimal(int(k)) * n for k, n in denoms.items()), ZERO) + coins)
            if coins:
                denoms["coins"] = str(coins)
        else:
            raw = request.form.get("counted_cash", "").strip()
            counted = to_dec(raw, "Cash counted") if raw else None
            denoms = None
        notes = clean(request.form.get("notes"), 1000)
        if (day_old["counted_cash"] != counted or (day_old["notes"] or "") != notes
                or (day_old["denominations"] or None) != (denoms or None)):
            cur.execute("UPDATE days SET counted_cash=%s, denominations=%s, notes=%s WHERE business_date=%s",
                        (counted, json.dumps(denoms) if denoms else None, notes, d))
            if day_old["counted_cash"] != counted:
                changes.append(f"Cash counted {day_old['counted_cash']} → {counted}")
            if (day_old["notes"] or "") != notes:
                changes.append("Notes changed")
        if changes:
            audit(cur, "update", "day_cash", d.isoformat(), "; ".join(changes), business_date=d)
    flash("Collections and cash count saved." if changes else "Nothing changed.",
          "success" if changes else "info")
    return redirect(url_for("day.day_view", d=d.isoformat()) + "#cash")


# ------------------------------------------------------------------ credit totals per transporter (reconciled)
@bp.route("/day/<d>/credit", methods=["POST"])
@login_required("closing_enter")
def save_credit(d):
    """Closing person's total credit per transporter for the day. It must match Credit transport."""
    d = parse_date(d)
    changes = []
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        _touch_day(cur, d)
        cur.execute("SELECT party_id, amount FROM day_credit WHERE business_date=%s", (d,))
        old = {r["party_id"]: Decimal(r["amount"]) for r in cur.fetchall()}
        cur.execute("SELECT id, name FROM parties")
        names = {r["id"]: r["name"] for r in cur.fetchall()}
        wanted = {}
        for k, v in request.form.items():
            if k.startswith("credit_") and k[7:].isdigit() and v.strip() != "":
                wanted[int(k[7:])] = to_dec(v, f"Credit for {names.get(int(k[7:]), 'party')}")
        if request.form.get("new_party") and request.form.get("new_amount", "").strip():
            pid = int(request.form["new_party"])
            wanted[pid] = to_dec(request.form["new_amount"], f"Credit for {names.get(pid, 'party')}")
        cleared = {int(k[7:]) for k, v in request.form.items() if k.startswith("credit_") and k[7:].isdigit() and v.strip() == ""}
        for pid, amt in wanted.items():
            if pid not in names:
                raise UserError("Choose a valid transporter.")
            if old.get(pid) != amt:
                cur.execute("""INSERT INTO day_credit (business_date, party_id, amount, updated_by) VALUES (%s,%s,%s,%s)
                               ON CONFLICT (business_date, party_id) DO UPDATE SET amount=EXCLUDED.amount,
                               updated_by=EXCLUDED.updated_by, updated_at=NOW()""", (d, pid, amt, current_user()["username"]))
                changes.append(f"{names[pid]} {old.get(pid, '–')} → {amt}")
        for pid in cleared & set(old):
            cur.execute("DELETE FROM day_credit WHERE business_date=%s AND party_id=%s", (d, pid))
            changes.append(f"{names[pid]} {old[pid]} → removed")
        if changes:
            audit(cur, "update", "day_credit", d.isoformat(), "Closing credit totals: " + "; ".join(changes), business_date=d)
        t = compute_day(cur, d)
    bad = [r for r in t["recon"] if not r["ok"]]
    if not changes:
        flash("Nothing changed.", "info")
    elif bad:
        flash(f"Saved. {len(bad)} transporter(s) do not match Credit transport yet — see the red rows.", "error")
    else:
        flash("Saved. Every transporter matches Credit transport ✓", "success")
    return redirect(url_for("day.day_view", d=d.isoformat()) + "#credit")


# ------------------------------------------------------------------ expenses
@bp.route("/day/<d>/expense", methods=["POST"])
@login_required("closing_enter")
def add_expense(d):
    d = parse_date(d)
    amount = to_dec(request.form.get("amount"), "Expense amount", allow_blank=False)
    if amount <= 0:
        raise UserError("Expense amount must be more than zero.")
    desc = clean(request.form.get("description"), 200)
    if not desc:
        raise UserError("Write what the expense was for.")
    cat = clean(request.form.get("category"), 40) or "General"
    from_counter = request.form.get("from_counter", "1") == "1"
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        _touch_day(cur, d)
        cur.execute("""INSERT INTO expenses (business_date, category, description, amount, from_counter, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (d, cat, desc, amount, from_counter, current_user()["username"]))
        e = cur.fetchone()
        audit(cur, "create", "expense", e["id"], f"Expense ₹{amount} – {cat}: {desc}", after=e, business_date=d)
    flash(f"Expense of ₹{amount} added.", "success")
    return redirect(url_for("day.day_view", d=d.isoformat()) + "#expenses")


@bp.route("/expense/<int:eid>/void", methods=["POST"])
@login_required("cancel_entries")
def void_expense(eid):
    reason = clean(request.form.get("reason"), 200)
    if not reason:
        raise UserError("Give a reason for cancelling this expense.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM expenses WHERE id=%s FOR UPDATE", (eid,))
        e = cur.fetchone()
        if not e or e["voided"]:
            raise UserError("Expense not found or already cancelled.")
        assert_writable(cur, e["business_date"])
        cur.execute("""UPDATE expenses SET voided=TRUE, void_reason=%s, voided_by=%s, voided_at=NOW()
                       WHERE id=%s RETURNING *""", (reason, current_user()["username"], eid))
        audit(cur, "void", "expense", eid, f"Cancelled expense ₹{e['amount']} ({e['description']}): {reason}",
              before=e, after=cur.fetchone(), business_date=e["business_date"])
    flash("Expense cancelled. It stays visible, struck through.", "success")
    return redirect(url_for("day.day_view", d=e["business_date"].isoformat()) + "#expenses")


# ------------------------------------------------------------------ lube counter sale
@bp.route("/day/<d>/lube-sale", methods=["POST"])
@login_required("closing_enter")
def lube_sale(d):
    d = parse_date(d)
    try:
        pid = int(request.form.get("product_id") or 0)
    except ValueError:
        pid = 0
    q = to_dec(request.form.get("qty"), "Quantity", allow_blank=False)
    if q <= 0:
        raise UserError("Quantity must be more than zero.")
    mode = request.form.get("pay_mode", "Cash")
    if mode not in ("Cash", "Credit"):
        raise UserError("Choose cash or credit.")
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        _touch_day(cur, d)
        cur.execute("SELECT * FROM lube_products WHERE id=%s AND active FOR UPDATE", (pid,))
        p = cur.fetchone()
        if not p:
            raise UserError("Choose a lube product.")
        rate = Decimal(p["sale_rate"])
        if request.form.get("rate") and can("rate_override"):
            rate = to_dec(request.form.get("rate"), "Rate")
        if rate <= 0:
            raise UserError(f"{p['name']} has no selling rate. Set it in Lube stock.")
        cur.execute("SELECT COALESCE(SUM(qty),0) AS s FROM lube_moves WHERE product_id=%s AND NOT voided", (pid,))
        stock = Decimal(cur.fetchone()["s"])
        if q > stock and request.form.get("add_stock") == "1":
            # stock is in the office but its purchase was not entered yet: add exactly what is missing
            if not can("lube_stock"):
                raise UserError("You are not allowed to add stock. Ask them, or record the purchase in Lube stock.")
            add = q - stock
            bill = clean(request.form.get("stock_bill"), 60)
            cur.execute("""INSERT INTO lube_moves (product_id, business_date, kind, qty, rate, amount, remarks, created_by)
                           VALUES (%s,%s,'purchase',%s,0,0,%s,%s) RETURNING id""",
                        (pid, d, add, ("Stock added from daily closing" + (f" – bill {bill}" if bill else "")
                                       + " (purchase details to be completed)"), current_user()["username"]))
            audit(cur, "create", "lube_move", cur.fetchone()["id"],
                  f"Added {add} {p['unit']} of {p['name']} to stock from daily closing (stock was {stock})",
                  business_date=d)
            stock = q
        if q > stock:
            raise UserError(f"Only {stock} {p['unit']} of {p['name']} in stock. If it has arrived, tick "
                            "“Stock has arrived – add it” on the lube form, or use “Add stock that arrived”.")
        amount = money(q * rate)
        txn_id = None
        party_name = ""
        if mode == "Credit":
            try:
                party_id = int(request.form.get("party_id") or 0)
            except ValueError:
                party_id = 0
            cur.execute("SELECT * FROM parties WHERE id=%s AND active", (party_id,))
            party = cur.fetchone()
            if not party:
                raise UserError("Choose the party for a credit lube sale.")
            party_name = party["name"]
            cur.execute("""INSERT INTO party_txns (party_id, business_date, kind, lube_product_id, qty, rate,
                                                   debit, vehicle_no, remarks, created_by)
                           VALUES (%s,%s,'lube',%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (party_id, d, pid, q, rate, amount, clean(request.form.get("vehicle_no"), 20).upper(),
                         p["name"], current_user()["username"]))
            txn_id = cur.fetchone()["id"]
        cur.execute("""INSERT INTO lube_moves (product_id, business_date, kind, qty, rate, amount, pay_mode,
                                               party_txn_id, created_by)
                       VALUES (%s,%s,'sale',%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (pid, d, -q, rate, amount, mode, txn_id, current_user()["username"]))
        m = cur.fetchone()
        audit(cur, "create", "lube_sale", m["id"],
              f"Sold {q} {p['name']} @ ₹{rate} = ₹{amount} ({mode}{' – ' + party_name if party_name else ''})",
              after=m, business_date=d)
    flash(f"Lube sale ₹{amount} recorded.", "success")
    return redirect(request.form.get("_back") or (url_for("day.day_view", d=d.isoformat()) + "#lube"))


@bp.route("/lube-move/<int:mid>/void", methods=["POST"])
@login_required("cancel_entries")
def void_lube_move(mid):
    reason = clean(request.form.get("reason"), 200)
    if not reason:
        raise UserError("Give a reason for cancelling.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM lube_moves WHERE id=%s FOR UPDATE", (mid,))
        m = cur.fetchone()
        if not m or m["voided"]:
            raise UserError("Entry not found or already cancelled.")
        assert_writable(cur, m["business_date"])
        if m["kind"] in ("purchase", "opening", "adjust") and Decimal(m["qty"]) > 0:
            cur.execute("SELECT COALESCE(SUM(qty),0) AS s FROM lube_moves WHERE product_id=%s AND NOT voided",
                        (m["product_id"],))
            if Decimal(cur.fetchone()["s"]) - Decimal(m["qty"]) < 0:
                raise UserError("Cancelling this would make stock negative. Cancel the later sales first.")
        cur.execute("""UPDATE lube_moves SET voided=TRUE, void_reason=%s, voided_by=%s, voided_at=NOW()
                       WHERE id=%s""", (reason, current_user()["username"], mid))
        if m["party_txn_id"]:
            cur.execute("""UPDATE party_txns SET voided=TRUE, void_reason=%s, voided_by=%s, voided_at=NOW()
                           WHERE id=%s""", (f"Lube sale cancelled: {reason}", current_user()["username"],
                                            m["party_txn_id"]))
        audit(cur, "void", "lube_move", mid, f"Cancelled lube {m['kind']} of {abs(m['qty'])} (₹{m['amount']}): {reason}",
              before=m, business_date=m["business_date"])
    flash("Lube entry cancelled; stock" + (" and party balance" if m["party_txn_id"] else "") + " restored.",
          "success")
    return redirect(request.form.get("_back") or url_for("day.day_view", d=m["business_date"].isoformat()))


# ------------------------------------------------------------------ workflow
@bp.route("/day/<d>/submit", methods=["POST"])
@login_required("closing_submit")
def submit(d):
    d = parse_date(d)
    with db.tx() as cur:
        assert_writable(cur, d)
        t = compute_day(cur, d)
        missing = [r["nozzle"]["name"] for r in t["nozzles"] if r["nozzle"]["active"] and not r["saved"]]
        if missing:
            raise UserError("Enter closing readings first: " + ", ".join(missing))
        if t["counted_cash"] is None:
            raise UserError("Count the cash and enter it before submitting.")
        if not t["recon_ok"]:
            bad = [r for r in t["recon"] if not r["ok"]]
            raise UserError("Credit does not match Credit transport for " + ", ".join(
                f"{r['name']} (closing {r['declared'] if r['declared'] is not None else 'not entered'}, slips {r['register']})"
                for r in bad) + ". Fix the slip entries or the closing amount first.")
        _touch_day(cur, d)
        snap = snapshot_of(t)
        cur.execute("""UPDATE days SET status='submitted', snapshot=%s, submitted_by=%s, submitted_at=NOW()
                       WHERE business_date=%s""", (json.dumps(snap), current_user()["username"], d))
        audit(cur, "submit", "day", d.isoformat(),
              f"Submitted day: sale ₹{t['gross_sale']}, expected cash ₹{t['expected_cash']}, "
              f"counted ₹{t['counted_cash']}, difference ₹{t['difference']}", after=snap, business_date=d)
    flash("Day submitted. The owner can now verify and lock it.", "success")
    return redirect(url_for("day.day_view", d=d.isoformat()))


@bp.route("/day/<d>/lock", methods=["POST"])
@login_required("day_lock")
def lock(d):
    d = parse_date(d)
    with db.tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"day:{d.isoformat()}",))
        cur.execute("SELECT is_legacy, status FROM days WHERE business_date=%s", (d,))
        lg = cur.fetchone()
        if lg and lg["is_legacy"] and lg["status"] != "locked":
            # imported days have no cash count from the new system; lock them back as they were
            cur.execute("UPDATE days SET status='locked', locked_by=%s, locked_at=NOW() WHERE business_date=%s",
                        (current_user()["username"], d))
            audit(cur, "lock", "day", d.isoformat(), "Imported day locked again after editing", business_date=d)
            flash(f"{d.strftime('%d %b %Y')} locked again.", "success")
            return redirect(url_for("day.day_view", d=d.isoformat()))
        t = compute_day(cur, d)
        if t["status"] == "locked":
            raise UserError("Already locked.")
        missing = [r["nozzle"]["name"] for r in t["nozzles"] if r["nozzle"]["active"] and not r["saved"]]
        if missing:
            raise UserError("Cannot lock: readings missing for " + ", ".join(missing))
        if t["counted_cash"] is None:
            raise UserError("Cannot lock: cash counted is not entered.")
        if not t["recon_ok"]:
            bad = [r for r in t["recon"] if not r["ok"]]
            raise UserError("Credit does not match Credit transport for " + ", ".join(
                f"{r['name']} (closing {r['declared'] if r['declared'] is not None else 'not entered'}, slips {r['register']})"
                for r in bad) + ". Fix the slip entries or the closing amount first.")
        _touch_day(cur, d)
        snap = snapshot_of(t)
        cur.execute("""UPDATE days SET status='locked', snapshot=%s, locked_by=%s, locked_at=NOW()
                       WHERE business_date=%s""", (json.dumps(snap), current_user()["username"], d))
        audit(cur, "lock", "day", d.isoformat(), f"Verified and locked. Difference ₹{t['difference']}",
              after=snap, business_date=d)
    flash(f"{d.strftime('%d %b %Y')} verified and locked. No one can change it now.", "success")
    return redirect(url_for("day.day_view", d=d.isoformat()))


@bp.route("/day/<d>/unlock", methods=["POST"])
@login_required("day_reopen")
def unlock(d):
    d = parse_date(d)
    reason = clean(request.form.get("reason"), 300)
    if not reason:
        raise UserError("Write why the day is being unlocked. It is kept in the audit log.")
    with db.tx() as cur:
        if not can_unlock(cur):
            raise UserError("Only the owner can reopen a locked day. The owner can allow managers in "
                            "Settings → Old data editing.")
        cur.execute("SELECT * FROM days WHERE business_date=%s FOR UPDATE", (d,))
        day = cur.fetchone()
        if not day or day["status"] == "open":
            raise UserError("Day is already open.")
        cur.execute("UPDATE days SET status='open' WHERE business_date=%s", (d,))
        audit(cur, "unlock", "day", d.isoformat(), f"Unlocked ({day['status']} → open): {reason}",
              before={"status": day["status"], "snapshot": day["snapshot"]}, business_date=d)
    flash("Day reopened for changes. The unlock and reason are in the audit log.", "success")
    return redirect(url_for("day.day_view", d=d.isoformat()))


@bp.route("/day/<d>/print")
@login_required()
def print_day(d):
    d = parse_date(d)
    with db.tx() as cur:
        t = compute_day(cur, d)
        st = settings_map(cur)
    return render_template("day/print.html", t=t, d=d, st=st, PAY_MODES=PAY_MODES)
