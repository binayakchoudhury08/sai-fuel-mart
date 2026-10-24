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
VERSION = "3.6.2"


def _secret_key():
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    # Fallback that is stable across workers/restarts and not guessable
    # without the DB password. Setting SECRET_KEY in Render is still better.
    seed = os.environ.get("DATABASE_URL", "") + "|sfm-v2"
    log.warning("SECRET_KEY not set; deriving one from DATABASE_URL. Set SECRET_KEY in Vercel → Settings → Environment Variables.")
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
    app = Flask(__name__, static_folder="../public/static", static_url_path="/static", template_folder="../templates")
    app.secret_key = _secret_key()
    on_https = bool(os.environ.get("VERCEL") or os.environ.get("RENDER") or os.environ.get("FORCE_HTTPS") == "1")
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_https,
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=48 * 1024 * 1024,
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

    from .views import admin, auth, day, main, parties, lube, proofs, reports, staff, tanks, transport
    for bp in (auth.bp, main.bp, day.bp, parties.bp, lube.bp, tanks.bp, tanks.api, staff.bp, reports.bp,
               admin.bp, transport.bp, proofs.bp):
        app.register_blueprint(bp)

    @app.before_request
    def load_user():
        g.user = None
        if (request.endpoint in ("static", "service_worker", "health")
                or (request.endpoint or "").startswith(("api.", "api_v1."))
                or request.path.startswith("/api/v1/") or request.path in ("/api/tank-sync", "/api/cron/daily")):
            return
        uid = session.get("uid")
        if uid:
            import time as _t
            cached = session.get("u")
            fresh = cached and _t.time() - float(session.get("u_at", 0)) < 120
            # pages re-use the signed-in user for 2 minutes (saves a database trip on every page);
            # anything that saves data, and every 2 minutes, re-checks the database
            if fresh and request.method == "GET":
                g.user = dict(cached)
                if session.get("u_station"):
                    g.station_name = session["u_station"]
            else:
                u = db.one("SELECT id, username, full_name, role, active, must_change_pw, password_hash, "
                           "(SELECT value FROM settings WHERE key='station_name') AS station_name "
                           "FROM users WHERE id=%s", (uid,))
                stamp = hashlib.sha256(u["password_hash"].encode()).hexdigest()[:16] if u else None
                if not u or not u["active"] or session.get("pw") != stamp:
                    session.clear()
                else:
                    if u.get("station_name"):
                        g.station_name = u["station_name"]
                    u = dict(u)
                    u.pop("password_hash", None)
                    station = u.pop("station_name", None)
                    g.user = u
                    session["u"] = u
                    session["u_at"] = _t.time()
                    session["u_station"] = station
        if g.get("user"):
            from .grants import close_ended
            close_ended()
        if request.method == "POST" and request.routing_exception is None \
                and not (request.endpoint or "").startswith(("api.", "api_v1.")):
            check_csrf()

    @app.after_request
    def compress(resp):
        import gzip
        if (resp.direct_passthrough or resp.status_code < 200 or resp.status_code >= 300
                or "gzip" not in (request.headers.get("Accept-Encoding") or "")
                or resp.headers.get("Content-Encoding")
                or resp.mimetype not in ("text/html", "application/json", "text/css", "application/javascript",
                                         "image/svg+xml")):
            return resp
        data = resp.get_data()
        if len(data) < 1400:
            return resp
        resp.set_data(gzip.compress(data, compresslevel=5))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(resp.get_data()))
        resp.vary.add("Accept-Encoding")
        return resp

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(self)")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if g.get("user") and resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def inject():
        from .security import active_grants
        def alert_count():
            """Bell badge: recounted at most every 2 minutes per signed-in session."""
            import time as _t
            if not g.get("user"):
                return 0
            if _t.time() - float(session.get("ac_at", 0)) > 120:
                try:
                    from .automation import compute
                    with db.tx() as cur:
                        session["ac"] = len(compute(cur, can))
                except Exception:
                    session["ac"] = 0
                session["ac_at"] = _t.time()
            return session.get("ac", 0)
        def brand_tagline():
            if "brand_tagline" not in g:
                try:
                    r = db.one("SELECT value FROM settings WHERE key='brand_tagline'")
                    g.brand_tagline = (r["value"] if r else "") or "Fuel station"
                except Exception:
                    g.brand_tagline = "Fuel station"
            return g.brand_tagline
        def station_place():
            if "station_place" not in g:
                try:
                    r = db.one("SELECT value FROM settings WHERE key='station_place'")
                    g.station_place = (r["value"] if r else "") or ""
                except Exception:
                    g.station_place = ""
            return g.station_place
        def banner_set():
            if "banner_set" not in g:
                try:
                    r = db.one("SELECT value FROM settings WHERE key='banner_photo_path'")
                    g.banner_set = bool(r and r["value"])
                except Exception:
                    g.banner_set = False
            return g.banner_set
        return {"banner_set": banner_set, "station_place": station_place, "brand_tagline": brand_tagline, "csrf_token": csrf_token, "form_token": new_form_token, "user": g.get("user"),
                "my_grants": active_grants, "alert_count": alert_count, "now": __import__("sfm.util", fromlist=["now_ist"]).now_ist(),
                "can": can, "today": today(), "ROLE_LABEL": ROLE_LABEL, "VERSION": VERSION,
                "station_name": _station_name()}

    _name_cache = {"v": None, "t": 0.0}

    def _station_name():
        if "station_name" not in g:
            import time as _t
            if _name_cache["v"] and _t.time() - _name_cache["t"] < 300:
                g.station_name = _name_cache["v"]
            else:
                try:
                    r = db.one("SELECT value FROM settings WHERE key='station_name'")
                    g.station_name = r["value"] if r else "Sai Fuel Mart"
                    _name_cache.update(v=g.station_name, t=_t.time())
                except Exception:
                    g.station_name = "Sai Fuel Mart"
        return g.station_name

    from .util import crore, rs
    app.jinja_env.filters.update(inr=inr, qty=qty, d=fmt_date, rs=rs, cr=crore)

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
    def service_worker():   # on Vercel the CDN serves public/service-worker.js directly
        from flask import send_from_directory
        resp = send_from_directory(os.path.join(os.path.dirname(app.root_path), "public"), "service-worker.js")
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
