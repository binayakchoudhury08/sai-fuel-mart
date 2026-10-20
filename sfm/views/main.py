import os
import uuid
from datetime import timedelta
from decimal import Decimal

import requests
from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import db
from ..security import audit, can, claim_form_token, current_user, login_required
from ..services import (avg_daily_sale, compute_day, lube_stock, party_balances, range_summary,
                        tank_reconciliation)
from ..util import ZERO, UserError, clean, parse_date, today

bp = Blueprint("main", __name__)


@bp.route("/")
def home():
    return redirect(url_for("main.dashboard"))


@bp.route("/dashboard")
@login_required()
def dashboard():
    t0 = today()
    with db.tx() as cur:
        td = compute_day(cur, t0)
        span = range_summary(cur, t0 - timedelta(days=13), t0)
        parties = party_balances(cur, active_only=False)
        lube = [p for p in lube_stock(cur) if p["active"] and Decimal(p["stock"]) <= Decimal(p["reorder_level"])]
        tanks = tank_reconciliation(cur, t0)
        if all(r["actual"] is None for r in tanks):
            tanks = tank_reconciliation(cur, t0 - timedelta(days=1))
        days_left = {}
        for r in tanks:
            avg = avg_daily_sale(cur, r["tank"]["fuel_code"], t0)
            if r["actual"] is not None and avg > 0:
                days_left[r["tank"]["id"]] = (r["actual"] / avg).quantize(Decimal("0.1"))
        cur.execute("""SELECT business_date, status FROM days
                       WHERE business_date BETWEEN %s AND %s AND status <> 'locked' AND NOT is_legacy
                       ORDER BY business_date""", (t0 - timedelta(days=30), t0 - timedelta(days=1)))
        pending = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM nozzles WHERE active")
        setup_nozzles = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM fuel_rates")
        setup_rates = cur.fetchone()["n"]
    by_date = {r["date"]: r for r in span}
    chart = []
    for i in range(13, -1, -1):
        d = t0 - timedelta(days=i)
        r = by_date.get(d)
        chart.append({"date": d, "sale": r["gross_sale"] if r else ZERO,
                      "diff": r["difference"] if r else None, "status": r["status"] if r else None})
    peak = max((c["sale"] for c in chart), default=ZERO) or Decimal(1)
    month_rows = [r for r in span if r["date"].month == t0.month]
    dues = sorted((p for p in parties if Decimal(p["balance"]) > 0), key=lambda p: -Decimal(p["balance"]))
    total_due = sum((Decimal(p["balance"]) for p in dues), ZERO)
    diffs = [c["diff"] for c in chart[-7:] if c["diff"] is not None]
    return render_template("dashboard.html", td=td, chart=chart, peak=peak, dues=dues[:6],
                           total_due=total_due, due_count=len(dues), lube=lube, tanks=tanks,
                           days_left=days_left, pending=pending, week_diff=sum(diffs, ZERO),
                           week_diff_days=len(diffs),
                           needs_setup=(setup_nozzles == 0 or setup_rates == 0))


# ------------------------------------------------------------------ photo proofs
def _upload_to_supabase(file, folder):
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    bucket = os.environ.get("SUPABASE_BUCKET", "proofs")
    if not base or not key:
        raise UserError("Photo storage is not set up. Add SUPABASE_URL and SUPABASE_SERVICE_KEY in Render.")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"):
        raise UserError("Upload a photo (JPG/PNG/WEBP) or a PDF.")
    path = f"{folder}/{uuid.uuid4().hex}{ext}"
    data = file.read()
    r = requests.post(f"{base}/storage/v1/object/{bucket}/{path}", data=data, timeout=40,
                      headers={"Authorization": f"Bearer {key}", "apikey": key,
                               "Content-Type": file.mimetype or "application/octet-stream", "x-upsert": "false"})
    if r.status_code >= 300:
        raise UserError(f"Upload failed ({r.status_code}). Check the '{bucket}' bucket exists and is public.")
    return f"{base}/storage/v1/object/public/{bucket}/{path}"


@bp.route("/proofs", methods=["GET", "POST"])
@login_required()
def proofs():
    if request.method == "POST":
        f = request.files.get("photo")
        if not f or not f.filename:
            raise UserError("Choose a photo to upload.")
        d = parse_date(request.form.get("business_date") or today().isoformat())
        if d > today():
            raise UserError("Date cannot be in the future.")
        url = _upload_to_supabase(f, d.strftime("%Y-%m"))
        with db.tx() as cur:
            claim_form_token(cur)
            cur.execute("""INSERT INTO proofs (business_date, category, note, file_url, latitude, longitude, created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (d, clean(request.form.get("category"), 40) or "General", clean(request.form.get("note"), 300),
                         url, clean(request.form.get("lat"), 20) or None, clean(request.form.get("lng"), 20) or None,
                         current_user()["username"]))
            pid = cur.fetchone()["id"]
            audit(cur, "create", "proof", pid, f"Uploaded proof photo ({request.form.get('category')})",
                  business_date=d)
        flash("Photo saved.", "success")
        return redirect(url_for("main.proofs"))
    rows = db.query("SELECT * FROM proofs ORDER BY business_date DESC, id DESC LIMIT 120")
    configured = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))
    return render_template("proofs.html", rows=rows, configured=configured)
