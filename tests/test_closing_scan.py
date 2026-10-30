"""Scan closing page: the owner's real 29 Oct 2026 page (as Gemini would read it) → checks → saved Daily Closing."""
import os, sys, copy, re, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSN = os.environ["DATABASE_URL"]
if "supabase" in DSN:
    sys.exit("Refusing to run tests against a real database.")
os.environ.update(SKIP_SCHEMA="1", GEMINI_API_KEY="g")
from decimal import Decimal
from datetime import date, timedelta
import psycopg2
FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
D = date(2026, 9, 29)
PAGE = {"date": "29.09.26",
 "ms": {"nozzles": [{"closing": "689947.87", "opening": "689529.75", "diff": "418.12"}, {"closing": "1025840.46", "opening": "1025198.50", "diff": "641.96"},
                    {"closing": "28818.51", "opening": "28725.92", "diff": "92.59"}], "total": "1152.67", "testing": "15.00", "net": "1137.67"},
 "hsd": {"nozzles": [{"closing": "5683260.78", "opening": "5679369.26", "diff": "3891.52"}, {"closing": "4525076.56", "opening": "4521566.54", "diff": "3510.02"},
                     {"closing": "458718.32", "opening": "458435.89", "diff": "282.43"}], "total": "7683.97", "testing": "15.00", "net": "7668.97"},
 "ms_sale": {"litres": "1137.67", "rate": "110.32", "amount": "125508"}, "hsd_sale": {"litres": "7668.97", "rate": "101.98", "amount": "782082"},
 "fuel_total": "907590",
 "lube": [{"text": "LAALGHOD 1Ltr 1x260", "qty": "1", "rate": "260", "amount": "260"}, {"text": "RACER 2T 20ml 4x14", "qty": "4", "rate": "14", "amount": "56"},
          {"text": "Petrol Plus 5ml 2x10", "qty": "2", "rate": "10", "amount": "20"}],
 "after_lube": "907926", "cng": {"qty": "239.11", "rate": "97.70", "amount": "23361"}, "collection": "931287",
 "lines": [{"sign": "+", "name": "S.V.T", "amount": "700000"}, {"sign": "+", "name": "S.R.C", "amount": "36500"}, {"sign": "+", "name": "V.N.S Logistics", "amount": "91047"},
           {"sign": "=", "name": "", "amount": "1758834"},
           {"sign": "-", "name": "Phonepe", "amount": "217282"}, {"sign": "-", "name": "Card swipe", "amount": "136970"}, {"sign": "-", "name": "HPCL OTP", "amount": "856869"},
           {"sign": "-", "name": "HP Pay", "amount": "15000"}, {"sign": "=", "name": "", "amount": "532713"},
           {"sign": "-", "name": "R.K. Roadways", "amount": "229805"}, {"sign": "-", "name": "V.N.S Logistics", "amount": "79677"}, {"sign": "=", "name": "", "amount": "223231"},
           {"sign": "-", "name": "Gajalaxmi", "amount": "58740"}, {"sign": "-", "name": "Manish", "amount": "25000"}, {"sign": "-", "name": "S.V.P", "amount": "69209"},
           {"sign": "-", "name": "SAI Hitech", "amount": "38000"}, {"sign": "=", "name": "EXP", "amount": "32282"},
           {"sign": "-", "name": "Laxmipriya Tukuda", "amount": "1100"}, {"sign": "-", "name": "C.F (Discount)", "amount": "750"}],
 "final_cash": "30432",
 "dips": [{"fuel": "MS", "dip": "94.4", "litres": "9328", "adjust": "", "net": ""}, {"fuel": "HSD", "dip": "196.2", "litres": "29133", "adjust": "-14", "net": "29119"}]}
k = psycopg2.connect(DSN); k.autocommit = True; c = k.cursor()
# ---- a station like the owner's: 3 MS + 3 HSD nozzles, CNG, rates, transporters, payment modes, lube products
c.execute("SELECT id FROM pump.tanks WHERE fuel_code='MS' LIMIT 1"); ms_t = c.fetchone()[0]
c.execute("SELECT id FROM pump.tanks WHERE fuel_code='HSD' LIMIT 1"); hsd_t = c.fetchone()[0]
c.execute("UPDATE pump.nozzles SET active=FALSE")
noz = {}
for code, tank, names in (("MS", ms_t, ["MS-A", "MS-B", "MS-C"]), ("HSD", hsd_t, ["HSD-A", "HSD-B", "HSD-C"]), ("CNG", None, ["CNG-A"])):
    for i, nm in enumerate(names):
        c.execute("INSERT INTO pump.nozzles (name, fuel_code, tank_id, sort, active) VALUES (%s,%s,%s,%s,TRUE) RETURNING id", (nm, code, tank, i))
        noz[nm] = c.fetchone()[0]
