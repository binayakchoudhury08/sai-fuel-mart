import hashlib
import os
import secrets
from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from .. import db
from ..security import (LOCK_MINUTES, MAX_FAILED, ROLES, audit, check_pw, claim_form_token,
                        current_user, hash_pw, login_attempt_lockout, login_required,
                        validate_new_password)
from ..util import UserError, clean, now_ist

bp = Blueprint("auth", __name__)


def _start_session(u, remember):
    session.clear()
    session["uid"] = u["id"]
    session["pw"] = hashlib.sha256(u["password_hash"].encode()).hexdigest()[:16]
    session.permanent = bool(remember)


def _safe_next(nxt):
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return url_for("main.dashboard")


@bp.route("/login", methods=["GET", "POST"])
def login():
    has_users = db.one("SELECT COUNT(*) AS n FROM users")["n"] > 0
    if not has_users:
        return redirect(url_for("auth.setup"))
    if current_user():
        return redirect(url_for("main.dashboard"))
    error = None
    username = ""
    if request.method == "POST":
        username = clean(request.form.get("username"), 60).lower()
        pw = request.form.get("password") or ""
        with db.tx() as cur:
            cur.execute("SELECT * FROM users WHERE LOWER(username)=%s FOR UPDATE", (username,))
            u = cur.fetchone()
            if not u or not u["active"]:
                error = "Wrong username or password."
            else:
                mins = login_attempt_lockout(u)
                if mins:
                    error = f"Too many wrong attempts. Try again in {mins} minute(s)."
                elif check_pw(u["password_hash"], pw):
                    cur.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login_at=NOW() "
                                "WHERE id=%s", (u["id"],))
                    _start_session(u, request.form.get("remember"))
                    from flask import g
                    g.user = {"username": u["username"], "role": u["role"]}
                    audit(cur, "login", "user", u["id"], f"{u['username']} signed in")
                else:
                    fails = u["failed_attempts"] + 1
                    lock = now_ist() + timedelta(minutes=LOCK_MINUTES) if fails >= MAX_FAILED else None
                    cur.execute("UPDATE users SET failed_attempts=%s, locked_until=%s WHERE id=%s",
                                (0 if lock else fails, lock, u["id"]))
                    error = ("Too many wrong attempts. Account paused for "
                             f"{LOCK_MINUTES} minutes.") if lock else "Wrong username or password."
        if not error:
            return redirect(_safe_next(request.args.get("next")))
    return render_template("auth/login.html", error=error, username=username)


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First-run: create the owner account. Needs SETUP_CODE from Render's
    environment so a stranger who finds the URL first can't take over."""
    if db.one("SELECT COUNT(*) AS n FROM users")["n"] > 0:
        return redirect(url_for("auth.login"))
    code_required = os.environ.get("SETUP_CODE", "").strip()
    error = None
    if request.method == "POST":
        try:
            if not code_required:
                raise UserError("SETUP_CODE is not set on the server. Add it in Render → Environment, "
                                "then reload this page.")
            if not secrets.compare_digest(request.form.get("setup_code", "").strip(), code_required):
                raise UserError("Setup code is wrong.")
            username = clean(request.form.get("username"), 40).lower()
            if not username.isidentifier() and not username.replace(".", "").replace("-", "").isalnum():
                raise UserError("Username can use letters, numbers, dot and dash only.")
            pw = request.form.get("password") or ""
            validate_new_password(pw, username)
            if pw != request.form.get("password2"):
                raise UserError("The two passwords do not match.")
            with db.tx() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(77001)")
                cur.execute("SELECT COUNT(*) AS n FROM users")
                if cur.fetchone()["n"]:
                    return redirect(url_for("auth.login"))
                cur.execute("""INSERT INTO users (username, full_name, password_hash, role)
                               VALUES (%s,%s,%s,'owner') RETURNING *""",
                            (username, clean(request.form.get("full_name"), 80), hash_pw(pw)))
                u = cur.fetchone()
                from flask import g
                g.user = {"username": username, "role": "owner"}
                audit(cur, "create", "user", u["id"], f"Owner account '{username}' created at first setup")
            _start_session(u, True)
            flash("Owner account created. Next: set fuel rates and nozzles in Settings.", "success")
            return redirect(url_for("admin.settings"))
        except UserError as e:
            error = str(e)
    return render_template("auth/setup.html", error=error, code_required=bool(code_required))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required()
