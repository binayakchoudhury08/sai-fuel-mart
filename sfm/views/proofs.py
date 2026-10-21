"""Photo / video proofs taken LIVE on a phone camera (dip checks, nozzle testing, stock checks…).

- New proofs can only be made from a phone, with the in-page camera (no gallery, no file upload).
  Each photo is stamped with date, time, GPS, proof type and user before it leaves the phone.
- Files go to a Supabase Storage bucket and are served back ONLY through this app to signed-in users,
  so there is no public link that could leak.
- Register with filters, map and video links; PDF report with the photos and scan codes.
"""
import os
import re
import uuid
from datetime import datetime, timedelta
from io import BytesIO

import requests
from flask import (Blueprint, Response, abort, jsonify, redirect, render_template, request, send_file,
                   url_for)

from .. import db
from ..security import audit, claim_form_token, current_user, login_required
from ..services import settings_map
from ..util import IST, UserError, clean, now_ist, parse_date, today

bp = Blueprint("proofs", __name__)

PROOF_TYPES = ["MS Dip Check", "HSD Dip Check", "MS Nozzle Testing", "HSD Nozzle Testing", "Stock Check",
               "Tanker Challan", "Meter Reading", "Density Check", "Cash Deposit", "Expense Bill", "General"]
STOCK_STATUS = ["", "Available", "No Stock", "Low Stock"]
MOBILE_RE = re.compile(r"Android|iPhone|iPad|iPod|Mobile|SaiFuelMartApp", re.I)
MAX_PHOTO = 8 * 1024 * 1024
MAX_VIDEO = 40 * 1024 * 1024


def is_mobile():
    return bool(MOBILE_RE.search(request.headers.get("User-Agent", "")))


# ------------------------------------------------------------------ storage (Supabase Storage)
def _storage():
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    bucket = os.environ.get("SUPABASE_BUCKET", "proofs")
    return base, key, bucket


def storage_ready():
    base, key, _ = _storage()
    return bool(base and key)


def _put(path, data, mime):
    base, key, bucket = _storage()
    r = requests.post(f"{base}/storage/v1/object/{bucket}/{path}", data=data, timeout=60,
                      headers={"Authorization": f"Bearer {key}", "apikey": key, "Content-Type": mime,
                               "x-upsert": "false"})
    if r.status_code >= 300:
        raise UserError(f"Could not save the file ({r.status_code}). Check the '{bucket}' bucket exists in Supabase "
                        "Storage.")


def _get(path):
    base, key, bucket = _storage()
    r = requests.get(f"{base}/storage/v1/object/{bucket}/{path}", timeout=60,
                     headers={"Authorization": f"Bearer {key}", "apikey": key})
    if r.status_code >= 300:
        return None, None
    return r.content, r.headers.get("Content-Type", "application/octet-stream")


def _fetch_file(row, kind):
    """Bytes of a proof's photo/video: private path (new) or the old app's public URL (imported)."""
    path = row["photo_path"] if kind == "photo" else row["video_path"]
    if path:
        return _get(path)
    url = row["file_url"] if kind == "photo" else row["video_url"]
    base, key, _ = _storage()
    # old-app files: if the link is in our own Supabase storage, read it privately (works after the
    # bucket is made private, so no public link is needed any more)
    m = re.search(r"/storage/v1/object/(?:public|sign|authenticated)/([^?]+)", url or "")
    if m and base and key and url.startswith(base):
        r = requests.get(f"{base}/storage/v1/object/{m.group(1)}", timeout=60,
                         headers={"Authorization": f"Bearer {key}", "apikey": key})
        if r.ok:
            return r.content, r.headers.get("Content-Type", "application/octet-stream")
    if url and url.startswith("https://"):
        try:
            r = requests.get(url, timeout=30)
            if r.ok:
                return r.content, r.headers.get("Content-Type", "application/octet-stream")
        except requests.RequestException:
            pass
    return None, None


