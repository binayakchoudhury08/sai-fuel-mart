"""Login, roles, CSRF, double-submit protection, audit trail and the
date/lock rules that every write must pass through."""
import json
import secrets
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

from flask import abort, flash, g, has_request_context, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .util import UserError, now_ist, today

ROLES = ("owner", "admin", "manager", "operator")
ROLE_LABEL = {"owner": "Owner", "admin": "Admin", "manager": "Manager", "operator": "Staff (data entry)"}
RANK = {"operator": 1, "manager": 2, "admin": 3, "owner": 4}

# Everything a role can be allowed to do. The OWNER always has all of it and is the only one who can
# change this list for Admin, Manager and Staff (Settings → Permissions).
#  key, label, group, default roles
PERMISSIONS = [
    ("closing_enter", "Enter daily closing: meter readings, cash, digital, expenses, lube sales", "Daily work", {"operator", "manager", "admin"}),
    ("closing_submit", "Submit the day for verification", "Daily work", {"operator", "manager", "admin"}),
    ("transport_enter", "Credit transport entries (slips)", "Daily work", {"operator", "manager", "admin"}),
    ("bills", "Download transport bills (PDF / Excel / ZIP)", "Daily work", {"operator", "manager", "admin"}),
    ("payments", "Receive payments from parties", "Daily work", {"operator", "manager", "admin"}),
    ("tanks", "Tank dips and tanker receipts", "Daily work", {"operator", "manager", "admin"}),
    ("proofs", "Take photo proofs", "Daily work", {"operator", "manager", "admin"}),
    ("attendance_photo", "Mark attendance with a live staff photo (the only way for anyone except the owner)", "Staff", {"manager", "admin"}),
    ("salary", "Pay salary / advance / bonus and see the salary sheet", "Staff", {"manager", "admin"}),
    ("staff_manage", "Add and edit staff", "Staff", {"manager", "admin"}),
    ("lube_stock", "Add lube stock, products and stock counts", "Stock", {"manager", "admin"}),
    ("cancel_entries", "Cancel or correct entries (with reason)", "Control", {"manager", "admin"}),
    ("rate_override", "Use a different rate on a credit entry", "Control", {"manager", "admin"}),
    ("parties_manage", "Add / edit parties and give discounts", "Control", {"manager", "admin"}),
    ("accounts_view", "See party balances and statements", "Control", {"manager", "admin"}),
    ("reports", "See reports and analytics, download Excel / PDF", "Control", {"manager", "admin"}),
    ("day_lock", "Verify and lock days", "Owner-level", {"admin"}),
    ("day_reopen", "Reopen locked days and change days older than the back-date window", "Owner-level", {"admin"}),
    ("meter_override", "Change an opening meter reading", "Owner-level", set()),
    ("adjustments", "Balance adjustments and opening balances", "Owner-level", {"admin"}),
    ("credit_limit_override", "Allow credit beyond a party's limit", "Owner-level", {"admin"}),
    ("setup", "Fuel rates, nozzles, tanks, pump details", "Owner-level", {"admin"}),
    ("users", "Create staff / manager accounts and reset their passwords", "Owner-level", {"admin"}),
    ("audit", "See the audit log", "Owner-level", {"admin"}),
    ("insights", "Business insights: profit, tank variation, tanker receipts, credit scores, month comparison", "Owner-level", set()),
    ("backup", "Download full backup", "Owner-level", set()),
]
PERM_KEYS = [p[0] for p in PERMISSIONS]
_perm_cache = {"at": 0.0, "map": None}

MAX_FAILED = 5
LOCK_MINUTES = 15


# ------------------------------------------------------------------ passwords
def hash_pw(pw):
    return generate_password_hash(pw, method="pbkdf2:sha256:600000")


def check_pw(hashed, pw):
    try:
        return check_password_hash(hashed, pw)
    except Exception:
        return False


def validate_new_password(pw, username=""):
    if len(pw) < 8:
        raise UserError("Password must be at least 8 characters.")
    if pw.lower() in {"12345678", "password", "password1", "11111111", "00000000", "123456789"}:
        raise UserError("That password is too easy to guess. Choose another.")
    if username and pw.lower() == username.lower():
        raise UserError("Password cannot be the same as the username.")


