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


# ------------------------------------------------------------------ storage + capture tickets
from .. import storage


def storage_ready():
    return storage.ready()


def _serializer():
    from flask import current_app
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.secret_key, salt="live-capture")


def issue_capture(prefix, label, want_video=False, video_ext="webm"):
    """Paths + one-time upload links for a live capture, and a signed ticket that proves
    this signed-in user asked for exactly these paths (checked when saving)."""
    d = today()
    tag = re.sub("[^A-Za-z0-9]+", "-", label)[:40]
    uid = uuid.uuid4().hex
    photo = f"{prefix}/{d:%Y-%m}/{d.isoformat()}_{tag}_{uid}.jpg"
    video = f"{prefix}/{d:%Y-%m}/{d.isoformat()}_{tag}_{uid}.{video_ext}" if want_video else ""
    out = {"photo_url": storage.signed_upload(photo)}
    if video:
        out["video_url"] = storage.signed_upload(video)
    out["ticket"] = _serializer().dumps({"u": current_user()["id"], "p": photo, "v": video})
    return out


def check_capture(ticket, require_video=False):
    """Validates the ticket and the uploaded files. Returns (photo_path, video_path, photo_bytes, video_bytes)."""
    from itsdangerous import BadSignature, SignatureExpired
    try:
        t = _serializer().loads(ticket or "", max_age=2 * 3600)
    except (BadSignature, SignatureExpired):
        raise UserError("This capture expired. Take the photo again.")
    if t.get("u") != current_user()["id"]:
        raise UserError("This capture belongs to another user.")
    data, _, size = storage.get(t["p"])
    if not data or data[:3] != b"\xff\xd8\xff" or size > MAX_PHOTO:
        raise UserError("The photo did not reach storage. Check the internet and take it again.")
    vsize = None
    if t.get("v"):
        head, _, vsize = storage.get(t["v"], first_bytes=16)
        if head:
            if not (head[:4] == b"\x1a\x45\xdf\xa3" or head[4:8] == b"ftyp") or vsize > MAX_VIDEO:
                raise UserError("The video is not a valid camera recording.")
        else:
            t["v"], vsize = "", None
    return t["p"], t.get("v", ""), size, vsize


def _fetch_file(row, kind):
    """Bytes of a proof photo (for the PDF): private path or an old-app link."""
    path = row["photo_path"] if kind == "photo" else row["video_path"]
    if path:
        data, mime, _ = storage.get(path)
        return data, mime
    url = row["file_url"] if kind == "photo" else row["video_url"]
    p = _own_path(url)
    if p:
        data, mime, _ = storage.get(p)
        return data, mime
    if url and url.startswith("https://"):
        try:
            r = requests.get(url, timeout=30)
            if r.ok:
                return r.content, r.headers.get("Content-Type", "application/octet-stream")
        except requests.RequestException:
            pass
    return None, None


def _own_path(url):
    """Old-app public link inside our own bucket → its path (so it can be read privately)."""
    base, _, bucket = storage.config()
    m = re.search(r"/storage/v1/object/(?:public|sign|authenticated)/([^/]+)/([^?]+)", url or "")
    if m and base and (url or "").startswith(base) and m.group(1) == bucket:
        return m.group(2)
    return None