for nm, op in (("MS-A", "689529.75"), ("MS-B", "1025198.50"), ("MS-C", "28725.92"), ("HSD-A", "5679369.26"), ("HSD-B", "4521566.54"), ("HSD-C", "458435.89"), ("CNG-A", "1000")):
    c.execute("INSERT INTO pump.nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate) VALUES (%s,%s,%s,%s,0,100)", (D - timedelta(days=1), noz[nm], Decimal(op) - 1, op))
for code, r in (("MS", "110.32"), ("HSD", "101.98"), ("CNG", "97.70")):
    c.execute("INSERT INTO pump.fuel_rates (fuel_code, effective_from, rate) VALUES (%s,'2026-09-01',%s) ON CONFLICT (fuel_code, effective_from) DO UPDATE SET rate=EXCLUDED.rate", (code, r))
for nm in ("R.K. Roadways", "V.N.S Logistics", "Gajalaxmi", "Manish", "S.V.P", "Sai Hitech", "S.V.T", "S.R.C"):
    c.execute("INSERT INTO pump.parties (name) VALUES (%s) ON CONFLICT DO NOTHING", (nm,))
for nm in ("PhonePe", "Card swipe", "HPCL OTP", "HP Pay"):
    c.execute("INSERT INTO pump.collection_modes (name) VALUES (%s) ON CONFLICT DO NOTHING", (nm,))
for nm, rt in (("Laalghod 1L", 260), ("Racer 2T 20ml", 14), ("Petrol Plus 5ml", 10)):
    c.execute("INSERT INTO pump.lube_products (name, sale_rate) VALUES (%s,%s) ON CONFLICT DO NOTHING", (nm, rt))
import sfm.closing_scan as cs
from sfm import create_app, db
app = create_app()
def analyse(page):
    with app.test_request_context():
        from flask import g
        c.execute("SELECT id, username, full_name, role FROM pump.users WHERE username='boss'"); u = c.fetchone()
        g.user = {"id": u[0], "username": u[1], "full_name": u[2], "role": u[3]}
        with db.tx() as cur:
            return cs.analyse(cur, D, page)
print("Reading the owner's 29 Oct page")
a = analyse(PAGE)
check(not a["flags"] and not a["notdone"], f"every line adds up — no warnings {a['flags'][:3]}{a['notdone'][:2]}")
check(a["fuels"]["MS"]["net"] == Decimal("1137.67") and a["fuels"]["HSD"]["net"] == Decimal("7668.97"), "MS 1137.67 L and HSD 7668.97 L after testing")
check(a["page_cash"] == 30432, f"page adds up to the written cash ₹30,432 ({a['page_cash']})")
kinds = {l["name"]: (l["kind"], l.get("target")) for l in a["lines"] if l["kind"] != "total"}
check(kinds["S.V.T"][0] == "payment" and kinds["Phonepe"][0] == "digital" and kinds["Gajalaxmi"][0] == "credit" and kinds["SAI Hitech"][0] == "credit",
      "S.V.T → payment · PhonePe → digital · Gajalaxmi / Sai Hitech → credit given")
check(kinds["C.F (Discount)"][0] == "expense" and kinds["V.N.S Logistics"][0] == "credit", "C.F (Discount) → General expense; V.N.S (minus) → credit, V.N.S (plus) → payment")
check(all(l["product"] for l in a["lube"]), "all 3 lube lines matched to products")
print("A misread digit is caught")
bad = copy.deepcopy(PAGE); bad["lines"][1]["amount"] = "36900"
b = analyse(bad)
hit = [f for f in b["flags"] if "Running total written 1758834" in f]
check(hit and "1759234" in hit[0] and "S.R.C +36900" in hit[0], f"S.R.C read as 36,900 → flagged, naming the lines to check: {hit[0] if hit else ''}")
print("Babu + unknown name")
extra = copy.deepcopy(PAGE)
extra["lines"][12:12] = [{"sign": "-", "name": "Babu car", "amount": "1500"}, {"sign": "-", "name": "Ramu bhai", "amount": "500"}]
extra["lines"][18]["amount"] = "30282"     # the "EXP" running total after the two extra lines
extra["final_cash"] = "28432"
e = analyse(extra)
ke = {l["name"]: (l["kind"], l.get("target")) for l in e["lines"] if l["kind"] != "total"}
check(ke["Babu car"] == ("sai", "Sai Hitech"), f"“Babu car” → Sai Hitech slip ({ke['Babu car']})")
check(not [f for f in e["flags"] if "Running total" in f], "running totals still add up with the extra lines")
check(ke["Ramu bhai"][0] == "unknown" and any("Ramu bhai" in f and "not matched" in f for f in e["flags"]), "unknown name → warning, not saved by default")
print("Save (as the review screen sends it)")
cl = app.test_client()
with cl.session_transaction() as s: s["_csrf"] = "t"
cl.post("/login", data={"_csrf": "t", "username": "boss", "password": "Pump@2026x"})
import json
c.execute("INSERT INTO pump.closing_scans (business_date, created_by, raw) VALUES (%s,'boss',%s) RETURNING id", (D, json.dumps(extra)))
sid = c.fetchone()[0]
h = cl.get(f"/day/{D}/scan/{sid}").get_data(as_text=True)
form = {"_csrf": "t", "_ft": uuid.uuid4().hex, "cng_open": "1000"}
for nm in ("open_", "close_", "test_", "lube_qty_", "amt_", "cng_qty", "page_cash", "dip_"):
    for name, val in re.findall(rf'name="({nm}[^"]*)" value="([^"]*)"', h): form[name] = val
