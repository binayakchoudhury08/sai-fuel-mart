"""Stage 3 (website side): WhatsApp starts the GitHub robot, PIN handed over once (never logged/stored), company asked,
steps with screenshots relayed to WhatsApp, XML and PDF doors protected by the secret."""
import os, sys, json, hmac, hashlib, io, base64
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "supabase" in os.environ["DATABASE_URL"]:
    sys.exit("Refusing to run tests against a real database.")
OWNER = "919999900001"
os.environ.update(SKIP_SCHEMA="1", WA_TOKEN="tok", WA_PHONE_ID="555", WA_VERIFY_TOKEN="v", WA_APP_SECRET="appsecret",
                  WA_OWNERS=f"{OWNER}:boss", CRON_SECRET="agentsecret", GITHUB_TOKEN="ghp_test", GITHUB_REPO="binayak/sai-fuel-mart")
import psycopg2
import sfm.wa as wa, sfm.tally_jobs as tj
FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
SENT, DISPATCH = [], []
class R:
    def __init__(s, code, body): s.status_code, s._b, s.text = code, body, json.dumps(body)
    def json(s): return s._b
def fake_post(url, json=None, headers=None, timeout=None, files=None, data=None, **k):
    if "api.github.com" in url:
        DISPATCH.append((url, json, headers)); return R(204, {})
    if url.endswith("/media"): return R(200, {"id": "M1"})
    if url.endswith("/messages"):
        if json.get("status") == "read": return R(200, {})
        t = json.get("type")
        SENT.append(json["text"]["body"] if t == "text" else f"{t.upper()}:" + (json.get(t, {}).get("caption") or json.get(t, {}).get("filename", "")))
        return R(200, {"messages": [{"id": "x"}]})
    return R(200, {})
