"""WhatsApp webhook (Meta), background bill-reading workers, and the scheduler tick."""
import json
import os
import secrets as _s
import time

import requests
from flask import Blueprint, g, jsonify, request

from .. import db

bp = Blueprint("api_wa", __name__)


def _internal_ok():
    secret = os.environ.get("CRON_SECRET", "")
    sent = request.headers.get("x-cron-secret") or (request.headers.get("Authorization") or "").replace("Bearer ", "", 1)
    return bool(secret) and _s.compare_digest(sent, secret)


@bp.route("/api/wa/webhook", methods=["GET"])
def verify():
    """Meta checks the webhook once: we answer with the challenge if the verify token matches.
    Opened in a browser (no hub.* fields) it shows a status page, to check the setup."""
    from .. import VERSION, wa
    mine = (os.environ.get("WA_VERIFY_TOKEN") or "").strip()
    if "hub.mode" not in request.args:
        ok = lambda v: "✅ set" if v else "❌ NOT set — add it in Vercel, then Redeploy"
        lines = [f"✅ Sai Fuel Mart WhatsApp webhook is live (version {VERSION}).", "",
                 f"WA_VERIFY_TOKEN: {ok(mine)}", f"WA_TOKEN: {ok(os.environ.get('WA_TOKEN'))}",
                 f"WA_PHONE_ID: {ok(os.environ.get('WA_PHONE_ID'))}", f"WA_APP_SECRET: {ok(os.environ.get('WA_APP_SECRET'))}",
                 f"WA_OWNERS: {len(wa.owners())} number(s)" + ("" if wa.owners() else " — ❌ add e.g. 919876543210:boss"),
                 ("WA_TOKEN validity: " + wa.token_info()[0]) if os.environ.get("WA_TOKEN") else "", "",
                 "In Meta → WhatsApp → Configuration, use exactly this page's address as the Callback URL,",
                 "and the same word as WA_VERIFY_TOKEN as the Verify token.", ""]
        from ..util import IST
        with db.tx() as cur:
            for ph, uname in wa.owners().items():
                cur.execute("SELECT role, active FROM users WHERE username=%s", (uname,))
                u = cur.fetchone()
                lines.append(f"WA_OWNERS {wa.mask(ph)} → website login “{uname}”: " +
                             (f"✅ found ({u['role']})" if u and u['active'] else "❌ no active login with this username"))
            cur.execute("SELECT at, kind, detail FROM wa_log ORDER BY id DESC LIMIT 15")
            ev = cur.fetchall()
        lines += ["", "Last WhatsApp events (newest first) — send “hi”, then refresh this page:"]
        lines += [f"{e['at'].astimezone(IST):%d %b %I:%M:%S %p}  {e['kind'].upper()}  {e['detail']}" for e in ev] or \
                 ["(nothing received yet — if you sent “hi”, Meta did not deliver it here. Most often the app is not",
                  " subscribed to your WhatsApp account: sign in as owner → Settings → System → WhatsApp connection →",
                  " Connect. Also check the Callback URL is green and “messages” is Subscribed under Webhook fields.)"]
        return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}
    if (request.args.get("hub.mode") == "subscribe" and mine
            and _s.compare_digest(request.args.get("hub.verify_token", "").strip(), mine)):
        return request.args.get("hub.challenge", ""), 200, {"Content-Type": "text/plain"}
    return "verify token does not match WA_VERIFY_TOKEN", 403


def _act_as(cur, username, phone):
    cur.execute("SELECT id, username, full_name, role, active FROM users WHERE username=%s", (username,))
    u = cur.fetchone()
    if not u or not u["active"]:
        return None
    u = dict(u)
    u["username"] = f"{u['username']} (WhatsApp)"
    g.user = u
    return u


def _start_workers(jid, count):
    """Read the bill photos in parallel: 3 workers, each takes every 3rd photo (separate Vercel requests)."""
    base = request.host_url.rstrip("/")
    for k in range(min(3, count)):
        idxs = list(range(k, count, 3))
        try:
            requests.post(f"{base}/api/wa/work", json={"job": jid, "idx": idxs}, timeout=(3, 0.8),
                          headers={"x-cron-secret": os.environ.get("CRON_SECRET", "")})
        except requests.RequestException:
            pass            # the worker keeps running on its own; we don't wait for it


@bp.route("/api/wa/twilio", methods=["POST"])
def receive_twilio():
    """Twilio WhatsApp (Sandbox) webhook: form fields From, Body, NumMedia, MediaUrl0…, MessageSid."""
    from .. import wa
    params = request.form.to_dict()
    url = (os.environ.get("TWILIO_WEBHOOK_URL") or request.url).replace("http://", "https://", 1)
    if not wa.twilio_signature_ok(url, params, request.headers.get("X-Twilio-Signature")):
        return "bad signature", 403
    phone = params.get("From", "").replace("whatsapp:", "").lstrip("+")
    msgs = []
    n = int(params.get("NumMedia") or 0)
    for i in range(n):
        msgs.append({"from": phone, "id": f"{params.get('MessageSid')}-{i}", "type": "image",
                     "image": {"id": params.get(f"MediaUrl{i}"), "caption": params.get("Body", "") if i == 0 else ""}})
    if not n:
        msgs.append({"from": phone, "id": params.get("MessageSid"), "type": "text", "text": {"body": params.get("Body", "")}})
    _process(msgs)
    return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', 200, {"Content-Type": "text/xml"}


