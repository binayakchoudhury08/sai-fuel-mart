"""Login, roles, CSRF, double-submit protection, audit trail and the
date/lock rules that every write must pass through."""
import json
import secrets
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .util import UserError, now_ist, today

ROLES = ("owner", "manager", "operator")
ROLE_LABEL = {"owner": "Owner", "manager": "Manager", "operator": "Operator"}
RANK = {"operator": 1, "manager": 2, "owner": 3}

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


def can(min_role):
    r = role()
    return bool(r) and RANK[r] >= RANK[min_role]


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
          if request else "",
          action, entity, str(entity_id) if entity_id is not None else None,
          business_date, summary[:500], _dump(before), _dump(after)))


# ------------------------------------------------------------------ date rules
def get_setting(cur, key, default=""):
    cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
    r = cur.fetchone()
    return r["value"] if r else default


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
    if r != "owner":
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
    if st == "submitted" and r == "operator":
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
