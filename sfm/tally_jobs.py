"""Tally Cloud through the GitHub Actions agent (owner, WhatsApp).
tally import 1/11 to 3/11 → website starts the agent on GitHub → asks for the secure PIN → agent logs in, uploads the
XML to D:\\My_Applications\\SFM_TALLY_IMPORT\\<MON YYYY>, opens TallyPrime, asks which company, imports, sends a
screenshot after every step. tally ledger svt this month → ledger PDF.
The PIN is never stored: it waits in the database only until the agent reads it (then deleted; expires in 10 min)."""
import json
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
    cur.execute("""SELECT * FROM tally_jobs WHERE phone=%s AND state NOT IN ('done','failed') AND waiting IS NOT NULL
                   AND created_at > NOW() - interval '30 minutes' ORDER BY id DESC LIMIT 1""", (phone,))
    job = cur.fetchone()
    if job and job["waiting"] == "pin" and re.fullmatch(r"\d{4,8}", text.strip()):
        cur.execute("UPDATE tally_jobs SET answer=%s, answered_at=NOW(), waiting=NULL WHERE id=%s", (text.strip(), job["id"]))
        return "🔐 PIN received — logging in to Tally Cloud…"
    if job and job["waiting"] == "company" and not low.startswith(("tally", "menu", "hi")):
        cur.execute("UPDATE tally_jobs SET answer=%s, answered_at=NOW(), waiting=NULL WHERE id=%s", (text.strip()[:120], job["id"]))
        return f"🏢 Opening *{text.strip()}*…"
    again = bool(re.match(r"^tally\s+import\s+again\b", low))
    low = re.sub(r"^tally\s+import\s+again\b", "tally import", low)
    m = re.match(r"^tally\s+import\s+(.+?)(?:\s+(?:to|-)\s+(.+))?$", low)
    ml = re.match(r"^tally\s+ledger\s+(.+?)\s+(this month|last month|month|[a-z]+(?:\s+\d{4})?|\d{1,2}[./-]\d{1,2}.*)$", low)
    if not (m or ml):
        return None
    if m:
        w1, w2 = _when(m.group(1)), (_when(m.group(2)) if m.group(2) else None)
        a = w1[1] if w1 else None
        b = (w2[1] if w2 else a)
        if not a:
            return "Which date? e.g. *tally import 2/11*"
        from .tally_convert import convert
        from .tally_site import current
        data, _ = current(cur)
        res = convert(data, a, b) if data else {"ok": False, "errors": ["no workbook in the website"]}
        if not res["ok"]:
            return "❌ Not starting — the XML is blocked:\n" + "\n".join("• " + e for e in res["errors"][:8])
        cur.execute("""SELECT id, params FROM tally_jobs WHERE kind='import' AND state='done'
                       AND (params->>'from')::date <= %s AND (params->>'to')::date >= %s ORDER BY id DESC LIMIT 1""", (b, a))
        before = cur.fetchone()
        if before and not again:
            return (f"⚠️ {before['params']['from']} – {before['params']['to']} was already imported (job #{before['id']}). "
                    "Importing again makes duplicate vouchers in Tally.\nIf you really want it: *tally import again "
                    f"{a:%d/%m} to {b:%d/%m}*")
        params = {"from": a.isoformat(), "to": b.isoformat(), "name": res["name"], "vouchers": res["vouchers"]}
        kind, what = "import", f"import {res['vouchers']} vouchers ({a:%d/%m} – {b:%d/%m})"
    else:
        w = _when(ml.group(2))
        if not w:
            return "Write: *tally ledger svt this month*"
        first = w[1].replace(day=1)
        last = min(today(), (first.replace(month=first.month % 12 + 1, year=first.year + (first.month == 12)) - __import__("datetime").timedelta(days=1)))
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
    try:
        start_github(jid)
    except UserError as e:
        cur.execute("UPDATE tally_jobs SET state='failed', waiting=NULL WHERE id=%s", (jid,))
        return f"❌ {e}"
    return f"🤖 Tally job #{jid}: {what}.\nStarting the robot (about 1 minute)…\n🔐 Send your *secure PIN* now (it is never stored)."


# ------------------------------------------------------------------ the agent's private doors (CRON_SECRET)
def agent_wait(jid, field):
    """The agent asks for the owner's answer; the PIN is deleted as soon as it is handed over (10-minute limit)."""
    from . import db
    with db.tx() as cur:
        cur.execute("SELECT * FROM tally_jobs WHERE id=%s FOR UPDATE", (jid,))
        j = cur.fetchone()
        if not j:
            return {"state": "missing"}
        if j["answer"] is not None and j["waiting"] is None:
            ans = j["answer"]
            cur.execute("UPDATE tally_jobs SET answer=NULL WHERE id=%s", (jid,))
            return {"state": "ok", "answer": ans}
        if field == "company" and j["waiting"] != "company":
            cur.execute("UPDATE tally_jobs SET waiting='company' WHERE id=%s", (jid,))
            _wa().notify(cur, j["phone"], f"🏢 Tally job #{jid}: which company should I open? (e.g. *SAI FUEL MART 26-27*)")
        if j["created_at"] and (__import__("datetime").datetime.now(j["created_at"].tzinfo) - j["created_at"]).total_seconds() > 600 and field == "pin":
            cur.execute("UPDATE tally_jobs SET state='failed', waiting=NULL WHERE id=%s", (jid,))
            return {"state": "expired"}
        return {"state": "waiting"}


def agent_step(jid, step, message, png=None, done=False, failed=False):
    from . import db
    with db.tx() as cur:
        cur.execute("SELECT phone FROM tally_jobs WHERE id=%s", (jid,))
        j = cur.fetchone()
        if not j:
            return False
        state = "failed" if failed else ("done" if done else step)
        cur.execute("UPDATE tally_jobs SET state=%s, log = log || %s::jsonb WHERE id=%s",
                    (state, json.dumps([{"step": step, "message": message[:300]}]), jid))
        if failed:
            cur.execute("UPDATE tally_jobs SET waiting=NULL, answer=NULL WHERE id=%s", (jid,))
        phone = j["phone"]
    icon = "❌" if failed else ("✅" if done else "▶️")
    if png:
        _wa().send_image(phone, png, f"{icon} #{jid} {message}"[:900])
    else:
        _wa().send_text(phone, f"{icon} Tally job #{jid}: {message}")
    return True


def agent_xml(jid):
    from . import db
    from .tally_convert import convert
    from .tally_site import current
    with db.tx() as cur:
        cur.execute("SELECT * FROM tally_jobs WHERE id=%s", (jid,))
        j = cur.fetchone()
        data, _ = current(cur)
    p = j["params"]
    res = convert(data, date.fromisoformat(p["from"]), date.fromisoformat(p["to"]))
    return res
