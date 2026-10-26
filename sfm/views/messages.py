"""Bill Message: photos of a transporter's bills → each bill's date + TOTAL → the message sent to the transporter."""
import json
import uuid as _u

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

from .. import db
from ..security import audit, current_user, login_required
from ..util import UserError, clean, today

bp = Blueprint("messages", __name__)


@bp.route("/bill-message")
@login_required("transport_enter|bills")
def index():
    from ..scan import gemini_ready
    with db.tx() as cur:
        cur.execute("SELECT id, name FROM parties WHERE active ORDER BY name")
        parties = cur.fetchall()
        cur.execute("""SELECT b.*, p.name AS party_name, (SELECT COUNT(*) FROM msg_bills m WHERE m.batch_id=b.id) AS bills
                       FROM msg_batches b LEFT JOIN parties p ON p.id=b.party_id ORDER BY b.id DESC LIMIT 20""")
        recent = cur.fetchall()
    return render_template("messages/index.html", ready=gemini_ready("message"), parties=parties, recent=recent)


@bp.route("/bill-message/batch", methods=["POST"])
@login_required("transport_enter|bills")
def batch_new():
    pid = int(request.form.get("party") or 0) or None
    with db.tx() as cur:
        cur.execute("INSERT INTO msg_batches (created_by, party_id) VALUES (%s,%s) RETURNING id", (current_user()["username"], pid))
        bid = cur.fetchone()["id"]
    return jsonify({"ok": True, "id": bid, "review": url_for("messages.batch", bid=bid)})


@bp.route("/bill-message/batch/<int:bid>/page", methods=["POST"])
@login_required("transport_enter|bills")
def batch_page(bid):
    from .. import storage
    from ..scan import bill_summary, indian, match_party, read_page
    try:
        b = db.one("SELECT * FROM msg_batches WHERE id=%s", (bid,))
        if not b:
            raise UserError("Not found.")
        f = request.files.get("photo")
        if not f or not f.filename:
            raise UserError("No photo received.")
        data = f.read(4_000_001)
        if len(data) > 4_000_000:
            raise UserError("Photo is too big.")
        mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        raw = read_page(data, mime, job="message")
        path = ""
        if storage.ready():
            try:
                path = f"bill-messages/{today():%Y-%m}/{_u.uuid4().hex}.{'png' if mime == 'image/png' else 'jpg'}"
                storage.put(path, data, mime)
            except Exception:
                path = ""
        s = bill_summary(raw)
        with db.tx() as cur:
            if not b["party_id"]:
                p, _ = match_party(cur, raw.get("party"))
                if p:
                    cur.execute("UPDATE msg_batches SET party_id=%s WHERE id=%s AND party_id IS NULL", (p["id"], bid))
            cur.execute("INSERT INTO msg_bills (batch_id, photo_path, raw) VALUES (%s,%s,%s)", (bid, path, json.dumps(raw)))
        return jsonify({"ok": True, "label": s["label"], "total": indian(s["total"]), "checked": s["ok"]})
    except UserError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/bill-message/<int:bid>")
@login_required("transport_enter|bills")
def batch(bid):
    from ..scan import bill_message, bill_summary, indian
    with db.tx() as cur:
        cur.execute("SELECT * FROM msg_batches WHERE id=%s", (bid,))
        b = cur.fetchone()
        if not b:
            abort(404)
        cur.execute("SELECT * FROM msg_bills WHERE batch_id=%s ORDER BY id", (bid,))
        rows = cur.fetchall()
        cur.execute("SELECT id, name FROM parties WHERE active ORDER BY name")
        parties = cur.fetchall()
    bills = []
    for r in rows:
        s = bill_summary(r["raw"] or {})
        s.update(id=r["id"], photo=bool(r["photo_path"]))
        bills.append(s)
    bills.sort(key=lambda x: (x["date"] is None, x["date"] or today(), x["label"]))
    labels = [x["label"] for x in bills]
    for x in bills:
        x["dup"] = labels.count(x["label"]) > 1
    party = next((p for p in parties if p["id"] == b["party_id"]), None)
    name = party["name"] if party else (bills[0]["party"] if bills else "")
    return render_template("messages/batch.html", b=b, bills=bills, parties=parties, party_name=name,
                           message=bill_message(name, bills), indian=indian)


@bp.route("/bill-message/photo/<int:mid>")
@login_required("transport_enter|bills")
def photo(mid):
    from .. import storage
    r = db.one("SELECT photo_path FROM msg_bills WHERE id=%s", (mid,))
    if not r or not r["photo_path"]:
        abort(404)
    link = storage.signed_download(r["photo_path"], 1800)
    if not link:
        abort(404)
    return redirect(link)


@bp.route("/bill-message/<int:bid>/sent", methods=["POST"])
@login_required("transport_enter|bills")
def sent(bid):
    """Keeps the final text (as copied / shared) in the history."""
    text = (request.form.get("text") or "")[:4000]
    pid = int(request.form.get("party") or 0) or None
    with db.tx() as cur:
        cur.execute("UPDATE msg_batches SET sent_text=%s, party_id=COALESCE(%s, party_id) WHERE id=%s", (text, pid, bid))
        audit(cur, "create", "bill_message", bid, (text.splitlines()[0] if text else "") + " — bill message prepared")
    return jsonify({"ok": True})
