"""Tally Cloud through the GitHub Actions agent (owner, WhatsApp).
tally import 1/11 to 3/11 → website starts the agent on GitHub → asks for the secure PIN → agent logs in, uploads the
XML to D:\\My_Applications\\SFM_TALLY_IMPORT\\<MON YYYY>, opens TallyPrime, asks which company, imports, sends a
screenshot after every step. tally ledger svt this month → ledger PDF.
The PIN is never stored: it waits in the database only until the agent reads it (then deleted; expires in 10 min)."""
import json
import hashlib
import calendar
import os
import re
from datetime import date

import requests

from .util import UserError, today


def _wa():
    from . import wa
    return wa


def start_github(job_id):
    """workflow_dispatch on the owner's repository (GITHUB_TOKEN with Actions: write, GITHUB_REPO = owner/name)."""
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if not tok or not repo:
        raise UserError("GitHub is not connected yet: add GITHUB_TOKEN and GITHUB_REPO in Vercel (see TALLY_AGENT_GUIDE.md).")
    r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/tally-agent.yml/dispatches", timeout=15,
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                      json={"ref": os.environ.get("GITHUB_REF", "main"), "inputs": {"job_id": str(job_id)}})
    if r.status_code >= 300:
        raise UserError(f"GitHub did not start the job ({r.status_code}): {r.text[:160]}")


