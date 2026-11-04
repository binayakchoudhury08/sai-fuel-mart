"""Stage 1: the owner's Tally workbook in the website — upload, names, add a day, adjust, daily check, convert, versions."""
import os, sys, io, re, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "supabase" in os.environ["DATABASE_URL"]:
    sys.exit("Refusing to run tests against a real database.")
os.environ.setdefault("SKIP_SCHEMA", "1")
from decimal import Decimal
from datetime import date
import psycopg2, openpyxl
FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
BOOK = os.environ.get("SFM_BOOK", "/home/claude/conv/SFM Converter Studio/SFM_Daily_Readings.xlsx")
D = date(2026, 11, 2)
k = psycopg2.connect(os.environ["DATABASE_URL"]); k.autocommit = True; c = k.cursor()
# a day of Daily Closing on the website (2 Nov): 3 nozzles, credit to SVT, an expense, a lube sale, a payment received
c.execute("SELECT id, name FROM pump.nozzles WHERE active ORDER BY fuel_code, sort, id"); noz = c.fetchall()
c.execute("DELETE FROM pump.tally_books; DELETE FROM pump.tally_names")
for nid, nm in noz:
    c.execute("SELECT closing FROM pump.nozzle_readings WHERE nozzle_id=%s ORDER BY business_date DESC LIMIT 1", (nid,))
    c.execute("INSERT INTO pump.nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate) VALUES (%s,%s,0,%s,0,100) ON CONFLICT (business_date, nozzle_id) DO UPDATE SET closing=EXCLUDED.closing",
              (D, nid, {"MS-1": 691091.33, "HSD-1": 5700107.85, "HSD-2": 4538309.12}.get(nm, 1)))
