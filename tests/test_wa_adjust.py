"""WhatsApp guided adjustment with tap-menus from the owner's Excel (LEDGER / ITEM MASTER + most-used names)."""
import os, sys, json, hmac, hashlib, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "supabase" in os.environ["DATABASE_URL"]:
    sys.exit("Refusing to run tests against a real database.")
OWNER = "919999900001"
os.environ.update(SKIP_SCHEMA="1", WA_TOKEN="tok", WA_PHONE_ID="555", WA_VERIFY_TOKEN="v", WA_APP_SECRET="appsecret", WA_OWNERS=f"{OWNER}:boss", CRON_SECRET="c")
os.environ.pop("WA_LISTS", None)
from datetime import date
import psycopg2, openpyxl
import sfm.wa as wa
FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
SENT, LISTS = [], []
class R:
    def __init__(s, code, body): s.status_code, s._b, s.text = code, body, json.dumps(body)
    def json(s): return s._b
def fake_post(url, json=None, headers=None, timeout=None, files=None, data=None, **k):
    if url.endswith("/messages"):
        if json.get("status") == "read": return R(200, {})
        if json.get("type") == "interactive":
            rows = [r for sec in json["interactive"]["action"]["sections"] for r in sec["rows"]]
            LISTS.append(rows); SENT.append("LIST:" + json["interactive"]["body"]["text"]); return R(200, {"messages": [{"id": "x"}]})
        SENT.append(json["text"]["body"] if json.get("type") == "text" else json.get("type")); return R(200, {"messages": [{"id": "x"}]})
    return R(200, {"id": "M"})
wa.requests.post = fake_post
from sfm import create_app
app = create_app(); cl = app.test_client()
k = psycopg2.connect(os.environ["DATABASE_URL"]); k.autocommit = True; c = k.cursor()
n = {"i": 0}
def post(msg):
    n["i"] += 1; msg = dict(msg, **{"from": OWNER, "id": f"ad{n['i']}"})
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}).encode()
    b0 = len(SENT)
    cl.post("/api/wa/webhook", data=body, headers={"X-Hub-Signature-256": "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest(), "Content-Type": "application/json"})
    return SENT[b0:]
say = lambda t: post({"type": "text", "text": {"body": t}})
tap = lambda rid: post({"type": "interactive", "interactive": {"type": "list_reply", "list_reply": {"id": rid, "title": rid}}})
def book():
    c.execute("SELECT data FROM pump.tally_books ORDER BY id DESC LIMIT 1"); return openpyxl.load_workbook(io.BytesIO(bytes(c.fetchone()[0])), data_only=True)
print("Sheet menu")
out = say("excel adjust 1/11")
check(out and out[0].startswith("LIST:") and [r["title"] for r in LISTS[-1]] == ["HSD SALES", "MS SALES", "CNG SALES", "LUBE SALES", "PAYMENT", "RECEIPT"], "tap-menu of the 6 sheets")
print("MS SALES → most-used names → amount → YES")
out = tap("adj:s:1"); titles = [r["title"] for r in LISTS[-1]]
check("Phone Pe" in titles and "Cash" in titles and titles[-2:] == ["🔎 Search a name", "↩ Back"], f"MS SALES names from your Excel: {titles}")
out = tap(f"adj:p:{titles.index('Phone Pe')}")
check(out and "Phone Pe: 1,88,153" in out[0] and "amount" in out[0], "after tapping Phone Pe: today’s MS rows shown, amount asked")
out = say("190000"); check(out and "YES" in out[0] and "1,90,000" in out[0], "amount 1,90,000 → asks YES")
out = say("yes"); check(out and "changed row" in out[0], f"YES → {out[0][:60] if out else ''}")
ms = book()["MS SALES"]; check(ms["F244"].value == 190000 and ms["C244"].value == "Phone Pe", "workbook: MS SALES Phone Pe = 1,90,000 (same row)")
print("HSD → search “svt” in LEDGER MASTER")
say("excel adjust 1/11"); tap("adj:s:0"); out = tap("adj:find"); check(out and "first letters" in out[0], "🔎 Search → type letters")
out = say("svt"); check(out and "SVT LOGISTICS" in out[0], f"“svt” → SVT LOGISTICS: {out[0][:80] if out else ''}")
out = say("422000"); say("yes")
hs = book()["HSD SALES"]; check(any(hs.cell(r, 3).value == "SVT LOGISTICS" and hs.cell(r, 6).value == 422000 for r in range(1160, 1175)), "workbook: HSD SVT LOGISTICS = 4,22,000")
print("RECEIPT → in, then from")
say("excel adjust 1/11"); out = tap("adj:s:5"); t1 = [r["title"] for r in LISTS[-1]]
check("DTPLUS HPCL" in t1, f"RECEIPT ‘received IN’ choices from your Excel: {t1[:5]}")
tap(f"adj:p:{t1.index('DTPLUS HPCL')}"); out = say("vns"); 
if out and out[0].startswith("LIST:"):
    t2 = [r["title"] for r in LISTS[-1]]; tap(f"adj:p:{[i for i, t in enumerate(t2) if t.lower().startswith('vns')][0]}")
say("50000"); out = say("yes")
rc = book()["RECEIPT"]; check(any(rc.cell(r, 3).value == "DTPLUS HPCL" and str(rc.cell(r, 4).value).startswith("VNS") and rc.cell(r, 5).value == 50000 for r in range(270, 300)),
                              "workbook: RECEIPT DTPLUS HPCL ← VNS Logistics 50,000 (new row)")
print("LUBE with qty @ rate, and typed numbers instead of taps")
say("excel adjust 1/11"); say("4"); out = say("racer")
if out and out[0].startswith("LIST:"):
    t3 = [r["title"] for r in LISTS[-1]]; say(str([i for i, t in enumerate(t3) if "RACER 2 40" in t][0] + 1))
out = say("15 @ 14"); check(out and "15 × 14" in out[0], f"lube qty 15 @ 14 asked YES: {out[0][:60] if out else ''}"); say("yes")
lb = book()["LUBE SALES"]; check(any("RACER 2 40" in str(lb.cell(r, 4).value) and lb.cell(r, 5).value == 15 and lb.cell(r, 7).value == 210 for r in range(300, 320)), "workbook: lube HP RACER 2 40 ML 15 × 14 = 210")
print(); print("FAILED:" if FAILS else "ALL ADJUST CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