def handle(cur, phone, low, text):
    """WhatsApp: start a job, or answer the job that is waiting (PIN / company)."""
    from .wa import _when
    if low.strip() == "tally status":
        cur.execute("SELECT id, state, waiting, params, log FROM tally_jobs WHERE phone=%s ORDER BY id DESC LIMIT 1", (phone,))
        latest = cur.fetchone()
        if not latest:
            return "No Tally job found."
        lines = [f"Tally job #{latest['id']}: {latest['state']}"]
        if latest['params'].get('name'):
            lines.append(latest['params']['name'])
        if latest.get('log'):
            lines.append(latest['log'][-1].get('message', ''))
        if latest.get('waiting'):
            lines.append("Waiting for: " + latest['waiting'])
        if latest['state'] in ('submitting', 'review_required'):
            lines.append("Check Day Book before retrying; the import may have been partial.")
        return "\n".join(lines)
    if low in ("stop", "stop tally", "tally stop", "cancel"):
        cur.execute("""SELECT * FROM tally_jobs WHERE phone=%s AND
                       (state NOT IN ('done','failed','review_required') OR params ? 'session_seen')
                       ORDER BY (state NOT IN ('done','failed','review_required')) DESC, id DESC LIMIT 1 FOR UPDATE""", (phone,))
        active = cur.fetchone()
        if not active:
            return None if low == "cancel" else "No active Tally job to stop."
        jid = active["id"]
        uncertain = active["state"] == "submitting"
        state = "review_required" if uncertain else active["state"] if active["state"] in ("done","failed","review_required") else "failed"
        cur.execute("""UPDATE tally_jobs SET state=%s, waiting=NULL, answer=NULL, answered_at=NULL,
                       params=(params - 'session_seen') || '{"stopped":true}'::jsonb,
                       log=log || '[{"step":"stop","message":"Stopped by owner"}]'::jsonb WHERE id=%s""", (state,jid))
        return f"Stopped Tally job #{jid}." + (" Submission had started. Check Day Book before retrying." if uncertain else "")
    cur.execute("""SELECT * FROM tally_jobs WHERE phone=%s AND state NOT IN ('done','failed','review_required') AND waiting IS NOT NULL
                   AND created_at > NOW() - interval '30 minutes' ORDER BY id DESC LIMIT 1""", (phone,))
    job = cur.fetchone()
    if job and job["waiting"] == "import":
        if text.strip().upper() != "IMPORT":
            return "Review the import screenshot, then reply *IMPORT* or *STOP*."
        cur.execute("UPDATE tally_jobs SET answer='IMPORT', answered_at=NOW(), waiting=NULL WHERE id=%s", (job["id"],))
        return "Approval received — checking the form again before submission."
    if job and job["waiting"] == "pin" and re.fullmatch(r"\d{4,8}", text.strip()):
        cur.execute("UPDATE tally_jobs SET answer=%s, answered_at=NOW(), waiting=NULL WHERE id=%s", (text.strip(), job["id"]))
        return "🔐 PIN received — logging in to Tally Cloud…"
    if job and job["waiting"] == "company" and not low.startswith(("tally", "menu", "hi")):
        cur.execute("UPDATE tally_jobs SET answer=%s, answered_at=NOW(), waiting=NULL WHERE id=%s", (text.strip()[:120], job["id"]))
        return f"🏢 Opening *{text.strip()}*…"
    low = re.sub(r"\s+pdf$", "", low.strip())
    mc = re.fullmatch(r"tally\s+(?:check|day\s*book)\s+(.+)", low)
    again = bool(re.match(r"^tally\s+import\s+again\b", low))
    low = re.sub(r"^tally\s+import\s+again\b", "tally import", low)
    m = re.match(r"^tally\s+import\s+(.+?)(?:\s+(?:to|-)\s+(.+))?$", low)
    ml = re.match(r"^tally\s+ledger\s+(.+?)\s+((?:till|until)\s+.+|this month|last month|month|[a-z]+(?:\s+\d{4})?|\d{1,2}[./-]\d{1,2}.*)$", low)
    if not (m or ml or mc):
        return None
    # Serialize job creation across owners: one shared Tally desktop, no queued PIN expiry.
    cur.execute("SELECT pg_advisory_xact_lock(734109)")
    cur.execute("SELECT id FROM tally_jobs WHERE state NOT IN ('done','failed','review_required') AND created_at > NOW() - interval '45 minutes' ORDER BY id DESC LIMIT 1")
    active = cur.fetchone()
    if active:
        return f"Tally job #{active['id']} is still active. Finish it before starting another."
    if mc:
        w = _when(mc.group(1))
        if not w or w[0] != "day":
            return "Write a specific date: *tally check 2/11/2026*"
        # Report jobs use the existing read-only ledger job category; no schema change.
        params = {"report": "daybook", "from": w[1].isoformat(), "to": w[1].isoformat()}
        kind, what = "ledger", f"check Day Book for {w[1]:%d/%m/%Y} (read only)"
    elif m:
        w1, w2 = _when(m.group(1)), (_when(m.group(2)) if m.group(2) else None)
        a = w1[1] if w1 else None
        b = (w2[1] if w2 else a)
        if not a:
            return "Which date? e.g. *tally import 2/11*"
        if b < a:
            return "End date must be on or after the start date."
        from .tally_convert import convert
        from .tally_site import current
        data, _ = current(cur)
        res = convert(data, a, b) if data else {"ok": False, "errors": ["no workbook in the website"]}
        if not res["ok"]:
            return "❌ Not starting — the XML is blocked:\n" + "\n".join("• " + e for e in res["errors"][:8])
        cur.execute("""SELECT id, params FROM tally_jobs WHERE kind='import' AND (state IN ('done','submitting','review_required') OR log @> '[{"step":"submitting"}]'::jsonb)
                       AND (params->>'from')::date <= %s AND (params->>'to')::date >= %s ORDER BY id DESC LIMIT 1""", (b, a))
        before = cur.fetchone()
        if before and not again:
            return (f"⚠️ {before['params']['from']} – {before['params']['to']} was imported or may have been partially imported (job #{before['id']}). "
                    "Check Day Book first. Importing again can duplicate vouchers in Tally.\nIf you really want it: *tally import again "
                    f"{a:%d/%m} to {b:%d/%m}*")
        params = {"from": a.isoformat(), "to": b.isoformat(), "name": res["name"], "vouchers": res["vouchers"],
                  "xml": res["xml"], "sha256": hashlib.sha256(res["xml"].encode("utf8")).hexdigest()}
        kind, what = "import", f"import {res['vouchers']} vouchers ({a:%d/%m} – {b:%d/%m})"
    else:
        w = _when(ml.group(2))
        if not w:
            return "Write: *tally ledger svt this month*"
        cutoff = ml.group(2).startswith(("till ", "until "))
        if cutoff and w[0] != "day":
            return "Give an end date, e.g. *OSL ledger till 30th Oct 2026*."
        first = w[1].replace(day=1)
        last = w[1] if cutoff else first.replace(day=calendar.monthrange(first.year, first.month)[1])
        from .tally_site import current, masters, name_map, norm
        said = ml.group(1).strip()
        ledger = name_map(cur).get("party", {}).get(said)
        data, _ = current(cur)
        if not ledger and data:
            from .tally_book import Adder
            led = masters(Adder(data))[0]
            hits = [x for x in led if norm(x).startswith(norm(said))] or [x for x in led if norm(said) in norm(x)]
            if len(hits) > 1:
                return "Which ledger? " + ", ".join(hits[:10])
            ledger = hits[0] if hits else None
        if not ledger:
            return f"No Tally ledger matches “{said}”. Write the name as in Tally."
        params = {"ledger": ledger, "from": first.isoformat(), "to": last.isoformat()}
        kind, what = "ledger", f"ledger {ledger} {first:%d/%m} – {last:%d/%m} as PDF"
    cur.execute("INSERT INTO tally_jobs (phone, kind, params, waiting) VALUES (%s,%s,%s,'pin') RETURNING id", (phone, kind, json.dumps(params)))
    jid = cur.fetchone()["id"]
    if kind == "import":
        # A unique filename avoids accepting an old file-manager row as a new upload.
        params["name"] = params["name"].removesuffix(".xml") + f"_job{jid}.xml"
        cur.execute("UPDATE tally_jobs SET params=%s::jsonb WHERE id=%s", (json.dumps(params), jid))
    if kind == "ledger":
        cur.execute("SELECT id FROM tally_jobs WHERE phone=%s AND params->>'session_seen' IS NOT NULL AND (params->>'session_seen')::timestamptz > NOW() - interval '15 seconds' ORDER BY id DESC LIMIT 1 FOR UPDATE", (phone,))
        warm = cur.fetchone()
        if warm:
            cur.execute("UPDATE tally_jobs SET waiting=NULL, params=params || %s::jsonb WHERE id=%s", (json.dumps({"session_parent": warm["id"]}), jid))
            start_github(jid)  # queued fallback; exits without replay if warm worker claims it
            return f"Tally job #{jid}: {what}. Using your open Tally session — no new PIN needed."
    try:
        start_github(jid)
    except UserError as e:
        cur.execute("UPDATE tally_jobs SET state='failed', waiting=NULL WHERE id=%s", (jid,))
        return f"❌ {e}"
    return f"🤖 Tally job #{jid}: {what}.\nStarting the robot (about 1 minute)…\n🔐 Send your *secure PIN* now (it is never stored)."


