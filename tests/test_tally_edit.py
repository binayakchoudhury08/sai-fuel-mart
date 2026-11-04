"""Full row editing (website + WhatsApp), meter closings, wrong-meter safety — on the owner's real workbook."""
import os, sys, io, re, uuid, json, hmac, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "supabase" in os.environ["DATABASE_URL"]:
    sys.exit("Refusing to run tests against a real database.")
OWNER = "919999900001"
os.environ.update(SKIP_SCHEMA="1", WA_TOKEN="tok", WA_PHONE_ID="555", WA_VERIFY_TOKEN="v", WA_APP_SECRET="appsecret", WA_OWNERS=f"{OWNER}:boss", CRON_SECRET="c")
from datetime import date
from decimal import Decimal
import psycopg2, openpyxl
import sfm.wa as wa
FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
SENT = []
class R:
    def __init__(s, code, body): s.status_code, s._b, s.text = code, body, json.dumps(body)
    def json(s): return s._b
def fake_post(url, json=None, **k):
    if url.endswith("/messages"):
        if json.get("status") == "read": return R(200, {})
        SENT.append(json["text"]["body"] if json.get("type") == "text" else json.get("type")); return R(200, {"messages": [{"id": "x"}]})
    return R(200, {"id": "M"})
wa.requests.post = fake_post
from sfm import create_app
app = create_app(); o = app.test_client()
with o.session_transaction() as s: s["_csrf"] = "t"
o.post("/login", data={"_csrf": "t", "username": "boss", "password": "Pump@2026x"})
k = psycopg2.connect(os.environ["DATABASE_URL"]); k.autocommit = True; c = k.cursor()
def post(url, data):
    with o.session_transaction() as s: s["_csrf"] = "t"
    return o.post(url, data={"_csrf": "t", "_ft": uuid.uuid4().hex, **data}, follow_redirects=True)
def book():
    c.execute("SELECT data FROM pump.tally_books ORDER BY id DESC LIMIT 1"); return openpyxl.load_workbook(io.BytesIO(bytes(c.fetchone()[0])), data_only=True)
D = "2026-11-01"
print("Website")
h = o.get(f"/tally?d={D}").get_data(as_text=True)
check('list="ledgers"' in h and 'list="items"' in h and 'name="closing"' in h and "Tally Excel" in h, "every row editable (name dropdowns, amount, qty, rate) + meter closings + side menu entry")
post("/tally/edit", {"d": D, "sheet": "HSD SALES", "row": "1162", "party": "Phone Pe", "amount": "25885"})
check(book()["HSD SALES"]["C1162"].value == "Phone Pe", "HSD row: party changed Paytm → Phone Pe")
post("/tally/edit", {"d": D, "sheet": "LUBE SALES", "row": "308", "qty": "10", "rate": "15"})
lb = book()["LUBE SALES"]; check((lb["E308"].value, lb["F308"].value, lb["G308"].value) == (10, 15, 150), "lube row: qty 10 × rate 15 = 150")
post("/tally/edit", {"d": D, "sheet": "RECEIPT", "row": "283", "party": "Phone Pe", "to": "SVT LOGISTICS"})
rc = book()["RECEIPT"]; check((rc["C283"].value, rc["D283"].value) == ("Phone Pe", "SVT LOGISTICS"), "receipt row: received in / from changed")
mr = book()["METER READING"]; r1 = [r for r in range(1034, 1042) if mr.cell(r, 4).value == "01 NOZZLE PETROL DU1"][0]
post("/tally/edit", {"d": D, "sheet": "METER READING", "meter": "01 NOZZLE PETROL DU1", "closing": "690995.33"})
mr = book()["METER READING"]; check(mr.cell(r1, 7).value == 690995.33 and abs(mr.cell(r1, 9).value - (690995.33 - mr.cell(r1, 6).value - 5)) < 0.01,
                                   f"meter DU1 closing → 690,995.33, net recalculated ({mr.cell(r1, 9).value})")
