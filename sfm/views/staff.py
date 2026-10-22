import calendar
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import db
from ..security import assert_writable, audit, claim_form_token, current_user, login_required
from ..util import ZERO, UserError, clean, month_bounds, now_ist, parse_date, to_dec, today

bp = Blueprint("staff", __name__)

STATUS = [("P", "Present"), ("H", "Half day"), ("A", "Absent"), ("L", "Leave"), ("O", "Weekly off")]
STAFF_PAY_MODES = ["Cash from counter", "Cash by owner", "Bank transfer", "UPI"]


def attendance_rules(cur):
    """Owner's rules for photo attendance: how many past days and which hours."""
    from ..security import get_setting
    try:
        back = max(0, min(7, int(get_setting(cur, "attendance_back_days", "2") or 2)))
    except ValueError:
        back = 2
    start = get_setting(cur, "attendance_start", "05:00") or "05:00"
    end = get_setting(cur, "attendance_end", "23:59") or "23:59"
    return {"back": back, "start": start, "end": end}


def check_photo_rules(cur, d):
    """Raises UserError if a photo attendance for date d is not allowed right now (owner bypasses)."""
    from ..security import grant_for, role
    if role() == "owner" or grant_for(d, "attendance_photo"):
        return
    r = attendance_rules(cur)
    now = now_ist()
    if d > now.date():
        raise UserError("You cannot mark attendance for a future date.")
    if d < now.date() - timedelta(days=r["back"]):
        raise UserError(f"Attendance can only be marked for today and the previous {r['back']} day(s).")
    hm = now.strftime("%H:%M")
    if not (r["start"] <= hm <= r["end"]):
        raise UserError(f"Attendance can be marked only between {r['start']} and {r['end']}.")


@bp.route("/staff")
@login_required("attendance_photo|attendance_manual|staff_manage")
def index():
    d = parse_date(request.args.get("d") or today().isoformat())
    with db.tx() as cur:
        cur.execute("SELECT * FROM staff ORDER BY active DESC, name")
        staff = cur.fetchall()
        cur.execute("SELECT * FROM attendance WHERE work_date=%s", (d,))
        marks = {r["staff_id"]: r for r in cur.fetchall()}
        cur.execute("SELECT emp_code FROM staff WHERE emp_code ~ '^EMP[0-9]+$' "
                    "ORDER BY LENGTH(emp_code) DESC, emp_code DESC LIMIT 1")
        last = cur.fetchone()
        rules = attendance_rules(cur)
    from .proofs import is_mobile, storage_ready
    nxt = f"EMP{int(last['emp_code'][3:]) + 1:03d}" if last else "EMP001"
    active = [x for x in staff if x["active"]]
    present = sum(1 for x in active if marks.get(x["id"]) and marks[x["id"]]["status"] in ("P", "H"))
    from ..security import grant_for
    in_window = (d >= today() - timedelta(days=rules["back"]) and d <= today()) or bool(grant_for(d, "attendance_photo"))
    return render_template("staff/index.html", d=d, staff=staff, marks=marks, STATUS=STATUS, next_code=nxt,
                           present=present, total=len(active), rules=rules, in_window=in_window,
                           mobile=is_mobile(), storage=storage_ready(), station=_station())


def _station():
    try:
        return db.one("SELECT value FROM settings WHERE key='station_name'")["value"]
    except Exception:
        return "Sai Fuel Mart"


@bp.route("/staff/attendance/start", methods=["POST"])
@login_required("attendance_photo")
def attendance_start():
    from flask import jsonify
    from .proofs import is_mobile, issue_capture, storage_ready
    if not is_mobile():
        return jsonify({"ok": False, "error": "Attendance photos are taken on a phone."}), 400
    try:
        d = parse_date(request.form.get("work_date"))
        with db.tx() as cur:
            check_photo_rules(cur, d)
            cur.execute("SELECT name FROM staff WHERE id=%s AND active", (int(request.form.get("staff_id") or 0),))
            s = cur.fetchone()
            if not s:
                raise UserError("Choose the staff member.")
        if not storage_ready():
            raise UserError("Photo storage is not set up yet. Ask the owner.")
        return jsonify({"ok": True, **issue_capture("attendance", s["name"])})
    except UserError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/staff/attendance/photo", methods=["POST"])