# ------------------------------------------------------------------ the agent's private doors (CRON_SECRET)
def claim_job(jid):
    """Only one workflow execution may operate a job, including GitHub reruns."""
    from . import db
    with db.tx() as cur:
        cur.execute("UPDATE tally_jobs SET state='running', waiting=CASE WHEN answer IS NOT NULL AND waiting IS NULL THEN NULL ELSE 'pin' END WHERE id=%s AND state='starting' RETURNING id, phone, params, waiting", (jid,))
        claimed = cur.fetchone()
    if claimed and claimed['params'].get('session_parent') and claimed['waiting'] == 'pin':
        _wa().send_text(claimed['phone'], "The previous Tally session ended. Send your secure PIN to reconnect for this queued report.")
    return bool(claimed)


def retry_login_job(jid):
    """Explicit operator dispatch only: clone a failed pre-login job, never a submitted import."""
    from . import db
    with db.tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(734109)")
        cur.execute("SELECT * FROM tally_jobs WHERE id=%s FOR UPDATE", (jid,))
        old = cur.fetchone()
        if not old or old["state"] != "failed":
            return None
        # Legacy PIN expiry had no log. Every successful login writes a log before Connect.
        if any(e.get("step") != "answer_expired" or e.get("field") != "pin" for e in (old.get("log") or [])):
            return None
        cur.execute("SELECT id FROM tally_jobs WHERE (state NOT IN ('done','failed','review_required') AND created_at > NOW() - interval '45 minutes') OR params->>'retry_source'=%s LIMIT 1", (str(jid),))
        if cur.fetchone():
            return None
        params = {k: v for k, v in old["params"].items() if k not in ("session_parent", "session_seen")}
        params["retry_source"] = jid
        cur.execute("INSERT INTO tally_jobs (phone,kind,params,waiting) VALUES (%s,%s,%s,'pin') RETURNING id", (old["phone"], old["kind"], json.dumps(params)))
        new_id = cur.fetchone()["id"]
        if old["kind"] == "import":
            params["name"] = re.sub(r"(?:_job\d+)?\.xml$", f"_job{new_id}.xml", params["name"])
            cur.execute("UPDATE tally_jobs SET params=%s::jsonb WHERE id=%s", (json.dumps(params), new_id))
    _wa().send_text(old["phone"], f"Retrying pre-login job #{jid} as #{new_id}. Send a fresh secure PIN now. The previous PIN is not reused; import still requires your IMPORT reply after the form screenshot.")
    return {"id": new_id, "kind": old["kind"], "params": params, "state": "starting"}