# ------------------------------------------------------------------ register
@bp.route("/proofs")
@login_required()
def index():
    d = request.args.get("d") or today().isoformat()
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
        cur.execute("""SELECT category, COUNT(*) AS n FROM proofs WHERE business_date=%s GROUP BY category ORDER BY category""",
                    (parse_date(d),))
        day_counts = {r["category"]: r["n"] for r in cur.fetchall()}
    return render_template("proofs/index.html", rows=rows, TYPES=PROOF_TYPES, fd=d, fcat=cat, mobile=is_mobile(),
                           configured=storage_ready(), page=page, pages=max(1, -(-n // 60)), count=n,
                           today_counts=today_counts, day_counts=day_counts, dd=parse_date(d),
                           prev_day=parse_date(d) - timedelta(days=1),
                           next_day=parse_date(d) + timedelta(days=1) if parse_date(d) < today() else None)


@bp.route("/proofs/new")
@login_required("proofs")
def new():
    cat = request.args.get("category") if request.args.get("category") in PROOF_TYPES else ""
    return render_template("proofs/camera.html", TYPES=PROOF_TYPES, STATUS=STOCK_STATUS, cat=cat,
                           mobile=is_mobile(), configured=storage_ready(), station=settings_station())


def settings_station():
    try:
        return db.one("SELECT value FROM settings WHERE key='station_name'")["value"]
    except Exception:
        return "Sai Fuel Mart"


@bp.route("/proofs/start", methods=["POST"])
@login_required("proofs")
def start():
    """Step 1 of saving: the phone gets one-time upload links (files go straight to storage)."""
    if not is_mobile():
        return jsonify({"ok": False, "error": "Proofs can only be taken on a phone with its camera."}), 400
    if not storage_ready():
        return jsonify({"ok": False, "error": "Photo storage is not set up yet. Ask the owner."}), 400
    cat = request.form.get("category")
    if cat not in PROOF_TYPES:
        return jsonify({"ok": False, "error": "Choose what this proof is for."}), 400
    ext = "mp4" if request.form.get("video_ext") == "mp4" else "webm"
    try:
        return jsonify({"ok": True, **issue_capture("proofs", cat, request.form.get("video") == "1", ext)})
    except UserError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/proofs/new", methods=["POST"])
@login_required("proofs")
def create():
    """Step 2: the files are already in storage; check them and record the proof."""
    def fail(msg, code=400):
        return jsonify({"ok": False, "error": msg}), code
    if not is_mobile():
        return fail("Proofs can only be taken on a phone with its camera.")
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
    try:
        ppath, vpath, psize, vsize = check_capture(request.form.get("ticket"))
    except UserError as e:
        return fail(str(e))
    d = taken.date()
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
                     clean(request.headers.get("User-Agent"), 120), ppath, vpath, psize, vsize))
        new_id = cur.fetchone()["id"]
        audit(cur, "create", "proof", new_id, f"{cat} proof taken live at {taken:%I:%M %p}"
              + (f" ({status})" if status else "") + (" with video" if vpath else "")
              + (f", GPS {lat},{lng}" if lat else ", no GPS"), business_date=d)
    return jsonify({"ok": True, "id": new_id, "redirect": url_for("proofs.index", d=d.isoformat())})


@bp.route("/proofs/<int:pid>/<kind>")
@login_required()
def file(pid, kind):
    """Private file → a 10-minute signed link, handed only to signed-in users."""
    if kind not in ("photo", "video"):
        abort(404)
    row = db.one("SELECT * FROM proofs WHERE id=%s", (pid,))
    if not row:
        abort(404)
    path = row["photo_path"] if kind == "photo" else row["video_path"]
    url = row["file_url"] if kind == "photo" else row["video_url"]
    path = path or _own_path(url)
    if path and storage_ready():
        link = storage.signed_download(path, 600)
        if link:
            return redirect(link)
    if url and url.startswith("https://"):
        return redirect(url)
    abort(404)


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
                data = _shrink(data)
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
    return storage.deliver(buf, f"{(cat or 'All')} proofs {d:%d%m%y}.pdf", "application/pdf")


def _shrink(data, max_px=1000, quality=72):
    """Keeps the PDF small enough to download quickly (and under Vercel's size limit)."""
    try:
        from PIL import Image as PI
        im = PI.open(BytesIO(data))
        im.thumbnail((max_px, max_px))
        out = BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception:
        return data


# old address used by earlier versions
@bp.route("/proofs/old-report.pdf")
@login_required()
def old_pdf():
    return redirect(url_for("proofs.pdf", **request.args))