# ------------------------------------------------------------------ register
@bp.route("/proofs")
@login_required()
def index():
    d = request.args.get("d") or ""
    cat = request.args.get("category") or ""
    sql, p = "SELECT * FROM proofs WHERE TRUE", []
    if d:
        sql += " AND business_date=%s"
        p.append(parse_date(d))
    if cat:
        sql += " AND category=%s"
        p.append(cat)
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    with db.tx() as cur:
        cur.execute(sql.replace("SELECT *", "SELECT COUNT(*) AS n"), p)
        n = cur.fetchone()["n"]
        cur.execute(sql + " ORDER BY business_date DESC, id DESC LIMIT 60 OFFSET %s", p + [(page - 1) * 60])
        rows = cur.fetchall()
        cur.execute("""SELECT category, COUNT(*) AS n FROM proofs WHERE business_date=%s GROUP BY category""", (today(),))
        today_counts = {r["category"]: r["n"] for r in cur.fetchall()}
    return render_template("proofs/index.html", rows=rows, TYPES=PROOF_TYPES, fd=d, fcat=cat, mobile=is_mobile(),
                           configured=storage_ready(), page=page, pages=max(1, -(-n // 60)), count=n,
                           today_counts=today_counts)


@bp.route("/proofs/new")
@login_required()
def new():
    cat = request.args.get("category") if request.args.get("category") in PROOF_TYPES else ""
    return render_template("proofs/camera.html", TYPES=PROOF_TYPES, STATUS=STOCK_STATUS, cat=cat,
                           mobile=is_mobile(), configured=storage_ready(), station=settings_station())


def settings_station():
    try:
        return db.one("SELECT value FROM settings WHERE key='station_name'")["value"]
    except Exception:
        return "Sai Fuel Mart"


@bp.route("/proofs/new", methods=["POST"])
@login_required()
def create():
    """Receives the live-camera capture from the phone. Returns JSON (the page uploads with fetch)."""
    def fail(msg, code=400):
        return jsonify({"ok": False, "error": msg}), code
    if not is_mobile():
        return fail("Proofs can only be taken on a phone with its camera.")
    if not storage_ready():
        return fail("Photo storage is not set up. The owner must add SUPABASE_URL and SUPABASE_SERVICE_KEY in Render.")
    cat = request.form.get("category")
    if cat not in PROOF_TYPES:
        return fail("Choose what this proof is for.")
    mode = request.form.get("capture_mode")
    if mode not in ("live", "camera-input"):
        return fail("Use the camera on this page to take the photo.")
    try:
        taken = datetime.fromtimestamp(int(request.form.get("captured_at", "0")) / 1000, IST)
    except (ValueError, OverflowError, OSError):
        return fail("Photo time missing. Take the photo again.")
    if abs((now_ist() - taken).total_seconds()) > 15 * 60:
        return fail("This photo was not taken just now. Take a fresh photo.")
    photo = request.files.get("photo")
    if not photo:
        return fail("Take the photo first.")
    pdata = photo.read(MAX_PHOTO + 1)
    if len(pdata) > MAX_PHOTO or pdata[:3] != b"\xff\xd8\xff":
        return fail("The photo is not a valid camera picture.")
    video = request.files.get("video")
    vdata, vext, vmime = None, None, None
    if video and video.filename:
        vdata = video.read(MAX_VIDEO + 1)
        if len(vdata) > MAX_VIDEO:
            return fail("Video is too long. Keep it under 30 seconds.")
        if vdata[:4] == b"\x1a\x45\xdf\xa3":
            vext, vmime = "webm", "video/webm"
        elif vdata[4:8] == b"ftyp":
            vext, vmime = "mp4", "video/mp4"
        else:
            return fail("The video is not a valid camera recording.")
    d = taken.date()
    folder = d.strftime("%Y-%m")
    pid = uuid.uuid4().hex
    ppath = f"{folder}/{d.isoformat()}_{re.sub('[^A-Za-z0-9]+', '-', cat)}_{pid}.jpg"
    _put(ppath, pdata, "image/jpeg")
    vpath = ""
    if vdata:
        vpath = f"{folder}/{d.isoformat()}_{re.sub('[^A-Za-z0-9]+', '-', cat)}_{pid}.{vext}"
        _put(vpath, vdata, vmime)
    lat, lng = clean(request.form.get("lat"), 20), clean(request.form.get("lng"), 20)
    try:
        acc = round(float(request.form.get("acc") or 0), 1) or None
    except ValueError:
        acc = None
    status = request.form.get("stock_status") if request.form.get("stock_status") in STOCK_STATUS else ""
    fuel = "MS" if cat.startswith("MS ") else ("HSD" if cat.startswith("HSD ") else None)
    with db.tx() as cur:
        claim_form_token(cur)
        cur.execute("""INSERT INTO proofs (business_date, category, note, file_url, video_url, fuel_code, stock_status,
                                           proof_time, latitude, longitude, created_by, capture_mode, captured_at,
                                           gps_accuracy_m, device, photo_path, video_path, photo_bytes, video_bytes)
                       VALUES (%s,%s,%s,'','',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (d, cat, clean(request.form.get("note"), 300), fuel, status, taken.strftime("%I:%M:%S %p"),
                     lat or None, lng or None, current_user()["username"], mode, taken, acc,
                     clean(request.headers.get("User-Agent"), 120), ppath, vpath, len(pdata),
                     len(vdata) if vdata else None))
        new_id = cur.fetchone()["id"]
        audit(cur, "create", "proof", new_id, f"{cat} proof taken live at {taken:%I:%M %p}"
              + (f" ({status})" if status else "") + (" with video" if vdata else "")
              + (f", GPS {lat},{lng}" if lat else ", no GPS"), business_date=d)
    return jsonify({"ok": True, "id": new_id, "redirect": url_for("proofs.index", d=d.isoformat())})


@bp.route("/proofs/<int:pid>/<kind>")
@login_required()
def file(pid, kind):
    if kind not in ("photo", "video"):
        abort(404)
    row = db.one("SELECT * FROM proofs WHERE id=%s", (pid,))
    if not row:
        abort(404)
    data, mime = _fetch_file(row, kind)
    if data is None:
        abort(404)
    resp = Response(data, mimetype=mime)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.headers["Content-Disposition"] = f"inline; filename=proof-{pid}-{kind}"
    return resp


# ------------------------------------------------------------------ PDF with photos
@bp.route("/proofs/report.pdf")
@login_required()
def pdf():
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from ..exports import _footer_fn, _letterhead
    d = parse_date(request.args.get("d") or today().isoformat())
    cat = request.args.get("category") or ""
    with db.tx() as cur:
        st = settings_map(cur)
        sql, prm = "SELECT * FROM proofs WHERE business_date=%s", [d]
        if cat:
            sql += " AND category=%s"
            prm.append(cat)
        cur.execute(sql + " ORDER BY category, captured_at NULLS LAST, id", prm)
        rows = cur.fetchall()
    site = request.url_root.rstrip("/")

    def qr(value, size=26 * mm):
        w = QrCodeWidget(value)
        b = w.getBounds()
        dr = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
        dr.add(w)
        return dr

    buf = BytesIO()
    width = A4[0] - 40
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=38,
                            title=f"Proofs {d:%d-%m-%Y}")
    ss = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=11.5, leading=14)
    m = ParagraphStyle("m", parent=ss["Normal"], fontSize=8.5, leading=11.5, textColor=colors.HexColor("#344054"))
    small = ParagraphStyle("s", parent=m, fontSize=7.5, alignment=1)
    el = _letterhead(st, f"{(cat or 'ALL')} PROOFS".upper(), [f"Date: {d:%d-%m-%Y}", f"{len(rows)} record(s)"], width)
    if not rows:
        el.append(Paragraph("No proofs were taken for this date and type.", m))
    for r in rows:
        taken = r["captured_at"].astimezone(IST).strftime("%d-%m-%Y %I:%M:%S %p") if r["captured_at"] else (r["proof_time"] or "")
        info = [f"<b>Taken:</b> {taken}", f"<b>By:</b> {r['created_by'] or '-'}",
                f"<b>Status:</b> {r['stock_status'] or '-'}",
                f"<b>Location:</b> {r['latitude']}, {r['longitude']}" + (f" (±{r['gps_accuracy_m']} m)" if r["gps_accuracy_m"] else "")
                if r["latitude"] else "<b>Location:</b> not available",
                f"<b>Capture:</b> {'live camera' if r['capture_mode'] == 'live' else ('phone camera' if r['capture_mode'] else 'imported from old app')}"]
        if r["note"]:
            info.append(f"<b>Note:</b> {r['note']}")
        codes = []
        if r["latitude"]:
            codes.append([qr(f"https://maps.google.com/?q={r['latitude']},{r['longitude']}"), Paragraph("Scan: map", small)])
        if r["video_path"] or r["video_url"]:
            codes.append([qr(f"{site}{url_for('proofs.file', pid=r['id'], kind='video')}"),
                          Paragraph("Scan: video (sign-in needed)", small)])
        cells_right = [Paragraph("<br/>".join(info), m)]
        for c in codes:
            cells_right += [Spacer(1, 4), c[0], c[1]]
        img_cell = Paragraph("(photo could not be loaded)", m)
        data, mime = _fetch_file(r, "photo")
        if data and (mime or "").startswith("image") or (data and data[:3] == b"\xff\xd8\xff"):
            try:
                img = Image(BytesIO(data))
                ratio = img.imageHeight / float(img.imageWidth or 1)
                w = width * 0.55
                img.drawWidth, img.drawHeight = w, min(w * ratio, 120 * mm)
                if w * ratio > 120 * mm:
                    img.drawWidth = 120 * mm / ratio
                img_cell = img
            except Exception:
                pass
        t = Table([[img_cell, cells_right]], colWidths=[width * 0.57, width * 0.43])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD2DB")),
                               ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
        el.append(KeepTogether([Paragraph(r["category"], h), Spacer(1, 3), t, Spacer(1, 12)]))
    fn = _footer_fn(st, "Proof report", A4)
    doc.build(el, onFirstPage=fn, onLaterPages=fn)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{(cat or 'All')} proofs {d:%d%m%y}.pdf")


# old address used by earlier versions
@bp.route("/proofs/old-report.pdf")
@login_required()
def old_pdf():
    return redirect(url_for("proofs.pdf", **request.args))