# ------------------------------------------------------------------ session
def current_user():
    return g.get("user")


def role():
    u = current_user()
    return u["role"] if u else None


def role_perms(cur=None):
    """{role: set(keys)} — from Settings, falling back to the defaults above. Cached for 20 s."""
    import time as _t
    if _perm_cache["map"] is not None and _t.time() - _perm_cache["at"] < 20:
        return _perm_cache["map"]
    from . import db as _db
    m = {r: {k for k, _, _, d in PERMISSIONS if r in d} for r in ("admin", "manager", "operator")}
    try:
        rows = _db.query("SELECT key, value FROM settings WHERE key LIKE 'perms:%%'")
        for row in rows:
            r = row["key"].split(":", 1)[1]
            if r in m:
                m[r] = {k for k in row["value"].split(",") if k in PERM_KEYS}
    except Exception:
        pass
    _perm_cache.update(at=_t.time(), map=m)
    return m


def reset_perm_cache():
    _perm_cache.update(at=0.0, map=None)


def active_grants():
    """Temporary access the owner gave the signed-in user (cached for this request)."""
    if "grants" in g:
        return g.grants
    u = current_user()
    g.grants = []
    if u and u.get("role") not in ("owner", "system") and u.get("id"):
        from . import db as _db
        try:
            g.grants = [dict(r, perm_set=set(r["perms"].split(","))) for r in _db.query(
                """SELECT * FROM access_grants WHERE user_id=%s AND revoked_at IS NULL AND valid_until > NOW()
                   ORDER BY valid_until""", (u["id"],))]
        except Exception:
            g.grants = []
    return g.grants


def grant_for(d=None, perm=None):
    """The active grant covering date d (and permission perm), if any."""
    for gr in active_grants():
        if (d is None or gr["date_from"] <= d <= gr["date_to"]) and (perm is None or perm in gr["perm_set"]):
            return gr
    return None


def role_has(perm):
    r = role()
    if perm == "attendance_photo" and r and current_user() and current_user().get("id") == attendance_backup_id():
        return True
    return bool(r) and (r == "owner" or perm in role_perms().get(r, set()))


def attendance_backup_id():
    """The one person the owner named as permanent backup for photo attendance (cached per request)."""
    if "att_backup" not in g:
        g.att_backup = None
        try:
            from . import db as _db
            r = _db.one("SELECT value FROM settings WHERE key='attendance_backup_user'")
            g.att_backup = int(r["value"]) if r and r["value"] and r["value"].isdigit() else None
        except Exception:
            pass
    return g.att_backup


def has(perm):
    r = role()
    if not r:
        return False
    if r == "owner":
        return True
    if perm == "attendance_photo" and current_user() and current_user().get("id") == attendance_backup_id():
        return True
    return perm in role_perms().get(r, set()) or grant_for(perm=perm) is not None


def can(what):
    """Templates and views: can('perm_key'), can('a|b') for any of several, or a role name for 'at least this role'."""
    if "|" in what:
        return any(can(w) for w in what.split("|") if w)
    if what in RANK:
        r = role()
        return bool(r) and RANK[r] >= RANK[what]
    return has(what)


def login_required(min_role="operator"):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user():
                if request.method == "GET":
                    return redirect(url_for("auth.login", next=request.full_path))
                return redirect(url_for("auth.login"))
            if current_user().get("must_change_pw") and request.endpoint not in (
                    "auth.change_password", "auth.logout"):
                return redirect(url_for("auth.change_password"))
            if not can(min_role):
                abort(403)
            g.need_perm = min_role
            return fn(*a, **kw)
        return wrapper
    return deco


# ------------------------------------------------------------------ CSRF
def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok


def check_csrf():
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
        abort(400, description="This form expired. Go back, reload the page and try again.")


def new_form_token():
    return uuid.uuid4().hex


def claim_form_token(cur):
    """Reject the second submission of the same form (double tap, network
    retry, back-button resubmit). Must run inside the same transaction as
    the write, so a failed save frees the token for a retry."""
    tok = (request.form.get("_ft") or "").strip()
    if not tok:
        return
    cur.execute("INSERT INTO form_tokens(token) VALUES (%s) ON CONFLICT DO NOTHING RETURNING token",
                (tok[:64],))
    if cur.fetchone() is None:
        raise DuplicateSubmit()