def agent_wait(jid, field):
    """Consume answers only at their matching stage; never return an old PIN as a company."""
    from . import db
    with db.tx() as cur:
        cur.execute("SELECT * FROM tally_jobs WHERE id=%s FOR UPDATE", (jid,))
        j = cur.fetchone()
        if not j:
            return {"state": "missing"}
        if j["state"] in ("done", "failed", "review_required", "submitting"):
            return {"state": "cancelled"}
        expected = {"pin": "running", "company": "tally", "import": "awaiting_import"}
        if field not in expected or j["state"] != expected[field]:
            return {"state": "cancelled"}
        now = __import__("datetime").datetime.now(j["created_at"].tzinfo)
        stamp = j.get("answered_at") or j["created_at"]
        if (now - stamp).total_seconds() > 600 and (field == "pin" or j["answer"] is not None):
            message = f"Tally job #{jid}: {field} expired after 10 minutes. The robot stopped. Send tally status before retrying."
            cur.execute("UPDATE tally_jobs SET state='failed', waiting=NULL, answer=NULL, answered_at=NULL, log=log || %s::jsonb WHERE id=%s",
                        (json.dumps([{"step": "answer_expired", "message": message, "field": field}]), jid))
            _wa().notify(cur, j["phone"], message)
            return {"state": "expired"}
        if j["answer"] is not None and j["waiting"] is None:
            ans = j["answer"]
            cur.execute("UPDATE tally_jobs SET answer=NULL, answered_at=NULL WHERE id=%s", (jid,))
            return {"state": "ok", "answer": ans}
        if field == "company" and j["waiting"] != "company":
            cur.execute("UPDATE tally_jobs SET waiting='company', answer=NULL WHERE id=%s", (jid,))
            _wa().notify(cur, j["phone"], f"🏢 Tally job #{jid}: which company should I open? (e.g. *SAI FUEL MART 26-27*)")
        return {"state": "waiting"}


def valid_import_evidence(params, evidence):
    if not isinstance(evidence, dict) or evidence.get("filename") != params.get("name"):
        return False
    counts = evidence.get("counts", {})
    if not isinstance(counts, dict) or any(type(counts.get(k)) is not int or counts[k] < 0
                                        for k in ("created", "altered", "errors", "ignored")):
        return False
    return (counts["errors"] == counts["ignored"] == 0
            and counts["created"] + counts["altered"] == params.get("vouchers")
            and params.get("vouchers", 0) > 0)