wa.requests.post = fake_post; tj.requests.post = fake_post
from sfm import create_app
app = create_app(); cl = app.test_client()
k = psycopg2.connect(os.environ["DATABASE_URL"]); k.autocommit = True; c = k.cursor()
n = {"i": 0}
def send(text):
    n["i"] += 1
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": OWNER, "id": f"tj{n['i']}", "type": "text", "text": {"body": text}}]}}]}]}).encode()
    b0 = len(SENT)
    cl.post("/api/wa/webhook", data=body, headers={"X-Hub-Signature-256": "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest(), "Content-Type": "application/json"})
    return SENT[b0:]
A = {"x-cron-secret": "agentsecret"}
print("Start from WhatsApp")
out = send("tally import 1/11 to 1/11")
check(out and "Tally job #" in out[0] and "secure PIN" in out[0], f"“tally import 1/11 to 1/11” → robot started, asks for PIN: {out[0][:90] if out else ''}")
check(DISPATCH and DISPATCH[-1][0].endswith("/repos/binayak/sai-fuel-mart/actions/workflows/tally-agent.yml/dispatches") and DISPATCH[-1][2]["Authorization"] == "Bearer ghp_test",
      "GitHub Actions started with the job id")
jid = int(DISPATCH[-1][1]["inputs"]["job_id"])
print("Robot ↔ owner")
check(cl.post(f"/api/tally/job/{jid}/claim", headers=A).status_code == 200, "robot claims job once")
check(cl.post(f"/api/tally/job/{jid}/claim", headers=A).status_code == 409, "workflow replay rejected")
check(cl.get(f"/api/tally/job/{jid}/wait/pin").status_code == 401, "the robot’s doors need the secret")
check(cl.get(f"/api/tally/job/{jid}/wait/pin", headers=A).json["state"] == "waiting", "robot waits for the PIN")
out = send("482913")
check(out and "PIN received" in out[0], "owner sends the PIN on WhatsApp")
c.execute("SELECT detail FROM pump.wa_log WHERE detail LIKE '%%482913%%'"); check(not c.fetchall(), "the PIN never appears in the message log")
r = cl.get(f"/api/tally/job/{jid}/wait/pin", headers=A).json
check(r == {"state": "ok", "answer": "482913"}, "robot receives the PIN once")
c.execute("SELECT answer FROM pump.tally_jobs WHERE id=%s", (jid,)); check(c.fetchone()[0] is None, "…and it is deleted from the website at once")
r = cl.get(f"/api/tally/job/{jid}/xml", headers=A)
check(r.status_code == 200 and r.headers.get("X-File-Name") == f"SFM_Tally_Import_01-11-2026_to_01-11-2026_job{jid}.xml" and r.get_data(as_text=True).count("<VOUCHER ") == 25,
      "robot downloads your converter’s XML (25 vouchers)")
png = base64.b64encode(open("/tmp/wa_table.png", "rb").read()).decode()
b0 = len(SENT); cl.post(f"/api/tally/job/{jid}/step", json={"step": "login", "message": "Login successful", "png": png}, headers=A)
check(any(s.startswith("IMAGE:") and "Login successful" in s for s in SENT[b0:]), "each step reaches WhatsApp with its screenshot")
cl.post(f"/api/tally/job/{jid}/step", json={"step": "tally", "message": "Company?", "png": png}, headers=A)
b0 = len(SENT); cl.get(f"/api/tally/job/{jid}/wait/company", headers=A)
check(any("which company" in s for s in SENT[b0:]), "robot asks which company → question on WhatsApp")
out = send("SAI FUEL MART 26-27"); check(out and "Opening" in out[0], "owner answers the company")
check(cl.get(f"/api/tally/job/{jid}/wait/company", headers=A).json == {"state": "ok", "answer": "SAI FUEL MART 26-27"}, "robot receives the company")
r = cl.post(f"/api/tally/job/{jid}/step", json={"step": "import", "message": "Imported", "done": True}, headers=A)
check(not r.json["ok"], "legacy false success rejected")
cl.post(f"/api/tally/job/{jid}/step", json={"step": "company", "message": "Company verified", "png": png}, headers=A)
cl.post(f"/api/tally/job/{jid}/step", json={"step": "awaiting_import", "message": "Review then IMPORT or STOP", "png": png}, headers=A)
check("Approval" in send("IMPORT")[0], "owner approves exact file")
check(cl.get(f"/api/tally/job/{jid}/wait/import", headers=A).json == {"state": "ok", "answer": "IMPORT"}, "approval consumed once")
cl.post(f"/api/tally/job/{jid}/step", json={"step": "submitting", "message": "Submitting"}, headers=A)
b0 = len(SENT)
cl.post(f"/api/tally/job/{jid}/step", json={"step": "import_verified", "message": "Verified result", "done": True, "png": png,
    "evidence": {"filename": f"SFM_Tally_Import_01-11-2026_to_01-11-2026_job{jid}.xml", "counts": {"created": 25, "altered": 0, "errors": 0, "ignored": 0}}}, headers=A)
c.execute("SELECT state FROM pump.tally_jobs WHERE id=%s", (jid,)); check(c.fetchone()[0] == "done" and any("✅" in s for s in SENT[b0:]), "job marked done; owner told")
n0 = len(DISPATCH); out = send("tally import 1/11 to 1/11")
check(len(DISPATCH) == n0 and out and "may have been partially imported" in out[0], "same dates again → refused (no duplicate vouchers in Tally)")
out = send("tally import again 1/11 to 1/11"); check(len(DISPATCH) == n0 + 1, "“tally import again …” → allowed on purpose")
cl.post(f"/api/tally/job/{DISPATCH[-1][1]['inputs']['job_id']}/claim", headers=A)
send("111111"); cl.get(f"/api/tally/job/{DISPATCH[-1][1]['inputs']['job_id']}/wait/pin", headers=A)
cl.post(f"/api/tally/job/{DISPATCH[-1][1]['inputs']['job_id']}/step", json={"step": "x", "message": "stop", "failed": True}, headers=A)
print("Ledger PDF")
out = send("tally ledger svt this month"); jid2 = int(DISPATCH[-1][1]["inputs"]["job_id"])
c.execute("SELECT kind, params FROM pump.tally_jobs WHERE id=%s", (jid2,)); kind, params = c.fetchone()
check(kind == "ledger" and params["ledger"] == "SVT LOGISTICS" and params["from"].endswith("-01"), f"“tally ledger svt this month” → the Tally ledger SVT LOGISTICS {params}")
b0 = len(SENT); cl.post(f"/api/tally/job/{jid2}/file", data={"file": (io.BytesIO(b"%PDF-1.4 test"), "SVT_LOGISTICS_011126_031126.pdf")}, headers=A, content_type="multipart/form-data")
check(any(s.startswith("DOCUMENT:") for s in SENT[b0:]), "the ledger PDF is delivered to WhatsApp")
print("Blocked XML never starts the robot")
c.execute("INSERT INTO pump.tally_books (data, note) SELECT data, 'copy' FROM pump.tally_books ORDER BY id DESC LIMIT 1")
n0 = len(DISPATCH); out = send("tally import 5/12 to 5/12")
check(len(DISPATCH) == n0 and out and ("blocked" in out[0] or "Not starting" in out[0]), f"no rows for that date → not started: {out[0][:70] if out else ''}")
print(); print("FAILED:" if FAILS else "ALL TALLY-AGENT CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
