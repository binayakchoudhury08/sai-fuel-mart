"""Read-only API for n8n automations (WhatsApp reminders and bills).

Security:
- One owner-generated key. Only its SHA-256 hash is stored; the key is shown once.
- Header `X-API-Key` (HTTPS only on Render). Constant-time comparison.
- Read-only: nothing here can change data. No phone numbers, balances or passwords are returned.
- 60 requests per minute per process; every call is written to the audit log.
"""
import hashlib
import secrets
import time
from collections import deque
from datetime import timedelta
from decimal import Decimal
from functools import wraps

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from .. import db
from ..exports import bill_filename, transport_bill_pdf, transport_bill_xlsx
from ..security import audit, current_user, login_required
from ..services import (avg_daily_sales, range_summary, settings_map, tank_reconciliation, transport_register)
from ..util import UserError, inr, now_ist, parse_date, qty, today

bp = Blueprint("automation", __name__)        # owner settings page
api = Blueprint("api_v1", __name__)           # machine endpoints (no session, no CSRF)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_calls = deque()


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def _clean_param(s, limit=200):
    """WhatsApp template parameters may not contain new lines, tabs or 4+ spaces."""
    s = " ".join(str(s or "").split())
    return s[:limit] or "-"


def require_key(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        key = request.headers.get("X-API-Key", "")
        with db.tx() as cur:
            cur.execute("SELECT key, value FROM settings WHERE key IN ('api_key_hash','api_enabled')")
            st = {r["key"]: r["value"] for r in cur.fetchall()}
        good = st.get("api_key_hash")
        if not good or st.get("api_enabled") != "1" or not key or not secrets.compare_digest(_hash(key), good):
            time.sleep(0.5)  # slow down guessing
            return jsonify({"ok": False, "error": "Invalid or missing API key"}), 401
        now = time.time()
        while _calls and now - _calls[0] > 60:
            _calls.popleft()
        if len(_calls) >= 60:
            return jsonify({"ok": False, "error": "Too many requests; try again in a minute"}), 429
        _calls.append(now)
        g.user = {"username": "n8n automation", "role": "system"}
        resp = fn(*a, **kw)
        try:
            with db.tx() as cur:
                audit(cur, "api", "automation", None,
                      f"{request.method} {request.path}" + (f"?{request.query_string.decode()[:150]}" if request.query_string else ""))
        except Exception:
            pass
        return resp
    return wrapper


@api.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _date_param(name="date", default="yesterday"):
    v = (request.args.get(name) or default).strip().lower()
    if v == "today":
        return today()
    if v == "yesterday":
        return today() - timedelta(days=1)
    return parse_date(v)


def _find_party(cur, name):
    name = (name or "").strip()
    if not name:
        raise UserError("party is required")
    if name.isdigit():
        cur.execute("SELECT * FROM parties WHERE id=%s", (int(name),))
        r = cur.fetchone()
        if r:
            return r
    cur.execute("SELECT * FROM parties WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))", (name,))
    r = cur.fetchone()
    if r:
        return r
    cur.execute("SELECT * FROM parties WHERE name ILIKE %s ORDER BY active DESC, name", (f"%{name}%",))
    rows = cur.fetchall()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise UserError(f"No transporter matches '{name}'")
    raise UserError(f"'{name}' matches several transporters: " + ", ".join(r["name"] for r in rows[:6]))


def _err(e, code=400):
    return jsonify({"ok": False, "error": str(e)}), code


# ------------------------------------------------------------------ endpoints
@api.route("/api/v1/ping")
@require_key
def ping():
    with db.tx() as cur:
        st = settings_map(cur)
    return jsonify({"ok": True, "station": st.get("station_name"), "today": today().isoformat(),
                    "server_time_ist": now_ist().strftime("%Y-%m-%d %H:%M:%S")})


@api.route("/api/v1/closing/overdue")
@require_key
def closing_overdue():
    """Days whose Daily closing should be finished but isn't.
    Day D's entries are made on D+1, so D is overdue once D+1 has ended (grace_days=1).
    Run at 12:00 AM: on 23 Oct it reports 21 Oct (and any earlier unfinished day in the lookback)."""
    try:
        grace = min(max(int(request.args.get("grace_days", 1)), 0), 7)
        lookback = min(max(int(request.args.get("lookback", 7)), 1), 60)
    except ValueError:
        return _err("grace_days and lookback must be numbers")
    t = today()
    last = t - timedelta(days=grace + 1)
    first = t - timedelta(days=lookback)
    with db.tx() as cur:
        cur.execute("SELECT MIN(business_date) AS m FROM days WHERE NOT is_legacy")
        started = cur.fetchone()["m"]
        if started is None or last < first:
            return jsonify({"ok": True, "count": 0, "overdue": [], "checked_upto": last.isoformat()})
        first = max(first, started)
        cur.execute("""
            SELECT d::date AS day, dy.status, dy.counted_cash IS NOT NULL AS cash_counted,
                   (SELECT COUNT(*) FROM nozzle_readings r JOIN nozzles n ON n.id=r.nozzle_id
                     WHERE r.business_date=d::date AND n.active) AS readings_done,
                   (SELECT COUNT(*) FROM nozzles WHERE active) AS readings_needed,
                   (SELECT COUNT(*) FROM party_txns WHERE business_date=d::date AND NOT voided
                     AND kind IN ('fuel','cash_advance')) AS transport_entries
            FROM generate_series(%s::date, %s::date, interval '1 day') d
            LEFT JOIN days dy ON dy.business_date = d::date
            WHERE COALESCE(dy.status, 'open') NOT IN ('submitted', 'locked') AND NOT COALESCE(dy.is_legacy, FALSE)
            ORDER BY d""", (first, last))
        rows = cur.fetchall()
    overdue = []
    for r in rows:
        missing = []
        if r["readings_done"] < r["readings_needed"]:
            missing.append(f"meter readings {r['readings_done']}/{r['readings_needed']}")
        if not r["cash_counted"]:
            missing.append("cash count")
        missing.append("not submitted")
        overdue.append({"date": r["day"].isoformat(), "label": r["day"].strftime("%d %b"),
                        "status": r["status"] or "not started", "readings_done": r["readings_done"],
                        "readings_needed": r["readings_needed"], "cash_counted": r["cash_counted"],
                        "transport_entries": r["transport_entries"], "missing": missing})
    dates = ", ".join(o["label"] for o in overdue)
    details = "; ".join(f"{o['label']}: {', '.join(o['missing'])}" for o in overdue)
    return jsonify({"ok": True, "count": len(overdue), "overdue": overdue, "checked_upto": last.isoformat(),
                    "wa_params": [_clean_param(dates, 120), _clean_param(details, 500)]})


@api.route("/api/v1/transport/bill-info")
@require_key
def bill_info():
    """What the 4 PM workflow should send. date=yesterday by default; fallback=latest sends the most
    recent day that has entries (on or before the requested day) when that day has none."""
    try:
        d = _date_param()
    except UserError as e:
        return _err(e)
    fallback = request.args.get("fallback", "latest")
    with db.tx() as cur:
        try:
            p = _find_party(cur, request.args.get("party"))
        except UserError as e:
            return _err(e, 404)
        rows = transport_register(cur, d, d, p["id"], include_voided=False)
        used = d
        if not rows and fallback == "latest":
            cur.execute("""SELECT MAX(business_date) AS m FROM party_txns WHERE party_id=%s AND NOT voided
                           AND kind IN ('fuel','cash_advance') AND business_date <= %s""", (p["id"], d))
            m = cur.fetchone()["m"]
            if m:
                used = m
                rows = transport_register(cur, m, m, p["id"], include_voided=False)
    if not rows:
        return jsonify({"ok": True, "found": False, "party": p["name"], "requested_date": d.isoformat()})
    q = sum((Decimal(r["qty"]) for r in rows if r["kind"] == "fuel"), Decimal(0))
    total = sum((Decimal(r["debit"]) for r in rows), Decimal(0))
    cash = sum((r["cash"] for r in rows), Decimal(0))
    label = used.strftime("%d %b %Y") + ("" if used == d else f" (no entries on {d.strftime('%d %b')}; latest)")
    base = {"party": p["id"], "date": used.isoformat()}
    return jsonify({
        "ok": True, "found": True, "party": p["name"], "date": used.isoformat(), "requested_date": d.isoformat(),
        "is_fallback": used != d, "entries": len(rows), "qty": str(q), "cash": str(cash), "total": str(total),
        "files": {
            "pdf": {"path": url_for("api_v1.bill_file", fmt="pdf", **base),
                    "filename": bill_filename(p["name"], used, "pdf"), "mime": "application/pdf"},
            "xlsx": {"path": url_for("api_v1.bill_file", fmt="xlsx", **base),
                     "filename": bill_filename(p["name"], used, "xlsx"), "mime": XLSX}},
        "wa_params": [_clean_param(p["name"], 60), _clean_param(label, 80), str(len(rows)), qty(q),
                      inr(total, symbol=False)]})


@api.route("/api/v1/transport/bill.<fmt>")
@require_key
def bill_file(fmt):
    if fmt not in ("pdf", "xlsx"):
        abort(404)
    try:
        d = _date_param()
    except UserError as e:
        return _err(e)
    with db.tx() as cur:
        try:
            p = _find_party(cur, request.args.get("party"))
        except UserError as e:
            return _err(e, 404)
        rows = transport_register(cur, d, d, p["id"], include_voided=False)
        st = settings_map(cur)
    if not rows:
        return _err(f"No entries for {p['name']} on {d}", 404)
    maker = transport_bill_pdf if fmt == "pdf" else transport_bill_xlsx
    return send_file(maker(p, rows, d, d, st), as_attachment=True,
                     mimetype="application/pdf" if fmt == "pdf" else XLSX,
                     download_name=bill_filename(p["name"], d, fmt))


@api.route("/api/v1/summary/day")
@require_key
def day_summary():
    try:
        d = _date_param()
    except UserError as e:
        return _err(e)
    with db.tx() as cur:
        rows = range_summary(cur, d, d)
        tanks = tank_reconciliation(cur, d)
        avgs = avg_daily_sales(cur, d)
    r = rows[0] if rows else None
    tank_txt = []
    for t in tanks:
        if t["actual"] is not None:
            avg = avgs.get(t["tank"]["fuel_code"])
            left = f" (~{(t['actual'] / avg).quantize(Decimal('0.1'))} days)" if avg else ""
            tank_txt.append(f"{t['tank']['fuel_code']} {qty(t['actual'], 0)} L{left}")
    out = {"ok": True, "date": d.isoformat(), "has_data": bool(r)}
    if r:
        diff = r["difference"]
        out.update({"status": r["status"], "sale": str(r["gross_sale"]), "credit": str(r["credit"]),
                    "digital": str(r["digital"]), "expected_cash": str(r["expected_cash"]),
                    "difference": str(diff) if diff is not None else None,
                    "qty": {k: str(v) for k, v in r["qty"].items()}})
    status = {"open": "OPEN (not submitted)", "submitted": "submitted", "locked": "verified & locked",
              "legacy": "old system"}.get(r["status"] if r else "", "no entries")
    out["wa_params"] = [
        d.strftime("%d %b %Y"),
        inr(r["gross_sale"], symbol=False) if r else "0.00",
        qty(r["qty"].get("HSD", 0)) if r else "0.00",
        qty(r["qty"].get("MS", 0)) if r else "0.00",
        inr(r["credit"], symbol=False) if r else "0.00",
        ("not counted" if (not r or r["difference"] is None) else
         (("short " if r["difference"] < 0 else "excess " if r["difference"] > 0 else "") + inr(abs(r["difference"]), symbol=False))),
        status,
        _clean_param(", ".join(tank_txt) or "no dip reading", 150)]
    return jsonify(out)


# ------------------------------------------------------------------ owner page
@bp.route("/settings/automation", methods=["GET", "POST"])
@login_required("owner")
def settings_page():
    new_key = None
    if request.method == "POST":
        action = request.form.get("action")
        with db.tx() as cur:
            if action == "generate":
                new_key = "sfm_" + secrets.token_urlsafe(32)
                for k, v in (("api_key_hash", _hash(new_key)), ("api_key_hint", new_key[-4:]),
                             ("api_key_created", now_ist().strftime("%d %b %Y %I:%M %p")), ("api_enabled", "1")):
                    cur.execute("""INSERT INTO settings (key, value) VALUES (%s,%s)
                                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""", (k, v))
                audit(cur, "create", "api_key", None, "Generated a new automation (n8n) key; any old key stopped working")
            elif action in ("disable", "enable"):
                cur.execute("""INSERT INTO settings (key, value) VALUES ('api_enabled', %s)
                               ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""", ("1" if action == "enable" else "0",))
                audit(cur, "update", "api_key", None, f"Automation key {action}d")
                flash(f"Automation {'switched on' if action == 'enable' else 'switched off'}.", "success")
            elif action == "revoke":
                cur.execute("DELETE FROM settings WHERE key IN ('api_key_hash','api_key_hint','api_key_created')")
                cur.execute("""INSERT INTO settings (key, value) VALUES ('api_enabled','0')
                               ON CONFLICT (key) DO UPDATE SET value='0'""")
                audit(cur, "delete", "api_key", None, "Automation key revoked")
                flash("Key deleted. n8n can no longer read anything until you create a new key.", "success")
    with db.tx() as cur:
        st = settings_map(cur)
        cur.execute("""SELECT at, summary FROM audit_log WHERE action='api' ORDER BY id DESC LIMIT 15""")
        calls = cur.fetchall()
    return render_template("admin/automation.html", st=st, new_key=new_key, calls=calls,
                           base=request.url_root.rstrip("/"))
