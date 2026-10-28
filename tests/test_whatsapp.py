"""WhatsApp assistant + scheduler against a simulated Meta Graph API and Gemini."""
import os, sys, json, hmac, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSN = os.environ["DATABASE_URL"]
if "supabase" in DSN:
    sys.exit("Refusing to run tests against a real database.")
OWNER, MGR, STRANGER = "919999900001", "919999900002", "917777700000"
os.environ.update(SKIP_SCHEMA="1", WA_TOKEN="tok", WA_PHONE_ID="555", WA_VERIFY_TOKEN="verifyme", WA_APP_SECRET="appsecret",
                  WA_OWNERS=f"{OWNER}:boss,{MGR}:mgr2", CRON_SECRET="cronsecret", WA_INLINE_WORK="1", GEMINI_API_KEY="g")
from datetime import datetime, timedelta, date
from decimal import Decimal
import psycopg2
import sfm.wa as wa, sfm.scan as scan, sfm.scheduler as sched
FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
SENT, UPLOADS = [], []
class R:
    def __init__(s, code, body): s.status_code, s._b, s.text, s.content, s.headers = code, body, json.dumps(body), b"\xff\xd8\xffJPEG", {"Content-Type": "image/jpeg"}
    def json(s): return s._b
BILL = iter([{"entry_dates": [f"{d}.10.26"], "top_date": f"{d+1}.10.26", "party": "S.V.P", "total_amount": str(t), "amounts": [str(t)]}
             for d, t in ((12, 54251), (13, 75261), (14, 47827))])
import json as _json
def fake_post(url, json=None, headers=None, timeout=None, data=None, auth=None, **k):
    if "api.twilio.com" in url and url.endswith("/Messages.json"):          # Twilio send
        assert auth == ("AC123", "twtoken") and data["From"] == "whatsapp:+14155238886"
        SENT.append((data["To"].replace("whatsapp:+", ""), data["Body"])); return R(201, {"sid": "SM1"})
    if "graph" in url and url.endswith("/media"):                          # file upload
        UPLOADS.append(k["files"]["file"][0]); return R(200, {"id": f"MEDIA{len(UPLOADS)}"})
    if "graph" in url and url.endswith("/messages"):                       # WhatsApp send
        if json.get("type") == "document":
            SENT.append((json["to"], "DOC:" + json["document"]["filename"])); return R(200, {"messages": [{"id": "wamid.d"}]})
        SENT.append((json["to"], json["text"]["body"])); return R(200, {"messages": [{"id": "wamid.x"}]})
    if "generativelanguage" in url:                                       # Gemini reads a bill
        return R(200, {"candidates": [{"content": {"parts": [{"text": _json.dumps(next(BILL))}]}}]})
    raise AssertionError(url)