def change_password():
    if request.method == "POST":
        old = request.form.get("old_password") or ""
        new = request.form.get("new_password") or ""
        with db.tx() as cur:
            cur.execute("SELECT * FROM users WHERE id=%s FOR UPDATE", (current_user()["id"],))
            u = cur.fetchone()
            if not check_pw(u["password_hash"], old):
                raise UserError("Current password is wrong.")
            validate_new_password(new, u["username"])
            if new != request.form.get("new_password2"):
                raise UserError("The two new passwords do not match.")
            h = hash_pw(new)
            cur.execute("UPDATE users SET password_hash=%s, must_change_pw=FALSE WHERE id=%s", (h, u["id"]))
            audit(cur, "update", "user", u["id"], "Changed own password")
            u = dict(u); u["password_hash"] = h
        _start_session(u, session.permanent)
        flash("Password changed.", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("auth/password.html")


# ------------------------------------------------------------------ users (owner)
@bp.route("/users")
@login_required("owner")
def users():
    rows = db.query("SELECT * FROM users ORDER BY active DESC, role, username")
    return render_template("auth/users.html", rows=rows, roles=ROLES)


@bp.route("/users/new", methods=["POST"])
@login_required("owner")
def user_new():
    username = clean(request.form.get("username"), 40).lower()
    role = request.form.get("role")
    if not username or not username.replace(".", "").replace("-", "").replace("_", "").isalnum():
        raise UserError("Username can use letters, numbers, dot, dash and underscore only.")
    if role not in ROLES:
        raise UserError("Choose a role.")
    pw = request.form.get("password") or ""
    validate_new_password(pw, username)
    with db.tx() as cur:
        claim_form_token(cur)
        cur.execute("SELECT 1 FROM users WHERE LOWER(username)=%s", (username,))
        if cur.fetchone():
            raise UserError(f"Username '{username}' is taken.")
        cur.execute("""INSERT INTO users (username, full_name, password_hash, role, must_change_pw)
                       VALUES (%s,%s,%s,%s,TRUE) RETURNING id""",
                    (username, clean(request.form.get("full_name"), 80), hash_pw(pw), role))
        uid = cur.fetchone()["id"]
        audit(cur, "create", "user", uid, f"Created {role} account '{username}'")
    flash(f"Account '{username}' created. They must change the password at first sign-in.", "success")
    return redirect(url_for("auth.users"))


@bp.route("/users/<int:uid>", methods=["POST"])
@login_required("owner")
def user_update(uid):
    action = request.form.get("action")
    with db.tx() as cur:
        cur.execute("SELECT * FROM users WHERE id=%s FOR UPDATE", (uid,))
        u = cur.fetchone()
        if not u:
            raise UserError("User not found.")
        if action == "reset":
            pw = request.form.get("password") or ""
            validate_new_password(pw, u["username"])
            cur.execute("UPDATE users SET password_hash=%s, must_change_pw=TRUE, failed_attempts=0, "
                        "locked_until=NULL WHERE id=%s", (hash_pw(pw), uid))
            audit(cur, "update", "user", uid, f"Reset password for '{u['username']}'")
            flash(f"Password reset for {u['username']}. Old sessions are signed out.", "success")
        elif action in ("disable", "enable"):
            if uid == current_user()["id"]:
                raise UserError("You cannot disable your own account.")
            if action == "disable" and u["role"] == "owner":
                cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='owner' AND active AND id<>%s", (uid,))
                if cur.fetchone()["n"] == 0:
                    raise UserError("Keep at least one active owner.")
            cur.execute("UPDATE users SET active=%s WHERE id=%s", (action == "enable", uid))
            audit(cur, "update", "user", uid, f"{action.title()}d account '{u['username']}'")
            flash(f"{u['username']} {action}d.", "success")
        elif action == "role":
            new_role = request.form.get("role")
            if new_role not in ROLES:
                raise UserError("Choose a role.")
            if uid == current_user()["id"] and new_role != "owner":
                raise UserError("You cannot remove your own owner role.")
            cur.execute("UPDATE users SET role=%s WHERE id=%s", (new_role, uid))
            audit(cur, "update", "user", uid, f"Role of '{u['username']}' {u['role']} → {new_role}")
            flash("Role updated.", "success")
    return redirect(url_for("auth.users"))