@login_required("attendance_photo")
def attendance_photo():
    """Marks Present only with a live photo just taken on the phone."""
    from flask import jsonify
    from datetime import timedelta, datetime
    from ..util import IST
    from .proofs import check_capture, is_mobile
    try:
        if not is_mobile():
            raise UserError("Attendance photos are taken on a phone.")
        d = parse_date(request.form.get("work_date"))
        sid = int(request.form.get("staff_id") or 0)
        try:
            taken = datetime.fromtimestamp(int(request.form.get("captured_at", "0")) / 1000, IST)
        except (ValueError, OverflowError, OSError):
            raise UserError("Photo time missing. Take the photo again.")
        if abs((now_ist() - taken).total_seconds()) > 10 * 60:
            raise UserError("This photo was not taken just now. Take a fresh photo.")
        ppath, _, _, _ = check_capture(request.form.get("ticket"))
        with db.tx() as cur:
            check_photo_rules(cur, d)
            cur.execute("SELECT * FROM staff WHERE id=%s AND active", (sid,))
            s = cur.fetchone()
            if not s:
                raise UserError("Choose the staff member.")
            cur.execute("SELECT * FROM attendance WHERE staff_id=%s AND work_date=%s", (sid, d))
            old = cur.fetchone()
            cur.execute("""INSERT INTO attendance (staff_id, work_date, status, updated_by, photo_path, captured_at,
                                                   latitude, longitude, marked_via, note)
                           VALUES (%s,%s,'P',%s,%s,%s,%s,%s,'photo','')
                           ON CONFLICT (staff_id, work_date) DO UPDATE SET status='P', updated_by=EXCLUDED.updated_by,
                             updated_at=NOW(), photo_path=EXCLUDED.photo_path, captured_at=EXCLUDED.captured_at,
                             latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude, marked_via='photo'""",
                        (sid, d, current_user()["username"], ppath, taken, clean(request.form.get("lat"), 20) or None,
                         clean(request.form.get("lng"), 20) or None))
            audit(cur, "update", "attendance", sid, f"{s['name']} {d}: present by live photo at {taken:%I:%M %p}"
                  + (f" (was {old['status']})" if old else ""), business_date=d)
        return jsonify({"ok": True, "name": s["name"]})
    except UserError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/staff/attendance/<int:sid>/<d>/photo")
@login_required("attendance_photo|attendance_manual|staff_manage")
def attendance_photo_file(sid, d):
    from flask import abort
    from .. import storage
    row = db.one("SELECT photo_path FROM attendance WHERE staff_id=%s AND work_date=%s", (sid, parse_date(d)))
    if not row or not row["photo_path"]:
        abort(404)
    link = storage.signed_download(row["photo_path"], 600)
    if not link:
        abort(404)
    return redirect(link)


@bp.route("/staff/attendance/rules", methods=["POST"])
@login_required("owner")
def attendance_rules_save():
    try:
        back = int(request.form.get("back") or 2)
    except ValueError:
        back = 2
    back = max(0, min(7, back))
    start = (request.form.get("start") or "05:00")[:5]
    end = (request.form.get("end") or "23:59")[:5]
    if not (len(start) == 5 and len(end) == 5 and start < end):
        raise UserError("Choose a start time earlier than the end time.")
    with db.tx() as cur:
        for k, v in (("attendance_back_days", str(back)), ("attendance_start", start), ("attendance_end", end)):
            cur.execute("""INSERT INTO settings (key, value) VALUES (%s,%s)
                           ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""", (k, v))
        audit(cur, "update", "settings", "attendance_rules",
              f"Photo attendance: today + {back} previous day(s), between {start} and {end}")
    flash(f"Rules saved: managers can mark today and {back} previous day(s), between {start} and {end}.", "success")
    return redirect(url_for("staff.index"))


