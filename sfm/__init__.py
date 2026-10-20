import hashlib
import logging
import os
from datetime import timedelta

import psycopg2
from flask import Flask, flash, g, redirect, render_template, request, session, url_for

from . import db
from .security import (DuplicateSubmit, ROLE_LABEL, can, check_csrf, csrf_token,
                       new_form_token)
from .util import UserError, fmt_date, inr, qty, today

log = logging.getLogger("sfm")
VERSION = "2.0.0"


def _secret_key():
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    # Fallback that is stable across workers/restarts and not guessable
    # without the DB password. Setting SECRET_KEY in Render is still better.
    seed = os.environ.get("DATABASE_URL", "") + "|sfm-v2"
    log.warning("SECRET_KEY not set; deriving one from DATABASE_URL. Set SECRET_KEY in Render.")
    return hashlib.sha256(seed.encode()).hexdigest()


def _friendly_db_error(e):
    msg = str(getattr(e, "pgerror", "") or e).strip().splitlines()[0] if str(e) else "Database error"
    msg = msg.replace("ERROR:", "").strip()
    if "is locked" in msg or "cannot be" in msg:
        return msg
    if "duplicate key" in msg:
        return "That entry already exists."
    if "closing >= opening" in msg or "nozzle_readings_check" in msg:
        return "Closing reading cannot be less than opening reading."
    return None


def create_app():
    app = Flask(__name__, static_folder="../static", template_folder="../templates")
    app.secret_key = _secret_key()
    on_https = os.environ.get("RENDER", "") != "" or os.environ.get("FORCE_HTTPS") == "1"
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_https,
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=12 * 1024 * 1024,
        SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=7),
    )
    if on_https:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    logging.basicConfig(level=logging.INFO)

    if os.environ.get("SKIP_SCHEMA") != "1":
        try:
            db.ensure_schema()
        except Exception as e:  # keep the app up so the error page can explain
            log.exception("Schema setup failed: %s", e)

    from .views import admin, auth, day, main, parties, lube, reports, staff, tanks
    for bp in (auth.bp, main.bp, day.bp, parties.bp, lube.bp, tanks.bp, tanks.api, staff.bp, reports.bp,
               admin.bp):
        app.register_blueprint(bp)

    @app.before_request
    def load_user():
        g.user = None
        if request.endpoint in ("static", "service_worker", "health") or (request.endpoint or "").startswith("api."):
            return
        uid = session.get("uid")
        if uid:
            try:
                u = db.one("SELECT id, username, full_name, role, active, must_change_pw, password_hash "
                           "FROM users WHERE id=%s", (uid,))
            except Exception:
                u = None
                raise
            stamp = hashlib.sha256(u["password_hash"].encode()).hexdigest()[:16] if u else None
            if not u or not u["active"] or session.get("pw") != stamp:
                session.clear()
            else:
                u = dict(u)
                u.pop("password_hash", None)
                g.user = u
        if request.method == "POST" and not (request.endpoint or "").startswith("api."):
            check_csrf()

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if g.get("user") and resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "form_token": new_form_token, "user": g.get("user"),
                "can": can, "today": today(), "ROLE_LABEL": ROLE_LABEL, "VERSION": VERSION,
                "station_name": _station_name()}

    def _station_name():
        if "station_name" not in g:
            try:
                r = db.one("SELECT value FROM settings WHERE key='station_name'")
                g.station_name = r["value"] if r else "Sai Fuel Mart"
            except Exception:
                g.station_name = "Sai Fuel Mart"
        return g.station_name

    app.jinja_env.filters.update(inr=inr, qty=qty, d=fmt_date)

    def back():
        ref = request.form.get("_back") or request.referrer
        if ref and ref.startswith(request.host_url):
            return redirect(ref)
        if ref and ref.startswith("/") and not ref.startswith("//"):
            return redirect(ref)
        return redirect(url_for("main.dashboard"))

    @app.errorhandler(UserError)
    def user_error(e):
        flash(str(e), "error")
        if request.method == "POST":
            return back()
        return redirect(url_for("main.dashboard"))

    @app.errorhandler(DuplicateSubmit)
    def dup(e):
        flash("This form was already saved once, so the repeat was ignored. Nothing was recorded twice.",
              "info")
        return back()

    @app.errorhandler(psycopg2.Error)
    def pg_error(e):
        friendly = _friendly_db_error(e)
        log.exception("Database error")
        if friendly and request.method == "POST":
            flash(friendly, "error")
            return back()
        return render_template("error.html", code=503,
                               title="Database not reachable",
                               message="The database did not respond. Check your internet and try "
                                       "again in a minute. If it keeps happening, check Supabase."), 503

    @app.errorhandler(400)
    def bad(e):
        return render_template("error.html", code=400, title="Form expired",
                               message=getattr(e, "description", "")), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, title="Not allowed",
                               message="Your account role cannot open this page. Ask the owner."), 403

    @app.errorhandler(404)
    def nf(e):
        return render_template("error.html", code=404, title="Page not found",
                               message="This page does not exist or the record was removed."), 404

    @app.errorhandler(500)
    def ise(e):
        return render_template("error.html", code=500, title="Something went wrong",
                               message="The change was not saved. Try again; if it repeats, "
                                       "note what you were doing and tell the owner."), 500

    @app.route("/service-worker.js")
    def service_worker():
        resp = app.send_static_file("service-worker.js")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    @app.route("/healthz")
    def health():
        try:
            db.one("SELECT 1 AS ok")
            return {"ok": True, "version": VERSION}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}, 503

    return app