def fake_get(url, headers=None, timeout=None, auth=None, **k):
    if "api.twilio.com" in url and "/Media/" in url: return R(200, {})
    if url.endswith(("/m1", "/m2", "/m3")): return R(200, {"url": "https://lookaside.example/" + url[-2:], "mime_type": "image/jpeg"})
    if "lookaside" in url: return R(200, {})
    if "generativelanguage" in url:
        return R(200, {"models": [{"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]}]})
    return R(200, {})
wa.requests.post, wa.requests.get = fake_post, fake_get
from sfm import create_app
app = create_app(); cl = app.test_client()
k = psycopg2.connect(DSN); k.autocommit = True; c = k.cursor()
n = {"i": 0}
def send(phone, text=None, image=None, sign=True):
    n["i"] += 1
    msg = {"from": phone, "id": f"wamid.in{n['i']}", "type": "image" if image else "text"}
    if image: msg["image"] = {"id": image, "mime_type": "image/jpeg"}
    else: msg["text"] = {"body": text}
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest() if sign else "sha256=bad"
    before = len(SENT)
    r = cl.post("/api/wa/webhook", data=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    return r, [t for _, t in SENT[before:]]
print("Webhook security")
r = cl.get("/api/wa/webhook?hub.mode=subscribe&hub.verify_token=verifyme&hub.challenge=12345")
check(r.status_code == 200 and r.data == b"12345", "Meta's webhook check answered")
check(cl.get("/api/wa/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1").status_code == 403, "wrong verify token refused")
r, out = send(STRANGER, "hi"); check(r.status_code == 200 and not out, "a stranger's number gets no answer")
r, out = send(OWNER, "hi", sign=False); check(r.status_code == 403 and not out, "message without Meta's signature refused")
print("Menu and answers")
r, out = send(OWNER, "hi"); check(out and "Sai Fuel Mart assistant" in out[0], "hi → menu")
for cmd, want in (("1", "Tank stock"), ("pending", "Pending credit"), ("pending svt", "SVT Logistics"), ("closing", "Daily closing"),
                  ("rates", "Today’s rates"), ("lube", "Lube stock"), ("report", "Report —"), ("alerts", ""), ("profit", "Profit"),
                  ("variation", "Tank variation"), ("score", "credit score"), ("compare", "This month vs"), ("blah", "didn't understand")):
    r, out = send(OWNER, cmd)
    check(out and want in out[-1], f"“{cmd}” → {out[-1].splitlines()[0][:60] if out else 'nothing'}")
c.execute("SELECT msg_id FROM pump.wa_seen ORDER BY at DESC LIMIT 1"); last = c.fetchone()[0]
body = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": OWNER, "id": last, "type": "text", "text": {"body": "1"}}]}}]}]}).encode()
b0 = len(SENT); cl.post("/api/wa/webhook", data=body, headers={"X-Hub-Signature-256": "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()})
check(len(SENT) == b0, "the same message delivered twice by Meta is answered once")
print("Actions (need YES)")
tm = date.today() + timedelta(days=1)
r, out = send(OWNER, "rate hsd 95.50 ms 102.25 tomorrow"); check(out and "Reply *YES*" in out[0] and "95.50" in out[0], "rate change asks for YES")
r, out = send(OWNER, "yes")
c.execute("SELECT fuel_code, rate FROM pump.fuel_rates WHERE effective_from=%s ORDER BY 1", (tm,))
check(c.fetchall() == [("HSD", Decimal("95.50")), ("MS", Decimal("102.25"))] and "Rates saved" in out[0], f"rates saved for tomorrow (6 AM price): {out[0][:50]}")
c.execute("SELECT username FROM pump.audit_log WHERE entity='fuel_rate' ORDER BY id DESC LIMIT 1")
check(c.fetchone()[0] == "boss (WhatsApp)", "audit log shows “boss (WhatsApp)”")
ld = date.today() - timedelta(days=9)
c.execute("INSERT INTO pump.days (business_date, status) VALUES (%s,'locked') ON CONFLICT (business_date) DO UPDATE SET status='locked'", (ld,))
r, out = send(OWNER, f"reopen {ld:%d/%m}"); check(out and "for *" in out[0] and "YES" in out[0], f"reopen asks for YES: {out[0][:70] if out else ''}")
r, out = send(OWNER, "YES")
c.execute("SELECT status FROM pump.days WHERE business_date=%s", (ld,)); st = c.fetchone()[0]
c.execute("SELECT u.username FROM pump.access_grants a JOIN pump.users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 1")
check(st == "open" and c.fetchone()[0] == "mgr2" and "Opened for" in out[0], f"day {ld:%d/%m} opened for the manager for 24 h")
r, out = send(OWNER, "rate hsd 1 tomorrow"); r, out = send(OWNER, "no")
check("Not done" in out[0] or "Cancelled" in out[0], "anything other than YES cancels")
r, out = send(OWNER, "lube add servo 6 @ 250"); r2, out2 = send(OWNER, "yes")
c.execute("SELECT qty, rate FROM pump.lube_moves WHERE kind='purchase' AND remarks='via WhatsApp' ORDER BY id DESC LIMIT 1")
check(c.fetchone() == (Decimal("6.00"), Decimal("250.00")) and "saved" in out2[0], "lube arrived saved with purchase rate")
print("Bill message from photos")
r, out = send(OWNER, "bill")
check(out and "Send the bill photos" in out[0], "bill → asks for photos")
for m in ("m1", "m2", "m3"): r, out = send(OWNER, image=m)
r, out = send(OWNER, "done")
msg = next((t for t in out if t.startswith("SVT Logistics")), "")
check("Reading 3 bills" in out[0], "done → “Reading 3 bills…”")
check(msg == "SVT Logistics\nBill upto 14/10/2026\n\nBill 121026 = 54,251\nBill 131026 = 75,261\nBill 141026 = 47,827\n--------------------\nTOTAL = 1,77,339",
      "message in the owner's format (entry dates, Indian commas)" + ("" if msg else f" — got {out}"))
check(any("All totals checked" in t for t in out), "totals checked note")
c.execute("SELECT COUNT(*) FROM pump.msg_batches WHERE created_by LIKE '%%WhatsApp%%'"); check(c.fetchone()[0] == 1, "also saved in the website's Bill Message history")
print("Scheduler (own clock) + 24-hour window")
check(cl.post("/api/cron/tick").status_code == 401, "tick without the secret refused")
c.execute("UPDATE pump.wa_sessions SET last_in = NOW() - interval '2 days' WHERE phone=%s", (OWNER,))
c.execute("DELETE FROM pump.job_runs"); c.execute("UPDATE pump.days SET status='open' WHERE business_date=CURRENT_DATE")
real = sched.now_ist
sched.now_ist = lambda: real().replace(hour=23, minute=30)
b0 = len(SENT)
r = cl.post("/api/cron/tick", headers={"x-cron-secret": "cronsecret"})
done = r.json["done"]
check(all(j in done for j in ("daily_job", "morning_report", "attendance_close", "closing_reminder", "closing_escalate")) and done.count("alerts") <= 1,
      f"jobs due at 23:30 all ran, alerts bundled in one message: {done}")
c.execute("SELECT COUNT(*) FROM pump.wa_outbox WHERE phone=%s AND sent_at IS NULL", (OWNER,)); waiting = c.fetchone()[0]
check(waiting >= 5 and not [x for x in SENT[b0:] if x[0] == OWNER], f"owner's 24-hour window closed → {waiting} messages wait (nothing paid sent)")
c.execute("SELECT COUNT(*) FROM pump.wa_outbox WHERE phone=%s AND text LIKE '%%not submitted yet%%'", (MGR,))
check(c.fetchone()[0] == 1 or any(x[0] == MGR and "not submitted" in x[1] for x in SENT[b0:]), "manager gets the 9 PM closing reminder")
r = cl.post("/api/cron/tick", headers={"x-cron-secret": "cronsecret"})
check("morning_report" not in r.json["done"], "each job runs only once a day")
sched.now_ist = real
r, out = send(OWNER, "hi")
check(any("While you were away" in t and "Good morning" in t and "Attendance closed" in t for t in out), "owner writes → waiting messages delivered at once")
c.execute("SELECT COUNT(*) FROM pump.wa_outbox WHERE phone=%s AND sent_at IS NULL", (OWNER,)); check(c.fetchone()[0] == 0, "outbox emptied")
print("Webhook status page + bill files on WhatsApp")
r = cl.get("/api/wa/webhook"); t = r.get_data(as_text=True)
check(r.status_code == 200 and "webhook is live" in t and "WA_VERIFY_TOKEN: ✅ set" in t and "2 number(s)" in t, "browser check page: live + which settings are set")
os.environ["WA_VERIFY_TOKEN"] = "verifyme "
r = cl.get("/api/wa/webhook?hub.mode=subscribe&hub.verify_token=verifyme&hub.challenge=777")
check(r.status_code == 200 and r.data == b"777", "verify still works when the token has an extra space from copy-paste")
os.environ["WA_VERIFY_TOKEN"] = "verifyme"
c.execute("""SELECT t.business_date FROM pump.party_txns t JOIN pump.parties p ON p.id=t.party_id
             WHERE p.name='SVT Logistics' AND NOT t.voided AND t.kind IN ('fuel','cash_advance') GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1""")
sd = c.fetchone()[0]
r, out = send(OWNER, f"give me svt pdf {sd:%d/%m/%Y}")
docs = [t for t in out if t.startswith("DOC:")]
check(docs == [f"DOC:SVT Logistics Bill {sd:%d%m%y}.pdf"], f"“svt pdf {sd:%d/%m/%Y}” → only SVT's PDF for that date: {docs}")
r, out = send(OWNER, f"svt excel {sd.day} {sd:%B} {sd.year}")
check([t for t in out if t.startswith("DOC:")] == [f"DOC:SVT Logistics Bill {sd:%d%m%y}.xlsx"], "“svt excel 26 October 2026” style → only the Excel")
word = {0: "today", 1: "yesterday", 2: "day before yesterday"}.get((date.today() - sd).days)
if word:
    r, out = send(OWNER, f"svt pdf and bill {word}")
    check(sorted(t for t in out if t.startswith("DOC:")) == [f"DOC:SVT Logistics Bill {sd:%d%m%y}.pdf", f"DOC:SVT Logistics Bill {sd:%d%m%y}.xlsx"],
          f"“svt pdf and bill {word}” → PDF + Excel")
r, out = send(OWNER, f"svt pdf {sd:%B}")
check(len([t for t in out if t.startswith("DOC:")]) == 1, f"“svt pdf {sd:%B}” → the month bill")
r, out = send(OWNER, "pdf 26/10")
check(out and "Which transporter?" in out[0], "no transporter named → asks which one")
r, out = send(OWNER, "bill svt")
check(out and "Send the bill photos" in out[0], "plain “bill svt” still starts the photo bill message")
print("Twilio Sandbox (no Meta account)")
import base64
os.environ.update(TWILIO_ACCOUNT_SID="AC123", TWILIO_AUTH_TOKEN="twtoken", TWILIO_WHATSAPP_FROM="+14155238886",
                  TWILIO_WEBHOOK_URL="https://site.example/api/wa/twilio", TWILIO_SANDBOX_JOIN="join happy-tiger")
BILL = iter([{"entry_dates": [f"{d}.10.26"], "party": "S.V.P", "total_amount": str(t), "amounts": [str(t)]} for d, t in ((16, 69651), (17, 82909))])
tn = {"i": 0}
def tw(phone, body="", media=(), sign=True):
    tn["i"] += 1
    p = {"From": f"whatsapp:+{phone}", "Body": body, "NumMedia": str(len(media)), "MessageSid": f"SM{tn['i']:04d}"}
    for i, m in enumerate(media): p[f"MediaUrl{i}"] = m
    data = "https://site.example/api/wa/twilio" + "".join(k + p[k] for k in sorted(p))
    sig = base64.b64encode(hmac.new(b"twtoken", data.encode(), hashlib.sha1).digest()).decode() if sign else "bad"
    b0 = len(SENT); r = cl.post("/api/wa/twilio", data=p, headers={"X-Twilio-Signature": sig})
    return r, [t for _, t in SENT[b0:]]
r, out = tw(OWNER, "hi", sign=False); check(r.status_code == 403 and not out, "Twilio: message without Twilio's signature refused")
r, out = tw(OWNER, "join happy-tiger"); check(r.status_code == 200 and out and "Connected" in out[0], "Twilio: “join …” → connected + menu")
check(all(len(t) <= 1600 for t in out), "Twilio: every message within 1,600 characters")
r, out = tw(OWNER, "tank"); check(out and "Tank stock" in out[0], "Twilio: tank → stock")
r, out = tw(OWNER, "bill")
r, out = tw(OWNER, "", media=["https://api.twilio.com/2010-04-01/Accounts/AC123/Messages/MM1/Media/ME1"])
r, out = tw(OWNER, "", media=["https://api.twilio.com/2010-04-01/Accounts/AC123/Messages/MM2/Media/ME2"])
r, out = tw(OWNER, "done")
msg = next((t for t in out if t.startswith("SVT Logistics")), "")
check(msg.endswith("TOTAL = 1,52,560") and "Bill 161026 = 69,651" in msg, "Twilio: bill photos → message in the owner's format")
c.execute("UPDATE pump.wa_sessions SET joined_at = NOW() - interval '61 hours' WHERE phone=%s", (OWNER,))
r, out = tw(OWNER, "rates"); check(out and "send *join happy-tiger*" in out[-1], "Twilio: after 2½ days, reminder to send the join code again")
print(); print("FAILED:" if FAILS else "ALL WHATSAPP CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