@bp.route("/staff/save", methods=["POST"])
@login_required("staff_manage")
def save():
    sid = request.form.get("id")
    f = {"emp_code": clean(request.form.get("emp_code"), 20).upper(),
         "name": clean(request.form.get("name"), 80),
         "phone": "".join(c for c in request.form.get("phone", "") if c.isdigit())[-10:],
         "designation": clean(request.form.get("designation"), 40),
         "monthly_salary": to_dec(request.form.get("monthly_salary"), "Monthly salary"),
         "joined_on": parse_date(request.form["joined_on"], "Joining date") if request.form.get("joined_on") else None,
         "bank_account": clean(request.form.get("bank_account"), 40),
         "department": clean(request.form.get("department"), 40), "shift": clean(request.form.get("shift"), 20),
         "active": request.form.get("active", "1") == "1"}
    if not f["name"] or not f["emp_code"]:
        raise UserError("Name and employee code are required.")
    with db.tx() as cur:
        claim_form_token(cur)
        cur.execute("SELECT id FROM staff WHERE emp_code=%s AND id<>%s", (f["emp_code"], int(sid or 0)))
        if cur.fetchone():
            raise UserError(f"Employee code {f['emp_code']} is already used.")
        if sid:
            cur.execute("SELECT * FROM staff WHERE id=%s FOR UPDATE", (sid,))
            old = cur.fetchone()
            if not old:
                abort(404)
            cur.execute("""UPDATE staff SET emp_code=%(emp_code)s, name=%(name)s, phone=%(phone)s,
                           designation=%(designation)s, monthly_salary=%(monthly_salary)s, joined_on=%(joined_on)s,
                           bank_account=%(bank_account)s, active=%(active)s, department=%(department)s, shift=%(shift)s
                           WHERE id=%(id)s RETURNING *""",
                        {**f, "id": sid})
            audit(cur, "update", "staff", sid, f"Edited staff {f['name']}", before=old, after=cur.fetchone())
        else:
            cur.execute("""INSERT INTO staff (emp_code, name, phone, designation, monthly_salary, joined_on, bank_account,
                                              department, shift)
                           VALUES (%(emp_code)s,%(name)s,%(phone)s,%(designation)s,%(monthly_salary)s,%(joined_on)s,
                                   %(bank_account)s,%(department)s,%(shift)s) RETURNING *""", f)
            s = cur.fetchone()
            audit(cur, "create", "staff", s["id"], f"Added staff {f['name']} ({f['emp_code']})", after=s)
    flash("Staff saved.", "success")
    return redirect(url_for("staff.index"))


@bp.route("/staff/attendance", methods=["POST"])
@login_required("attendance_manual")
def save_attendance():
    """Mark or change attendance without a photo (owner, or whoever the owner allows)."""
    d = parse_date(request.form.get("work_date"))
    if d > today():
        raise UserError("You cannot mark attendance for a future date.")
    from ..security import grant_for, role_has
    if not role_has("attendance_manual") and not grant_for(d, "attendance_manual"):
        raise UserError("Your temporary access does not cover this date.")
    valid = {k for k, _ in STATUS} | {""}
    reason = clean(request.form.get("reason"), 200)
    n = 0
    with db.tx() as cur:
        claim_form_token(cur)
        cur.execute("SELECT id, name FROM staff WHERE active")
        for s in cur.fetchall():
            st = request.form.get(f"st_{s['id']}")
            if st is None or st not in valid:
                continue
            cur.execute("SELECT status, marked_via FROM attendance WHERE staff_id=%s AND work_date=%s", (s["id"], d))
            old = cur.fetchone()
            if (old["status"] if old else "") == st:
                continue
            if st == "":
                cur.execute("DELETE FROM attendance WHERE staff_id=%s AND work_date=%s", (s["id"], d))
            else:
                cur.execute("""INSERT INTO attendance (staff_id, work_date, status, updated_by, marked_via, note)
                               VALUES (%s,%s,%s,%s,'manual',%s)
                               ON CONFLICT (staff_id, work_date) DO UPDATE SET status=EXCLUDED.status,
                               updated_by=EXCLUDED.updated_by, updated_at=NOW(), marked_via='manual', note=EXCLUDED.note""",
                            (s["id"], d, st, current_user()["username"], reason))
            audit(cur, "update", "attendance", s["id"],
                  f"{s['name']} {d}: {(old['status'] if old else 'not marked')} → {st or 'not marked'} (manual)"
                  + (f" — {reason}" if reason else ""), business_date=d)
            n += 1
    flash(f"Attendance changed for {n} staff." if n else "Nothing changed.", "success" if n else "info")
    return redirect(url_for("staff.index", d=d.isoformat()))


def salary_sheet(cur, ym):
    first, last = month_bounds(ym)
    ndays = (last - first).days + 1
    cur.execute("SELECT * FROM staff WHERE active OR id IN (SELECT staff_id FROM attendance "
                "WHERE work_date BETWEEN %s AND %s) ORDER BY name", (first, last))
    staff = cur.fetchall()
    cur.execute("""SELECT staff_id, status, COUNT(*) AS n FROM attendance WHERE work_date BETWEEN %s AND %s
                   GROUP BY staff_id, status""", (first, last))
    att = {}
    for r in cur.fetchall():
        att.setdefault(r["staff_id"], {})[r["status"]] = r["n"]
    cur.execute("""SELECT staff_id, kind, SUM(amount) AS a FROM staff_payments WHERE for_month=%s AND NOT voided
                   GROUP BY staff_id, kind""", (ym,))
    pays = {}
    for r in cur.fetchall():
        pays.setdefault(r["staff_id"], {})[r["kind"]] = Decimal(r["a"])
    out = []
    for s in staff:
        a = att.get(s["id"], {})
        paid_days = Decimal(a.get("P", 0)) + Decimal(a.get("H", 0)) / 2 + Decimal(a.get("O", 0))
        earned = (Decimal(s["monthly_salary"]) * paid_days / ndays).quantize(Decimal("1"), ROUND_HALF_UP)
        p = pays.get(s["id"], {})
        adv, sal, bonus = p.get("advance", ZERO), p.get("salary", ZERO), p.get("bonus", ZERO)
        out.append({"staff": s, "att": a, "marked": sum(a.values()), "paid_days": paid_days, "earned": earned,
                    "advance": adv, "salary_paid": sal, "bonus": bonus,
                    "balance": earned - adv - sal})
    return {"rows": out, "ndays": ndays, "first": first, "last": last}