@bp.route("/api/wa/webhook", methods=["POST"])
def receive():
    from .. import wa
    raw = request.get_data()
    if not wa.signature_ok(raw, request.headers.get("X-Hub-Signature-256")):
        wa.log("refused", "the message's signature does not match WA_APP_SECRET — copy the App secret again "
                          "(Meta → App settings → Basic) into Vercel, then Redeploy")
        return "bad signature", 403
    body = json.loads(raw or b"{}")
    msgs = [m for e in body.get("entry", []) for ch in e.get("changes", []) for m in ((ch.get("value") or {}).get("messages") or [])]
    # delivery reports for the assistant's own replies: sent / delivered / read / failed (with Meta's reason)
    for e in body.get("entry", []):
        for ch in e.get("changes", []):
            for st in ((ch.get("value") or {}).get("statuses") or []):
                status = st.get("status", "")
                to = wa.mask(st.get("recipient_id", ""))
                if status == "failed":
                    errs = "; ".join(f"{x.get('code')}: {x.get('title', '')} — {(x.get('error_data') or {}).get('details', x.get('message', ''))}"
                                     for x in st.get("errors", []))
                    wa.log("delivery failed", f"to {to} — Meta says {errs}")
                elif status in ("delivered", "read"):
                    wa.log(status, f"to {to} ✓")
    try:
        _process(msgs)
    except Exception as e:
        import logging
        logging.getLogger("sfm").exception("WhatsApp message failed")
        wa.log("error", f"{type(e).__name__}: {e}")
    return "ok", 200


def _process(msgs):
    from .. import wa
    allowed = wa.owners()
    jobs = []
    for msg in msgs:
        phone = msg.get("from", "")
        if phone not in allowed:                   # strangers get no answer at all
            wa.log("ignored", f"message from {wa.mask(phone)} — this number is not in WA_OWNERS "
                              f"(WA_OWNERS has: {', '.join(wa.mask(p) for p in allowed) or 'nothing'})")
            continue
        with db.tx() as cur:
            cur.execute("INSERT INTO wa_seen (msg_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING msg_id", (msg.get("id"),))
            if not cur.fetchone():
                continue                           # the same message delivered twice
            user = _act_as(cur, allowed[phone], phone)
            if not user:
                wa.log("ignored", f"{wa.mask(phone)}: username “{allowed[phone]}” in WA_OWNERS is not an active login on the website")
                continue
            _body = (msg.get("text") or {}).get("body", msg.get("type")) or ""
            if __import__("re").fullmatch(r"\s*\d{4,8}\s*", _body):
                _body = "(number hidden)"                 # a secure PIN must never be logged
            wa.log("received", f"from {wa.mask(phone)} ({allowed[phone]}): {_body[:40]}")
            first = not wa.window_open(cur, phone)
            wa.typing(msg.get("id"))
            reply = wa.handle(cur, phone, user, msg)
            wa.save_state(cur, phone, wa.session(cur, phone)["state"] or {}, touch=True)
            if first or msg.get("type") == "text":
                wa.deliver_waiting(cur, phone)
            note = wa.sandbox_note(cur, phone, first)
        if isinstance(reply, dict) and reply.get("image"):
            wa.send_image(phone, reply["image"], reply.get("caption", ""))
        elif isinstance(reply, dict) and reply.get("texts"):
            for t_ in reply["texts"]:
                wa.send_text(phone, t_)
        elif isinstance(reply, dict) and reply.get("list"):
            body, button, sections = reply["list"]
            if not wa.send_list(phone, body + note, button, sections, force=reply.get("force", False)):
                wa.send_text(phone, reply.get("fallback", wa.MENU) + note)
        elif isinstance(reply, dict) and reply.get("docs"):
            if reply.get("reply") and (len(reply["docs"]) > 1 or reply.get("summary")):
                wa.send_text(phone, reply["reply"])
            for name, data, mime in reply["docs"]:
                wa.send_document(phone, name, data, mime, reply.get("caption", ""))
            if note:
                wa.send_text(phone, note.strip())
        elif isinstance(reply, dict):
            wa.send_text(phone, reply["reply"] + note)
            jobs.append(reply["start_job"])
        elif reply:
            wa.send_text(phone, reply + note)
    for jid in jobs:
        n = len(db.one("SELECT data FROM wa_jobs WHERE id=%s", (jid,))["data"]["photos"])
        if os.environ.get("WA_INLINE_WORK"):
            _work(jid, list(range(n)))
        else:
            _start_workers(jid, n)