post("/tally/edit", {"d": D, "sheet": "HSD SALES", "row": "1161", "party": "DTPLUS HPCL", "amount": "3059", "qty": "300", "orig_qty": "30", "rate": "101.98"})
hs = book()["HSD SALES"]; check((hs["D1161"].value, hs["F1161"].value) == (300, 30594), f"HSD litres 30 → 300: amount = 300 × 101.98 = {hs['F1161'].value}")
print("Wrong meter → nothing added")
c.execute("INSERT INTO pump.tally_names VALUES ('meter','MS-1','02 NOZZLE DIESEL DU 1') ON CONFLICT (kind, site_name) DO UPDATE SET tally_name=EXCLUDED.tally_name")
c.execute("SELECT id FROM pump.nozzles WHERE name='MS-1'"); nid = c.fetchone()[0]
c.execute("INSERT INTO pump.days (business_date) VALUES ('2026-11-02') ON CONFLICT DO NOTHING")
c.execute("INSERT INTO pump.nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate) VALUES ('2026-11-02',%s,0,691091.33,0,100) ON CONFLICT (business_date, nozzle_id) DO UPDATE SET closing=691091.33", (nid,))
c.execute("SELECT name FROM pump.nozzles WHERE active AND name<>'MS-1'")
for (nm,) in c.fetchall(): c.execute("INSERT INTO pump.tally_names VALUES ('meter',%s,'04 NOZZLE DIESEL DU2') ON CONFLICT DO NOTHING", (nm,))
for kind, site in (("party", "SVT Logistics"), ("party", "Sharma Transport"), ("expense", "Tea"), ("expense", "Electricity bill"), ("mode", "Cash"), ("mode", "Bank transfer"), ("mode", "UPI at pump"), ("item", "Servo 4T 1L")):
    c.execute("INSERT INTO pump.tally_names VALUES (%s,%s,'Cash') ON CONFLICT DO NOTHING", (kind, site))
r = post("/tally/add", {"d": "2026-11-02"})
mr = book()["METER READING"]
du1 = [rr for rr in range(1042, 1050) if mr.cell(rr, 4).value == "01 NOZZLE PETROL DU1"]
check(du1 and mr.cell(du1[0], 7).value == 691091.33, "petrol nozzle mapped to a diesel meter → its reading still lands on petrol DU1 (matched by reading)")
c.execute("SELECT data FROM pump.tally_books ORDER BY id DESC LIMIT 1"); latest = bytes(c.fetchone()[0])
from sfm.tally_book import Adder
from sfm.util import UserError
try:
    Adder(latest).meters(date(2026, 11, 3), {"01 NOZZLE PETROL DU1": "5"}); bad = False
except UserError as e:
    bad = "nothing added" in str(e)
check(bad, "a reading that fits no meter (5 L) → refused, nothing added")
print("WhatsApp")
n = {"i": 0}
def say(t):
    n["i"] += 1
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": OWNER, "id": f"te{n['i']}", "type": "text", "text": {"body": t}}]}}]}]}).encode()
    b0 = len(SENT); o.post("/api/wa/webhook", data=body, headers={"X-Hub-Signature-256": "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest(), "Content-Type": "application/json"})
    return SENT[b0:]
out = say("excel rename hsd 1/11 dillip to vns logistics"); check(out and "→ *VNS Logistics*" in out[0], f"rename asks YES: {out[0][:80] if out else ''}")
say("yes"); check(any(book()["HSD SALES"].cell(r_, 3).value == "VNS Logistics" and book()["HSD SALES"].cell(r_, 6).value == 5000 for r_ in range(1160, 1175)), "HSD: Dillip Rana row now VNS Logistics")
out = say("excel meter 1/11 petrol du2 1028100"); check(out and "03 NOZZLE PETROL DU2" in out[0], f"meter edit asks YES: {out[0][:70] if out else ''}")
out = say("yes"); mr = book()["METER READING"]
check(any(mr.cell(r_, 4).value == "03 NOZZLE PETROL DU2" and mr.cell(r_, 7).value == 1028100 for r_ in range(1034, 1042)), "meter DU2 petrol closing 1,028,100 saved")
out = say("cash 1/11"); check(out and "Meter = sales" in out[0] or (out and "❌" in out[0]), f"cash shows the meter vs sales check: {out[0][:120] if out else ''}")
out = say("excel ms 1/11 phone pe 1700 L"); check(out and "1700 L" in out[0], f"litres on WhatsApp asks YES: {out[0][:70] if out else ''}")
say("yes"); ms = book()["MS SALES"]
check((ms["D244"].value, ms["F244"].value) == (1700, 187544), f"MS Phone Pe = 1700 L → amount 1,87,544 ({ms['F244'].value})")
out = say("cash 1/11"); check(out and "❌ MS litres" in out[0], f"meter ≠ sales now shown: {out[0][:160] if out else ''}")
print(); print("FAILED:" if FAILS else "ALL EDIT CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
