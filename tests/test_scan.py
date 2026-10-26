"""Scan bill page: rules on the real sample page (SVT, bill 1070) + save through the website (Gemini replaced by a stub)."""
import os, sys, io, re, json, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSN = os.environ["DATABASE_URL"]
if "supabase" in DSN:
    sys.exit("Refusing to run tests against a real database.")
os.environ.update(SKIP_SCHEMA="1", GEMINI_API_KEY="testkey")
from decimal import Decimal
import psycopg2
import sfm.scan as scan
from tests.mock_gemini import PAGE
FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
print("Rules")
e, c, n = scan.build_entries(PAGE)
check(len(e) == 6, "12 lines → 6 entries (vehicle + its CASH line)")
check(sum(x["fuel_amount"] + x["cash"] for x in e) == Decimal("110718"), "total ₹1,10,718")
check(c["qty_ok"] and c["amount_ok"], "page totals 968 L and ₹1,10,718 confirmed")
check(e[2]["vehicle"] == "OD02CL2129", "‘ODO2CL2129’ cleaned to OD02CL2129")
check(all(str(x["date"]) == "2026-10-25" and x["rate"] == Decimal("101.98") for x in e), "ditto marks → 25.10.26 and ₹101.98")
v = {"bill_no": "9", "rows": [{"date": "26.10.26", "particulars": "OD02CL2129", "quantity": "", "amount": "2000"},
                              {"date": "''", "particulars": "OD02CB9566", "quantity": "100", "rate": "101.98", "amount": "10500"}]}