@bp.route("/staff/salary")
@login_required("salary")
def salary():
    ym = request.args.get("m") or today().strftime("%Y-%m")
    try:
        month_bounds(ym)
    except Exception:
        ym = today().strftime("%Y-%m")
    with db.tx() as cur:
        sheet = salary_sheet(cur, ym)
        cur.execute("""SELECT sp.*, s.name AS staff_name FROM staff_payments sp JOIN staff s ON s.id=sp.staff_id
                       WHERE sp.for_month=%s ORDER BY sp.business_date DESC, sp.id DESC""", (ym,))
        payments = cur.fetchall()
    y, m = (int(x) for x in ym.split("-"))
    prev_m = f"{y - (m == 1)}-{12 if m == 1 else m - 1:02d}"
    next_m = f"{y + (m == 12)}-{1 if m == 12 else m + 1:02d}"
    return render_template("staff/salary.html", ym=ym, sheet=sheet, payments=payments,
                           month_name=f"{calendar.month_name[m]} {y}", prev_m=prev_m,
                           next_m=next_m if next_m <= today().strftime("%Y-%m") else None,
                           PAY_MODES=STAFF_PAY_MODES)


@bp.route("/staff/payment", methods=["POST"])
@login_required("salary")
def add_payment():
    d = parse_date(request.form.get("business_date") or today().isoformat())
    ym = request.form.get("for_month") or d.strftime("%Y-%m")
    try:
        month_bounds(ym)
    except Exception:
        raise UserError("Choose the salary month.")
    kind = request.form.get("kind")
    if kind not in ("advance", "salary", "bonus"):
        raise UserError("Choose advance, salary or bonus.")
    mode = request.form.get("pay_mode")
    if mode not in STAFF_PAY_MODES:
        raise UserError("Choose how it was paid.")
    amt = to_dec(request.form.get("amount"), "Amount", allow_blank=False)
    if amt <= 0:
        raise UserError("Amount must be more than zero.")
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        cur.execute("SELECT * FROM staff WHERE id=%s", (int(request.form.get("staff_id") or 0),))
        s = cur.fetchone()
        if not s:
            raise UserError("Choose the staff member.")
        cur.execute("""INSERT INTO staff_payments (staff_id, business_date, for_month, kind, amount, pay_mode, remarks,
                                                   created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (s["id"], d, ym, kind, amt, mode, clean(request.form.get("remarks"), 200),
                     current_user()["username"]))
        p = cur.fetchone()
        audit(cur, "create", "staff_payment", p["id"], f"{kind.title()} ₹{amt} to {s['name']} for {ym} ({mode})",
              after=p, business_date=d)
    flash(f"{kind.title()} of ₹{amt} recorded for {s['name']}.", "success")
    back = request.form.get("_back")
    if back and back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect(url_for("staff.salary", m=ym))


@bp.route("/staff/payment/<int:pid>/void", methods=["POST"])
@login_required("cancel_entries")
def void_payment(pid):
    reason = clean(request.form.get("reason"), 200)
    if not reason:
        raise UserError("Give a reason for cancelling.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM staff_payments WHERE id=%s FOR UPDATE", (pid,))
        p = cur.fetchone()
        if not p or p["voided"]:
            raise UserError("Payment not found or already cancelled.")
        assert_writable(cur, p["business_date"])
        cur.execute("UPDATE staff_payments SET voided=TRUE, void_reason=%s, voided_by=%s, voided_at=NOW() WHERE id=%s",
                    (reason, current_user()["username"], pid))
        audit(cur, "void", "staff_payment", pid, f"Cancelled {p['kind']} ₹{p['amount']}: {reason}", before=p,
              business_date=p["business_date"])
    flash("Payment cancelled.", "success")
    back = request.form.get("_back")
    if back and back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect(url_for("staff.salary", m=p["for_month"]))
