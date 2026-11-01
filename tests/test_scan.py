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
print("Bill Message — the 7 SVT bills up to 18/10/2026")
BILLS = [(12, 54251), (13, 75261), (14, 47827), (15, 53438), (16, 69651), (17, 82909), (18, 53641)]
QUEUE = [{"bill_no": str(1060 + i), "bill_date": f"{d}.10.26", "party": "S.V.P",
          "rows": [{"date": f"{d}.10.26", "particulars": "OD02CL2129", "quantity": "", "rate": "", "amount": str(t)}],
          "total_quantity": "", "total_amount": str(t)} for i, (d, t) in enumerate(BILLS)]
QUEUE = [{"party": "S.V.P", "bill_no": str(1060 + i), "top_date": f"{d + 1}.10.26", "entry_dates": [f"{d}.10.26", "''"],
          "total_amount": str(t), "amounts": [str(t - 1000), "1000"]} for i, (d, t) in enumerate(BILLS)]   # top date = next day
KEYS, SEEN = [], []
def bill_post(url, json=None, headers=None, **k):
    KEYS.append((headers or {}).get("x-goog-api-key"))
    SEEN.append({"model": url.rsplit("/", 1)[1].split(":")[0], "cfg": json["generationConfig"]})
    return ok(QUEUE.pop(0))
scan.requests.post = bill_post
os.environ["GEMINI_API_KEY_MESSAGE"] = "msgkey"
with cl.session_transaction() as s_: s_["_csrf"] = "t"
mb = cl.post("/bill-message/batch", data={"_csrf": "t"}).json["id"]
got = []
for _ in BILLS:
    with cl.session_transaction() as s_: s_["_csrf"] = "t"
    got.append(cl.post(f"/bill-message/batch/{mb}/page", data={"_csrf": "t", "photo": (io.BytesIO(b"\xff\xd8\xff" + b"0" * 60), "b.jpg")},
                       content_type="multipart/form-data").json)
check(all(g["ok"] and g["checked"] for g in got) and got[0]["label"] == "121026" and got[0]["total"] == "54,251",
      "uses the ENTRY date (12/10) — not the date at the top (13/10)")
check(True,
      f"7 bills read, each total checked (first: Bill {got[0]['label']} = {got[0]['total']})")
check(set(KEYS) == {"msgkey"}, "Bill Message uses its own key (GEMINI_API_KEY_MESSAGE)")
check(SEEN[0]["model"] == "gemini-3.5-flash-lite", f"fastest model first for totals: {SEEN[0]['model']}")
check(SEEN[0]["cfg"].get("thinkingConfig") == {"thinkingLevel": "minimal"}, "thinking switched off (minimal) — answers straight away")
check(set(SEEN[0]["cfg"]["responseSchema"]["properties"]) == {"party", "bill_no", "entry_dates", "top_date", "total_amount", "amounts"},
      "asks only for party, bill no., entry dates, TOTAL and the amount column")
check(len(SEEN) == 7, f"one short request per bill when totals match ({len(SEEN)} for 7 bills)")
h = cl.get(f"/bill-message/{mb}").get_data(as_text=True)
import html as _h
msg = _h.unescape(re.search(r'<textarea id="text"[^>]*>(.*?)</textarea>', h, re.S).group(1))
want = """SVT Logistics
Bill upto 18/10/2026

Bill 121026 = 54,251
Bill 131026 = 75,261
Bill 141026 = 47,827
Bill 151026 = 53,438
Bill 161026 = 69,651
Bill 171026 = 82,909
Bill 181026 = 53,641
--------------------
TOTAL = 4,36,978"""
check(msg == want, "message matches the owner's format exactly (TOTAL = 4,36,978)")
if msg != want: print(msg)
with cl.session_transaction() as s_: s_["_csrf"] = "t"
cl.post(f"/bill-message/{mb}/sent", data={"_csrf": "t", "text": msg})
check("4,36,978" in cl.get("/bill-message").get_data(as_text=True), "sent message kept in the history")
print("Fast reader — second look and thinking fallback")
SEQ = []
def mismatch_post(url, json=None, headers=None, **k):
    text = json["contents"][0]["parts"][-1]["text"]
    SEQ.append(("second" if "First reading" in text else "first"))
    if "First reading" in text:
        return ok({"bill_date": "12.10.26", "total_amount": "54251", "amounts": ["53251", "1000"]})
    return ok({"bill_date": "12.10.26", "total_amount": "54751", "amounts": ["53251", "1000"]})   # 2 read as 7