def _work(jid, idxs):
    from .. import wa
    started = time.time()
    for i, idx in enumerate(idxs):
        if time.time() - started > 40 and not os.environ.get("WA_INLINE_WORK"):    # hand the rest to a fresh request
            try:
                requests.post(request.host_url.rstrip("/") + "/api/wa/work", json={"job": jid, "idx": idxs[i:]}, timeout=(3, 0.8),
                              headers={"x-cron-secret": os.environ.get("CRON_SECRET", "")})
            except requests.RequestException:
                pass
            return
        with db.tx() as cur:
            wa.bill_job_step(cur, jid, idx)


@bp.route("/api/wa/work", methods=["POST"])
def work():
    if not _internal_ok():
        return jsonify({"ok": False}), 401
    j = request.get_json(force=True, silent=True) or {}
    idxs = j.get("idx") if isinstance(j.get("idx"), list) else [j.get("idx")]
    _work(int(j["job"]), [int(i) for i in idxs])
    return jsonify({"ok": True})


@bp.route("/api/closing/work", methods=["POST"])
def closing_work():
    if not _internal_ok():
        return jsonify({"ok": False}), 401
    from ..closing_scan import work_part
    j = request.get_json(force=True, silent=True) or {}
    work_part(int(j["sid"]), j["part"] if j.get("part") in ("top", "bottom") else "top")
    return jsonify({"ok": True})


# ---------- Tally Cloud agent (GitHub Actions) — protected by CRON_SECRET
@bp.route("/api/tally/job/<int:jid>", methods=["GET"])
def tally_job(jid):
    if not _internal_ok():
        return jsonify({"ok": False}), 401
    from .. import db
    j = db.one("SELECT id, kind, params, state FROM tally_jobs WHERE id=%s", (jid,))
    return jsonify({"ok": bool(j), "job": j})


@bp.route("/api/tally/job/<int:jid>/retry-login", methods=["POST"])
def tally_retry_login(jid):
    if not _internal_ok():
        return jsonify({"ok": False}), 401
    from ..tally_jobs import retry_login_job
    job = retry_login_job(jid)
    return jsonify({"ok": bool(job), "job": job}), 200 if job else 409


@bp.route("/api/tally/job/<int:jid>/claim", methods=["POST"])
def tally_claim(jid):
    if not _internal_ok():
        return jsonify({"ok": False}), 401
    from ..tally_jobs import claim_job
    ok = claim_job(jid)
    return jsonify({"ok": ok}), 200 if ok else 409


@bp.route("/api/tally/job/<int:jid>/wait/<field>", methods=["GET"])
def tally_wait(jid, field):
    if not _internal_ok() or field not in ("pin", "company", "import"):
        return jsonify({"ok": False}), 401
    from ..tally_jobs import agent_wait
    return jsonify(agent_wait(jid, field))


@bp.route("/api/tally/job/<int:jid>/xml", methods=["GET"])
def tally_xml(jid):
    if not _internal_ok():
        return jsonify({"ok": False}), 401
    from ..tally_jobs import agent_xml
    res = agent_xml(jid)
    if not res["ok"]:
        return jsonify({"ok": False, "errors": res["errors"]}), 409
    return res["xml"], 200, {"Content-Type": "application/xml", "X-File-Name": res["name"]}


@bp.route("/api/tally/job/<int:jid>/step", methods=["POST"])
def tally_step(jid):
    if not _internal_ok():
        return jsonify({"ok": False}), 401
    import base64
    from ..tally_jobs import agent_step
    j = request.get_json(force=True, silent=True) or {}
    png = base64.b64decode(j["png"]) if j.get("png") else None
    return jsonify({"ok": agent_step(jid, j.get("step", "step"), j.get("message", ""), png, j.get("done"), j.get("failed"), j.get("evidence"))})


@bp.route("/api/tally/job/<int:jid>/file", methods=["POST"])
def tally_file(jid):
    if not _internal_ok():
        return jsonify({"ok": False}), 401
    from .. import db, wa
    j = db.one("SELECT phone FROM tally_jobs WHERE id=%s", (jid,))
    f = request.files.get("file")
    if not j or not f:
        return jsonify({"ok": False}), 400
    data = f.read()
    if not data.startswith(b"%PDF-"):
        return jsonify({"ok": False, "error": "Expected PDF"}), 400
    ok = wa.send_document(j["phone"], f.filename, data, "application/pdf", f"Tally job #{jid}", strict=True)
    return jsonify({"ok": bool(ok)}), 200 if ok else 502


@bp.route("/api/cron/tick", methods=["GET", "POST"])
def tick():
    """Called by Supabase every 15 minutes (see WHATSAPP_SETUP_GUIDE.md)."""
    if not _internal_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    from ..scheduler import tick as run
    return jsonify({"ok": True, "done": [d[0] for d in run()]})


@bp.route("/api/tally/job/<int:jid>/session", methods=["POST"])
def tally_session(jid):
    if not _internal_ok():
        return jsonify({"ok": False}), 401
    from ..tally_jobs import session_next
    body = request.get_json(silent=True) or {}
    return jsonify(session_next(jid, closing=bool(body.get('close'))))