class DuplicateSubmit(Exception):
    pass


# ------------------------------------------------------------------ audit
def _jsonable(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def _dump(d):
    if d is None:
        return None
    return json.dumps(dict(d), default=_jsonable, ensure_ascii=False)


def audit(cur, action, entity, entity_id=None, summary="", before=None, after=None,
          business_date=None):
    u = current_user() or {}
    cur.execute("""
        INSERT INTO audit_log (username, role, ip, action, entity, entity_id,
                               business_date, summary, before_data, after_data)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (u.get("username", "system"), u.get("role", "-"),
          (request.headers.get("X-Forwarded-For", request.remote_addr or "") or "").split(",")[0][:60]
          if has_request_context() else "",
          action, entity, str(entity_id) if entity_id is not None else None,
          business_date, summary[:500], _dump(before), _dump(after)))


# ------------------------------------------------------------------ date rules
def get_setting(cur, key, default=""):
    cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
    r = cur.fetchone()
    return r["value"] if r else default


TRANSPORT_DAYS = 31


def assert_transport_writable(cur, d):
    """Credit transport slips: any date in the last 31 days, whatever the Daily closing status.
    Only the owner can go further back."""
    if d > today():
        raise UserError("You cannot enter credit transport for a future date.")
    # any past date — including days imported from the old system — whatever the Daily closing status


def managers_edit_history(cur):
    """Owner's switch: let managers reopen and correct old / imported days."""
    return get_setting(cur, "managers_edit_history", "0") == "1"


def can_unlock(cur=None):
    return has("day_reopen")


def day_status(cur, d):
    cur.execute("SELECT status FROM days WHERE business_date=%s", (d,))
    r = cur.fetchone()
    return r["status"] if r else "open"


def assert_writable(cur, d, *, lock_row=True):
    """The single gate for any change dated `d`."""
    t = today()
    if d > t:
        raise UserError("You cannot enter data for a future date.")
    r = role()
    # temporary access from the owner: only inside its dates, and it lifts the back-date window there
    need = g.get("need_perm") or ""
    needs = [x for x in need.split("|") if x and x not in RANK]
    from_grant_only = needs and not any(role_has(x) for x in needs)
    covering = grant_for(d) if r not in ("owner", None) else None
    if from_grant_only and not any(grant_for(d, x) for x in needs):
        gr = next((x for x in active_grants() if any(n in x["perm_set"] for n in needs)), None)
        if gr:
            raise UserError(f"Your temporary access covers only {gr['date_from']:%d %b %Y} – {gr['date_to']:%d %b %Y}.")
        raise UserError("You are not allowed to change this date.")
    if not has("day_reopen") and not covering:
        try:
            back = int(get_setting(cur, "backdate_days", "2") or 2)
        except ValueError:
            back = 2
        if d < t - timedelta(days=back):
            raise UserError(f"Only the owner can change entries older than {back} day(s). "
                            f"{d.strftime('%d %b')} is outside that window.")
    if lock_row:
        # serialise writers for the same day so totals can't race
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"day:{d.isoformat()}",))
    st = day_status(cur, d)
    if st == "locked":
        raise UserError(f"{d.strftime('%d %b %Y')} is verified and locked. "
                        "Only the owner can unlock it.")
    if st == "submitted" and not has("cancel_entries"):
        raise UserError(f"{d.strftime('%d %b %Y')} has been submitted for verification. "
                        "Ask the manager to make changes.")


def next_receipt_no(cur, d):
    from .util import fy_label
    fy = fy_label(d)
    name = f"receipt:{fy}"
    cur.execute("""
        INSERT INTO counters(name, value) VALUES (%s, 1)
        ON CONFLICT (name) DO UPDATE SET value = counters.value + 1
        RETURNING value
    """, (name,))
    n = cur.fetchone()["value"]
    return f"R{fy[2:4]}{fy[-2:]}-{n:05d}"


def login_attempt_lockout(user_row):
    lu = user_row.get("locked_until")
    if lu and lu > now_ist():
        mins = int((lu - now_ist()).total_seconds() // 60) + 1
        return mins
    return 0


def fail_and_flash(msg, endpoint, **kw):
    flash(msg, "error")
    return redirect(url_for(endpoint, **kw))
