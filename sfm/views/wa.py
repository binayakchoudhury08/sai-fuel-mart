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
                 f"WA_OWNERS: {len(wa.owners())} number(s)" + ("" if wa.owners() else " — ❌ add e.g. 919876543210:boss"), "",
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
            wa.log("received", f"from {wa.mask(phone)} ({allowed[phone]}): {(msg.get('text') or {}).get('body', msg.get('type'))[:40]}")
            first = not wa.window_open(cur, phone)
            wa.typing(msg.get("id"))
            reply = wa.handle(cur, phone, user, msg)
            wa.save_state(cur, phone, wa.session(cur, phone)["state"] or {}, touch=True)
            if first or msg.get("type") == "text":
                wa.deliver_waiting(cur, phone)
            note = wa.sandbox_note(cur, phone, first)
        if isinstance(reply, dict) and reply.get("list"):
            body, button, sections = reply["list"]
            if not wa.send_list(phone, body + note, button, sections):
                wa.send_text(phone, reply.get("fallback", wa.MENU) + note)
        elif isinstance(reply, dict) and reply.get("docs"):
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


@bp.route("/api/cron/tick", methods=["GET", "POST"])
def tick():
    """Called by Supabase every 15 minutes (see WHATSAPP_SETUP_GUIDE.md)."""
    if not _internal_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    from ..scheduler import tick as run
    return jsonify({"ok": True, "done": [d[0] for d in run()]})
