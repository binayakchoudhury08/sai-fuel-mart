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
from ..util import ZERO, UserError, clean, now_ist, parse_date, today

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
def _upload_to_supabase(file, folder, video=False):
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    bucket = os.environ.get("SUPABASE_BUCKET", "proofs")
    if not base or not key:
        raise UserError("Photo storage is not set up. Add SUPABASE_URL and SUPABASE_SERVICE_KEY in Render.")
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = (".mp4", ".mov", ".3gp", ".webm", ".mkv") if video else (".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf")
    if ext not in allowed:
        raise UserError("Upload a video (MP4/MOV/3GP/WEBM)." if video else "Upload a photo (JPG/PNG/WEBP) or a PDF.")
    path = f"{folder}/{uuid.uuid4().hex}{ext}"
    data = file.read()
    r = requests.post(f"{base}/storage/v1/object/{bucket}/{path}", data=data, timeout=40,
                      headers={"Authorization": f"Bearer {key}", "apikey": key,
                               "Content-Type": file.mimetype or "application/octet-stream", "x-upsert": "false"})
    if r.status_code >= 300:
        raise UserError(f"Upload failed ({r.status_code}). Check the '{bucket}' bucket exists and is public.")
    return f"{base}/storage/v1/object/public/{bucket}/{path}"


PROOF_TYPES = ["MS Dip Check", "HSD Dip Check", "MS Nozzle Testing", "HSD Nozzle Testing", "Stock Check",
               "Tanker Challan", "Meter Reading", "Expense Bill", "Cash Deposit", "Density Check", "General"]
STOCK_STATUS = ["", "Available", "No Stock", "Low Stock"]


@bp.route("/proofs", methods=["GET", "POST"])
@login_required()
def proofs():
    if request.method == "POST":
        photo = request.files.get("photo")
        video = request.files.get("video")
        if (not photo or not photo.filename) and (not video or not video.filename):
            raise UserError("Add a photo or a video.")
        d = parse_date(request.form.get("business_date") or today().isoformat())
        if d > today():
            raise UserError("Date cannot be in the future.")
        cat = request.form.get("category") if request.form.get("category") in PROOF_TYPES else "General"
        status = request.form.get("stock_status") if request.form.get("stock_status") in STOCK_STATUS else ""
        url = _upload_to_supabase(photo, d.strftime("%Y-%m")) if photo and photo.filename else ""
        vurl = _upload_to_supabase(video, d.strftime("%Y-%m") + "/video", video=True) if video and video.filename else ""
        fuel = "MS" if cat.startswith("MS ") else ("HSD" if cat.startswith("HSD ") else None)
        with db.tx() as cur:
            claim_form_token(cur)
            cur.execute("""INSERT INTO proofs (business_date, category, note, file_url, video_url, fuel_code, stock_status,
                                               proof_time, latitude, longitude, created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (d, cat, clean(request.form.get("note"), 300), url, vurl, fuel, status,
                         now_ist().strftime("%I:%M %p"), clean(request.form.get("lat"), 20) or None,
                         clean(request.form.get("lng"), 20) or None, current_user()["username"]))
            pid = cur.fetchone()["id"]
            audit(cur, "create", "proof", pid, f"Uploaded {cat} proof" + (f" ({status})" if status else ""),
                  business_date=d)
        flash("Proof saved.", "success")
        return redirect(url_for("main.proofs", d=d.isoformat()))
    d = request.args.get("d")
    cat = request.args.get("category") or ""
    sql = "SELECT * FROM proofs WHERE TRUE"
    p = []
    if d:
        sql += " AND business_date=%s"; p.append(parse_date(d))
    if cat:
        sql += " AND category=%s"; p.append(cat)
    rows = db.query(sql + " ORDER BY business_date DESC, id DESC LIMIT 200", p)
    configured = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))
    return render_template("proofs.html", rows=rows, configured=configured, TYPES=PROOF_TYPES, STATUS=STOCK_STATUS,
                           fd=d or "", fcat=cat)


@bp.route("/proofs/report.pdf")
@login_required()
def proof_pdf():
    """Old 'Proof PDF': all proofs for a date (and type) with the photos printed."""
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer
    from flask import send_file
    from ..exports import _footer_fn, _letterhead
    from ..services import settings_map
    d = parse_date(request.args.get("d") or today().isoformat())
    cat = request.args.get("category") or ""
    with db.tx() as cur:
        st = settings_map(cur)
        sql, prm = "SELECT * FROM proofs WHERE business_date=%s", [d]
        if cat:
            sql += " AND category=%s"; prm.append(cat)
        cur.execute(sql + " ORDER BY category, id", prm)
        rows = cur.fetchall()
    buf = BytesIO()
    width = A4[0] - 40
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=38)
    ss = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=11, leading=14)
    m = ParagraphStyle("m", parent=ss["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#475467"))
    el = _letterhead(st, f"{cat or 'ALL'} PROOFS".upper(), [f"Date: {d:%d-%m-%Y}", f"{len(rows)} record(s)"], width)
    if not rows:
        el.append(Paragraph("No proofs were uploaded for this date and type.", m))
    for r in rows:
        block = [Paragraph(f"{r['category']}" + (f" — {r['stock_status']}" if r["stock_status"] else ""), h),
                 Paragraph(" · ".join(x for x in [r["proof_time"], f"by {r['created_by']}", r["note"],
                                                 r["latitude"] and f"GPS {r['latitude']}, {r['longitude']}"] if x), m)]
        if r["file_url"] and not r["file_url"].lower().endswith(".pdf"):
            try:
                resp = requests.get(r["file_url"], timeout=20)
                resp.raise_for_status()
                img = Image(BytesIO(resp.content))
                ratio = img.imageHeight / float(img.imageWidth or 1)
                w = min(width, 120 * mm)
                img.drawWidth, img.drawHeight = w, min(w * ratio, 150 * mm)
                block.append(Spacer(1, 4)); block.append(img)
            except Exception:
                block.append(Paragraph("(photo could not be loaded for this report)", m))
        if r["video_url"]:
            block.append(Paragraph(f"Video: {r['video_url']}", m))
        block.append(Spacer(1, 12))
        el.append(KeepTogether(block))
    fn = _footer_fn(st, "Proof report", A4)
    doc.build(el, onFirstPage=fn, onLaterPages=fn)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{(cat or 'All')} proofs {d:%d%m%y}.pdf")