scan.requests.post = mismatch_post
r = scan.read_bill_total(b"\xff\xd8\xff0")
check(SEQ == ["first", "second"] and scan.bill_summary(r)["total"] == Decimal("54251") and r["_pass"] == 2,
      "TOTAL not matching the amounts → second look fixed it (54,751 → 54,251)")
TH = []
def nothink_post(url, json=None, headers=None, **k):
    TH.append("thinkingConfig" in json["generationConfig"])
    if "thinkingConfig" in json["generationConfig"]:
        return Resp(400, {"error": {"message": "Unknown field thinkingConfig.thinkingLevel"}})
    return ok({"bill_date": "13.10.26", "total_amount": "75261", "amounts": ["75261"]})
scan.requests.post = nothink_post
r = scan.read_bill_total(b"\xff\xd8\xff0")
check(TH == [True, False] and r["total_amount"] == "75261", "model without the thinking setting → retried without it")
print("Bill Message — dates, models, time limit, errors")
x = scan.bill_summary({"top_date": "26.10.26", "entry_dates": ["25.10.26", "''", "''"], "total_amount": "110718", "amounts": ["110718"]})
check(x["label"] == "251026" and x["top_date"].day == 26, "sample bill: top 26/10, entries 25/10 → Bill 251026")
x = scan.bill_summary({"top_date": "22.10.26", "entry_dates": ["21.10.26"], "total_amount": "5", "amounts": ["5"]})
check(x["label"] == "211026", "top 22/10, entries 21/10 → Bill 211026")
x = scan.bill_summary({"top_date": "22.10.26", "entry_dates": ["20.10.26", "''", "21.10.26"], "total_amount": "5", "amounts": ["5"]})
check(x["label"] == "211026" and x["several_dates"], "entries on two dates → latest entry date, and pointed out")
x = scan.bill_summary({"top_date": "22.10.26", "entry_dates": [], "total_amount": "5", "amounts": ["5"]})
check(x["label"] == "221026" and x["no_entry_date"], "no entry date readable → top date used, and pointed out")
USED = []
def only38_get(url, headers=None, params=None, timeout=None):
    return Resp(200, {"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]})
def rec_post(url, json=None, headers=None, timeout=None, **k):
    USED.append(url.rsplit("/", 1)[1].split(":")[0])
    return ok({"entry_dates": ["12.10.26"], "total_amount": "100", "amounts": ["100"]})
scan._AVAILABLE.clear(); scan.requests.get, scan.requests.post = only38_get, rec_post
os.environ["GEMINI_API_KEY_MESSAGE"] = "msgkey2"
scan.read_bill_total(b"\xff\xd8\xff0")
check(USED == ["gemini-3.8-flash"], f"asks Google which models the key has; skips the missing ones ({USED})")
import time as _time, requests as _rq
scan._AVAILABLE.clear(); scan.requests.get = lambda *a, **k: (_ for _ in ()).throw(_rq.RequestException("x"))
def slow_post(url, json=None, headers=None, timeout=None, **k):
    raise _rq.Timeout("slow")
scan.requests.post = slow_post
scan.BUDGET = 8
t0 = _time.time()
try:
    scan.read_bill_total(b"\xff\xd8\xff0"); check(False, "slow Google → clear message")
except Exception as ex:
    check("took too long" in str(ex) and _time.time() - t0 < 9, f"slow Google → stops in time with a clear message: {ex}")
scan.BUDGET = 45
import sfm.views.messages as mv
real = scan.read_bill_total
scan.read_bill_total = lambda *a, **k: (_ for _ in ()).throw(KeyError("boom"))
with cl.session_transaction() as s_: s_["_csrf"] = "t"
mb2 = cl.post("/bill-message/batch", data={"_csrf": "t"}).json["id"]
with cl.session_transaction() as s_: s_["_csrf"] = "t"
r = cl.post(f"/bill-message/batch/{mb2}/page", data={"_csrf": "t", "photo": (io.BytesIO(b"\xff\xd8\xff0"), "b.jpg")}, content_type="multipart/form-data")
check(r.status_code == 500 and r.is_json and r.json["retry"], "unexpected error → still a proper reply (shown as Retry, not “no internet”)")
scan.read_bill_total = real
print(); print("FAILED:" if FAILS else "ALL SCAN CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