for name in re.findall(r'name="(lube_use_\d+)"', h): form[name] = "1"
for name, sel in re.findall(r'<select name="((?:kind|target|lube_prod)_\d+)">(.*?)</select>', h, re.S):
    m = re.search(r'<option value="([^"]*)" selected', sel); form[name] = m.group(1) if m else ""
with cl.session_transaction() as s: s["_csrf"] = "t"
r = cl.post(f"/day/{D}/scan/{sid}/save", data=form, follow_redirects=True)
txt = r.get_data(as_text=True)
c.execute("SELECT COUNT(*) FROM pump.nozzle_readings WHERE business_date=%s", (D,)); check(c.fetchone()[0] == 7, "7 meter readings saved (3 MS, 3 HSD, CNG)")
c.execute("SELECT SUM(testing) FROM pump.nozzle_readings r JOIN pump.nozzles n ON n.id=r.nozzle_id WHERE business_date=%s AND n.fuel_code='MS'", (D,))
check(c.fetchone()[0] == 15, "MS testing 15 L saved")
c.execute("SELECT closing FROM pump.nozzle_readings WHERE business_date=%s AND nozzle_id=%s", (D, noz["CNG-A"])); check(c.fetchone()[0] == Decimal("1239.11"), "CNG closing = typed opening 1000 + 239.11")
c.execute("SELECT p.name, d.amount FROM pump.day_credit d JOIN pump.parties p ON p.id=d.party_id WHERE business_date=%s ORDER BY 1", (D,))
cr = dict(c.fetchall())
check(cr.get("R.K. Roadways") == 229805 and cr.get("Gajalaxmi") == 58740 and cr.get("Sai Hitech") == 39500,
      f"credit totals per transporter (Sai Hitech 38,000 + Babu 1,500): {cr}")
c.execute("SELECT COUNT(*) FROM pump.party_txns t JOIN pump.parties p ON p.id=t.party_id WHERE p.name='Sai Hitech' AND business_date=%s AND REPLACE(vehicle_no,' ','')='BABUCAR'", (D,))
check(c.fetchone()[0] == 1, "Babu car → a Credit Transport slip for Sai Hitech")
c.execute("SELECT SUM(credit) FROM pump.party_txns WHERE kind='payment' AND business_date=%s", (D,)); check(c.fetchone()[0] == 827547, "payments received 7,00,000 + 36,500 + 91,047")
c.execute("SELECT SUM(amount) FROM pump.day_collections WHERE business_date=%s", (D,)); check(c.fetchone()[0] == 1226121, "digital 2,17,282 + 1,36,970 + 8,56,869 + 15,000")
c.execute("SELECT category, amount FROM pump.expenses WHERE business_date=%s ORDER BY amount", (D,)); ex = c.fetchall()
check(ex == [("General", 750), ("General", 1100)], f"expenses: C.F 750 + Laxmipriya 1,100 as General ({ex})")
c.execute("SELECT COUNT(*) FROM pump.lube_moves WHERE business_date=%s AND kind='sale'", (D,)); check(c.fetchone()[0] == 3, "3 lube sales")
c.execute("SELECT volume_l FROM pump.tank_dips WHERE business_date=%s ORDER BY tank_id", (D,)); check([x[0] for x in c.fetchall()] == [Decimal("9328"), Decimal("29119")], "dips MS 9,328 L, HSD 29,119 L")
c.execute("SELECT result FROM pump.closing_scans WHERE id=%s", (sid,)); res = c.fetchone()[0]
credit_without_slips = sum(v for k_, v in cr.items() if k_ != "Sai Hitech") + 38000
gap = Decimal(res["expected"]) - Decimal(res["page"])
check(Decimal("499") <= gap <= Decimal("501") and not res["recon_ok"],
      f"system cash = page cash except the skipped unknown ₹500 line (+ paise rounding): gap ₹{gap}; step 3 still asks for the missing slips")
check("Transporter credit totals don’t match the slips" in txt, "after saving: tells the manager that slips are still missing")
print(); print("FAILED:" if FAILS else "ALL CLOSING-SCAN CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