c.execute("SELECT id FROM pump.parties WHERE name='SVT Logistics'"); svt = c.fetchone()[0]
c.execute("INSERT INTO pump.days (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (D,))
c.execute("INSERT INTO pump.day_credit (business_date, party_id, amount) VALUES (%s,%s,50000) ON CONFLICT (business_date, party_id) DO UPDATE SET amount=50000", (D, svt))
c.execute("INSERT INTO pump.expenses (business_date, category, description, amount, from_counter) VALUES (%s,'Staff welfare','Mess',4000,TRUE)", (D,))
c.execute("INSERT INTO pump.party_txns (party_id, business_date, kind, credit, pay_mode, receipt_no) VALUES (%s,%s,'payment',25000,'Cash','R-T1')", (svt, D))
from sfm import create_app
app = create_app()
def login(u, p):
    cl = app.test_client()
    with cl.session_transaction() as s: s["_csrf"] = "t"
    cl.post("/login", data={"_csrf": "t", "username": u, "password": p}); return cl
o = login("boss", "Pump@2026x")
def post(url, data, **kw):
    with o.session_transaction() as s: s["_csrf"] = "t"
    return o.post(url, data={"_csrf": "t", "_ft": uuid.uuid4().hex, **data}, **kw)
print("Upload")
r = post("/tally/upload", {"book": (open(BOOK, "rb"), "SFM_Daily_Readings.xlsx")}, content_type="multipart/form-data", follow_redirects=True)
check("Workbook saved" in r.get_data(as_text=True), "owner uploads the workbook → kept in the website")
print("Tally names")
h = o.get(f"/tally?d={D}").get_data(as_text=True)
need = re.findall(r'name="site" value="([^"]+)"', h)
check({"MS-1", "HSD-1", "HSD-2", "SVT Logistics", "Mess", "Cash"} <= set(need), f"names still needed are listed: {need}")
rows_ = re.findall(r'<tr><td>[^<]*</td><td><b>([^<]+)</b></td>.*?</tr>', h, re.S)
guess = {}
for tr in re.findall(r"<tr><td>[^<]*</td><td><b>[^<]+</b></td>.*?</tr>", h, re.S):
    nm = re.search(r"<b>([^<]+)</b>", tr).group(1); sel = re.search(r"<option selected>([^<]+)</option>", tr)
    guess[nm] = sel.group(1) if sel else None
check(guess.get("SVT Logistics") == "SVT LOGISTICS" and guess.get("Mess") == "Mess Staff", f"good guesses from your LEDGER MASTER: SVT Logistics → {guess.get('SVT Logistics')}, Mess → {guess.get('Mess')}")
check(guess.get("Sharma Transport") is None, f"no wrong guess for a different transporter (Sharma Transport → {guess.get('Sharma Transport')})")
kinds_ = dict((s_, k_) for k_, s_ in re.findall(r'name="kind" value="([^"]+)"><input type="hidden" name="site" value="([^"]+)"', h))
fill = {"meter": {"MS-1": "01 NOZZLE PETROL DU1", "HSD-1": "02 NOZZLE DIESEL DU 1", "HSD-2": "04 NOZZLE DIESEL DU2"},
        "party": {"SVT Logistics": "SVT LOGISTICS"}, "expense": {"Mess": "Mess Staff"}, "mode": {"Cash": "Cash"}}
for site, kind in kinds_.items():
    tname = fill.get(kind, {}).get(site) or {"party": "SRC TRANSPORT", "expense": "Misc Exp", "item": "HP RACER 2 40 ML", "mode": "Cash", "meter": "08 NOZZLE CNG 2 DU4"}[kind]
    post("/tally/names", {"kind": kind, "site": site, "tally": tname})
print("Add the day from Daily Closing")
r = post("/tally/add", {"d": D.isoformat()}, follow_redirects=True); t = r.get_data(as_text=True)
check("added:" in t, "2 Nov added from Daily Closing: " + (re.search(r"02/11/2026 added: [^.<]+", t).group(0) if "added:" in t else re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", t))[200:420]))
c.execute("SELECT data FROM pump.tally_books ORDER BY id DESC LIMIT 1"); wb = openpyxl.load_workbook(io.BytesIO(bytes(c.fetchone()[0])), data_only=True)
hs = wb["HSD SALES"]; last = max(r_ for r_ in range(2, hs.max_row + 1) if hs.cell(r_, 2).value not in (None, "") and hs.cell(r_, 3).value == "SVT LOGISTICS")
rate_ = hs.cell(last, 5).value
check(hs.cell(last, 3).value == "SVT LOGISTICS" and hs.cell(last, 6).value == 50000 and abs(hs.cell(last, 4).value - round(50000 / rate_, 2)) < 0.006,
      f"HSD SALES: SVT LOGISTICS 50,000 at that day’s price ₹{rate_} → qty {hs.cell(last, 4).value}")
pm = wb["PAYMENT"]; lp = max(r_ for r_ in range(2, pm.max_row + 1) if pm.cell(r_, 2).value not in (None, "") and pm.cell(r_, 4).value == "Mess Staff")
check((pm.cell(lp, 3).value, pm.cell(lp, 4).value, pm.cell(lp, 5).value) == ("Cash", "Mess Staff", 4000), "PAYMENT: Cash → Mess Staff 4,000")
mr = wb["METER READING"]; r0 = 90 + (D - date(2026, 7, 6)).days * 8
check(mr.cell(r0, 7).value == 691091.33 and mr.cell(r0, 6).value == 690991.33 and mr.cell(r0, 9).value == 95, "METER: DU1 petrol closing 691,091.33, opening = 1 Nov closing, net 95")
r = post("/tally/add", {"d": D.isoformat()}, follow_redirects=True)
check("already in the workbook" in r.get_data(as_text=True), "adding the same day twice is refused (no duplicates)")
print("Your adjustment")
post("/tally/set", {"d": D.isoformat(), "sheet": "MS SALES", "name": "Phone Pe", "amount": "10000"})
post("/tally/set", {"d": D.isoformat(), "sheet": "MS SALES", "name": "Phone Pe", "amount": "10480"})
c.execute("SELECT data FROM pump.tally_books ORDER BY id DESC LIMIT 1"); wb = openpyxl.load_workbook(io.BytesIO(bytes(c.fetchone()[0])), data_only=True)
ms = wb["MS SALES"]; rows = [r_ for r_ in range(2, ms.max_row + 1) if ms.cell(r_, 2).value not in (None, "") and ms.cell(r_, 3).value == "Phone Pe" and ms.cell(r_, 6).value in (10000, 10480)]
check(len(rows) == 1 and ms.cell(rows[0], 6).value == 10480 and ms.cell(rows[0], 8).value == 10480, f"MS SALES Phone Pe: added, then changed to 10,480 (one row, day total 10,480)")
print("Daily check + convert")
h = o.get(f"/tally?d={D}").get_data(as_text=True)
cash = re.search(r"Final cash balance</b></td><td class=\"num\"><b>([^<]+)", h)
check(cash is not None, f"daily check shows the final cash balance: {cash.group(1) if cash else '?'}")
r = post("/tally/convert", {"from": D.isoformat(), "to": D.isoformat()})
xml = r.get_data(as_text=True)
check(r.mimetype == "application/xml" and xml.count("<VOUCHER ") >= 4 and "SVT LOGISTICS" in xml and "SAI FUEL MART 26-27" in xml,
      f"Convert 2/11 → your converter's XML ({xml.count('<VOUCHER ')} vouchers)" if r.mimetype == "application/xml" else "convert: " + re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", r.get_data(as_text=True)))[:300])
print("Owner only + versions")
m = login("mgr2", "Mgr@123456x")
check(m.get("/tally").status_code in (302, 403), "a manager cannot open Tally Excel")
c.execute("SELECT id FROM pump.tally_books ORDER BY id LIMIT 1"); first = c.fetchone()[0]
post(f"/tally/restore/{first}", {})
c.execute("SELECT note FROM pump.tally_books ORDER BY id DESC LIMIT 1"); check("Restored version" in c.fetchone()[0], "restore an earlier version (kept as a new version)")
print(); print("FAILED:" if FAILS else "ALL TALLY CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