e2, _, _ = scan.build_entries(v)
check(e2[0]["qty"] is None and e2[0]["cash"] == 2000, "vehicle with only an amount → cash only")
check(any("Amount written" in s for s in e2[1]["notes"]), "wrong amount (qty × rate) is flagged")
print("Page reader (Gemini) — simulated Google")
import copy
from tests.mock_gemini import next_page, PAGE_1071, PAGE_1072
class Resp:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self): return self._b
def ok(obj): return Resp(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]})
CALLS = []
LAST = {"page": None}
MODE = {"busy_first": False, "misread": False, "no_schema": False, "bad_key": False}
MISREAD = copy.deepcopy(PAGE); MISREAD["rows"][2]["amount"] = "22282"     # 3 read as 8 on row 3
def fake_post(url, json=None, headers=None, params=None, timeout=None):
    model = url.rsplit("/", 1)[1].split(":")[0]
    text = json["contents"][0]["parts"][-1]["text"]
    CALLS.append({"model": model, "key_in_header": (headers or {}).get("x-goog-api-key") == "testkey",
                  "key_in_url": "key=" in url or bool(params), "schema": "responseSchema" in json.get("generationConfig", {}),
                  "second_look": "Your first reading" in text})
    if MODE["bad_key"]:
        return Resp(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
    if "Reply with the single word OK" in text:
        return ok("OK") if model != "gemini-3.8-flash" or not MODE["busy_first"] else Resp(429, {"error": {"message": "Resource exhausted"}})
    if MODE["busy_first"] and model == "gemini-3.8-flash":
        return Resp(429, {"error": {"message": "You exceeded your current quota"}})
    if MODE["no_schema"] and "responseSchema" in json["generationConfig"]:
        return Resp(400, {"error": {"message": "Invalid JSON payload: unknown field responseSchema"}})
    if MODE["misread"]:
        return ok(PAGE if "Your first reading" in text else MISREAD)
    if "Your first reading" in text:          # a second look at the SAME photo
        return ok(LAST["page"])
    LAST["page"] = next_page()
    return ok(LAST["page"])
def fake_get(url, headers=None, params=None, timeout=None):
    return Resp(200, {"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
                                 {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                                 {"name": "models/text-embedding-005", "supportedGenerationMethods": ["embedContent"]}]})
scan.requests.post, scan.requests.get = fake_post, fake_get
scan.time = __import__("time"); import time as _t; _t.sleep = lambda s: None
img = b"\xff\xd8\xff" + b"0" * 50
from tests import mock_gemini as _mg
_mg._n["i"] = 0
r = scan.read_page(img)
c0 = CALLS[-1]
check(c0["model"] == "gemini-3.8-flash" and r["_model"] == "gemini-3.8-flash", "uses the newest free model gemini-3.8-flash")
check(c0["key_in_header"] and not c0["key_in_url"], "key sent in the request header (not in the web address)")
check(c0["schema"], "asks for the exact table format (structured output)")
check(r.get("_pass") == 1 and len(CALLS) == 1, "a page whose totals match is read once")
CALLS.clear(); MODE["busy_first"] = True; _mg._n["i"] = 0
r = scan.read_page(img)
check([c["model"] for c in CALLS] == ["gemini-3.8-flash", "gemini-3.5-flash-lite"] and r["_model"] == "gemini-3.5-flash-lite",
      f"limit reached on 3.8 Flash → falls back to 3.5 Flash-Lite ({[c['model'] for c in CALLS]})")
MODE["busy_first"] = False
CALLS.clear(); MODE["misread"] = True
r = scan.read_page(img)
e, c, n = scan.build_entries(r)
check(len(CALLS) == 2 and CALLS[1]["second_look"] and r["_pass"] == 2, "totals did not match → second, focused look")
check(c["amount_ok"] and r["rows"][2]["amount"] == "22232", "second look fixed the misread amount (22282 → 22232)")
MODE["misread"] = False
CALLS.clear(); MODE["no_schema"] = True; _mg._n["i"] = 0
r = scan.read_page(img)
check([x["schema"] for x in CALLS] == [True, False] and r["bill_no"] == "1070", "model without table format → retries as plain JSON")
MODE["no_schema"] = False
MODE["bad_key"] = True
try:
    scan.read_page(img); check(False, "wrong key gives a clear message")
except Exception as ex:
    check("refused the key" in str(ex), f"wrong key gives a clear message: {ex}")
MODE["bad_key"] = False
MODE["busy_first"] = True
t = scan.test_connection()
check(t["models"] == ["gemini-3.8-flash", "gemini-3.5-flash-lite"] and t["ok"] and t["chosen"] == "gemini-3.5-flash-lite",
      f"owner test: finds the free models and a working one ({t['models']} → {t['chosen']})")
MODE["busy_first"] = False
_mg._n["i"] = 0
print("Website — 3 pages of one transporter")
from sfm import create_app
app = create_app()
k = psycopg2.connect(DSN); k.autocommit = True; cu = k.cursor()
cl = app.test_client()
with cl.session_transaction() as s: s["_csrf"] = "t"
cl.post("/login", data={"_csrf": "t", "username": "mgr2", "password": "Mgr@123456x"})
with cl.session_transaction() as s: s["_csrf"] = "t"
bid = cl.post("/transport/scan/batch", data={"_csrf": "t"}).json["id"]
reads = []
for _ in range(3):
    with cl.session_transaction() as s: s["_csrf"] = "t"
    reads.append(cl.post(f"/transport/scan/batch/{bid}/page", data={"_csrf": "t", "photo": (io.BytesIO(b"\xff\xd8\xff" + b"0" * 100), "p.jpg")},
                         content_type="multipart/form-data").json)
check([r["bill_no"] for r in reads] == ["1070", "1071", "1072"], f"3 pages read one by one: {[r['bill_no'] for r in reads]}")
h = cl.get(f"/transport/scan/batch/{bid}").get_data(as_text=True)
sel = re.search(r'<option value="(\d+)" selected>([^<]+)</option>', h)
check(sel and sel.group(2) == "SVT Logistics", "transporter for the batch: SVT Logistics")
check("3 pages · 10 entries" in h, "summary at the top: 3 pages · 10 entries")
chips = re.findall(r'href="#page-\d+">\s*([✓⚠]) (\d+) <i>([^<]+)</i>', h)
check([c[1] for c in chips] == ["1070", "1071", "1072"] and chips[2][0] == "⚠", f"one chip per challan, 1072 flagged: {chips}")
check("Amount written 10500 but 100 × 101.98 = 10198" in h, "wrong amount on 1072 is shown (still wrong after a second look)")
check("read twice (checked again)" in h, "page 1072 shows it was checked twice")
check("No quantity — taken as cash only" in h, "vehicle with only an amount on 1071 → cash only")
form = {"_csrf": "t", "_ft": uuid.uuid4().hex, "party": sel.group(1)}
for name, val in re.findall(r'name="((?:challan|rows)_\d+)" value="([^"]*)"', h): form[name] = val
for name, val in re.findall(r'name="((?:use|date|vehicle|slip|qty|rate|cash)_\d+_\d+)" value="([^"]*)"', h): form[name] = val
with cl.session_transaction() as s: s["_csrf"] = "t"
r = cl.post(f"/transport/scan/batch/{bid}/save", data=form, follow_redirects=True)
cu.execute("""SELECT challan_no, COUNT(*), SUM(debit) FROM pump.party_txns t JOIN pump.scans s ON s.id=t.scan_id
              WHERE s.batch_id=%s GROUP BY 1 ORDER BY 1""", (bid,))
got = cu.fetchall()
check([(g[0], g[1]) for g in got] == [("1070", 6), ("1071", 3), ("1072", 1)], f"saved per challan: {[(g[0], g[1], str(g[2])) for g in got]}")
check(got[0][2] == Decimal("110718"), "challan 1070 total ₹1,10,718")
cu.execute("SELECT slip_no, kind, debit FROM pump.party_txns t JOIN pump.scans s ON s.id=t.scan_id WHERE s.bill_no='1071' AND s.batch_id=%s ORDER BY t.id", (bid,))
p71 = cu.fetchall()
check([x[0] for x in p71] == ["88", "89", "90"] and p71[1][1] == "cash_advance", f"1071: Credit Bill No. → Slip No., cash-only row: {p71}")
with cl.session_transaction() as s: s["_csrf"] = "t"
r = cl.post(f"/transport/scan/batch/{bid}/save", data=dict(form, _ft=uuid.uuid4().hex), follow_redirects=True)
check("already saved" in r.get_data(as_text=True), "the same pages cannot be saved twice")
print(); print("FAILED:" if FAILS else "ALL SCAN CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