def agent_step(jid, step, message, png=None, done=False, failed=False, evidence=None):
    from . import db
    with db.tx() as cur:
        cur.execute("SELECT * FROM tally_jobs WHERE id=%s FOR UPDATE", (jid,))
        j = cur.fetchone()
        if not j or j["state"] in ("done", "failed", "review_required"):
            return False
        if done and j["kind"] == "import":
            if (step != "import_verified" or j["state"] != "submitting" or not png
                    or not valid_import_evidence(j["params"], evidence)):
                return False
        if step == "awaiting_import" and (not png or j["state"] not in ("company", "awaiting_import")):
            return False
        if step == "submitting" and (j["state"] != "awaiting_import" or j["waiting"] is not None or j["answer"] is not None):
            return False
        uncertain = j["state"] == "submitting" and failed
        state = "review_required" if uncertain else ("failed" if failed else ("done" if done else step))
        if step == "daybook_evidence" and j["state"] == "submitting":
            state = "submitting"
        if uncertain:
            message = "Import outcome UNCONFIRMED. Check Day Book before retrying. " + message
        # Mark waiting before delivering the screenshot so a quick reply is not lost.
        if step == "awaiting_import":
            cur.execute("UPDATE tally_jobs SET waiting='import', answer=NULL, answered_at=NULL WHERE id=%s", (jid,))
        cur.execute("UPDATE tally_jobs SET state=%s, log = log || %s::jsonb WHERE id=%s",
                    (state, json.dumps([{"step": step, "message": message[:900], "evidence": evidence}]), jid))
        if failed or done or state == "review_required":
            cur.execute("UPDATE tally_jobs SET waiting=NULL, answer=NULL WHERE id=%s", (jid,))
        phone = j["phone"]
    icon = "⚠️" if state == "review_required" else ("❌" if failed else ("✅" if done else "▶️"))
    if png:
        delivered = _wa().send_image(phone, png, f"{icon} #{jid} {message}"[:900], strict=True)
    else:
        delivered = _wa().send_text(phone, f"{icon} Tally job #{jid}: {message}")
    if not delivered:
        _wa().send_text(phone, f"⚠️ Job #{jid}: screenshot/status delivery failed. Send tally status for the saved status. "
                        + ("Result recorded; check Tally before retrying." if done or state == "submitting" else "Agent stopped; no further import actions will be taken."))
    return bool(delivered)


def agent_xml(jid):
    """Use the validated snapshot, not a different workbook edited during login."""
    from . import db
    with db.tx() as cur:
        cur.execute("SELECT * FROM tally_jobs WHERE id=%s", (jid,))
        j = cur.fetchone()
    if not j or j["kind"] != "import" or j["state"] in ("done", "failed", "review_required", "submitting"):
        return {"ok": False, "errors": ["Import job is missing or no longer active"]}
    p = j["params"]
    xml = p.get("xml")
    if not xml or hashlib.sha256(xml.encode("utf8")).hexdigest() != p.get("sha256"):
        return {"ok": False, "errors": ["Validated XML snapshot unavailable. Start a new job after checking Day Book."]}
    return {"ok": True, "xml": xml, "name": p["name"], "vouchers": p["vouchers"]}


def session_next(jid, closing=False):
    """Short-lived lease for the existing browser; never persist login credentials."""
    from . import db
    with db.tx() as cur:
        cur.execute("SELECT * FROM tally_jobs WHERE id=%s FOR UPDATE", (jid,))
        parent = cur.fetchone()
        if not parent or parent['params'].get('stopped') or parent['state'] not in ('done', 'failed', 'review_required'):
            return {"ok": False}
        if closing:
            cur.execute("UPDATE tally_jobs SET params=params - 'session_seen' WHERE id=%s", (jid,))
            # Recover a command queued just as the browser closed: require a fresh login.
            cur.execute("UPDATE tally_jobs SET waiting='pin' WHERE state='starting' AND params->>'session_parent'=%s RETURNING id", (str(jid),))
            queued = cur.fetchall()
        else:
            cur.execute("UPDATE tally_jobs SET params=params || jsonb_build_object('session_seen', NOW()::text) WHERE id=%s", (jid,))
            cur.execute("SELECT * FROM tally_jobs WHERE phone=%s AND state='starting' AND params->>'session_parent'=%s ORDER BY id LIMIT 1 FOR UPDATE", (parent['phone'], str(jid)))
            job = cur.fetchone()
            if job:
                cur.execute("UPDATE tally_jobs SET state='running', waiting=NULL WHERE id=%s", (job['id'],))
                cur.execute("UPDATE tally_jobs SET params=params - 'session_seen' WHERE id=%s", (jid,))
            return {"ok": True, "job": job}
    return {"ok": True}
