"""Insights: profit checked against a hand calculation; variation, receipts, credit score, month comparison."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSN = os.environ["DATABASE_URL"]
if "supabase" in DSN:
    sys.exit("Refusing to run tests against a real database.")
os.environ["SKIP_SCHEMA"] = "1"
from datetime import date
from decimal import Decimal
import psycopg2
FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
k = psycopg2.connect(DSN); k.autocommit = True; c = k.cursor()
c.execute("SELECT id FROM pump.tanks WHERE fuel_code='HSD' AND active ORDER BY id LIMIT 1"); tank = c.fetchone()[0]
c.execute("SELECT id FROM pump.nozzles WHERE fuel_code='HSD' ORDER BY id LIMIT 1"); noz = c.fetchone()[0]
D = lambda d: date(2025, 3, d) if d > 0 else date(2025, 2, -d)
c.execute("""INSERT INTO pump.fuel_receipts (business_date, tank_id, invoice_no, invoice_qty_l, received_qty_l, invoice_amount)
             VALUES (%s,%s,'T0',10000,10000,900000)""", (D(-20), tank))
c.execute("INSERT INTO pump.tank_dips (business_date, tank_id, dip_mm, volume_l, source) VALUES (%s,%s,1000,10000,'manual')", (D(-28), tank))
c.execute("""INSERT INTO pump.fuel_receipts (business_date, tank_id, invoice_no, invoice_qty_l, received_qty_l, invoice_amount,
             dip_before_mm, dip_after_mm, challan_density, observed_density) VALUES (%s,%s,'T1',12000,11980,1104000,900,1700,830,834)""", (D(1), tank))
c.execute("INSERT INTO pump.tank_dips (business_date, tank_id, dip_mm, volume_l, source) VALUES (%s,%s,1500,17960,'manual')", (D(1), tank))
c.execute("INSERT INTO pump.tank_dips (business_date, tank_id, dip_mm, volume_l, source) VALUES (%s,%s,1200,12950,'manual')", (D(2), tank))
c.execute("INSERT INTO pump.nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate) VALUES (%s,%s,100000,104000,0,95)", (D(1), noz))
c.execute("INSERT INTO pump.nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate) VALUES (%s,%s,104000,109000,0,95)", (D(2), noz))
c.execute("INSERT INTO pump.expenses (business_date, category, description, amount) VALUES (%s,'Electricity','bill',500)", (D(1),))
c.execute("SELECT id FROM pump.staff ORDER BY id LIMIT 1"); sid = c.fetchone()[0]
c.execute("INSERT INTO pump.staff_payments (staff_id, business_date, for_month, kind, amount) VALUES (%s,%s,'2025-03','salary',1000)", (sid, D(2)))
for d in (1, 2):
    c.execute("INSERT INTO pump.days (business_date, status) VALUES (%s,'submitted') ON CONFLICT (business_date) DO UPDATE SET status='submitted'", (D(d),))
from sfm import create_app
from sfm import db
from sfm.insights import profit, tank_variation, receipt_checks, credit_scores, month_compare
app = create_app()
with app.app_context():
    with db.tx() as cur:
        p = profit(cur, D(1), D(2))
        hsd = next(f for f in p["fuels"] if f["code"] == "HSD")
        print("Profit (hand calculation)")
        check(hsd["litres"] == 9000 and hsd["sales"] == Decimal("855000.00"), f"HSD sold 9,000 L = ₹8,55,000 ({hsd['litres']}, {hsd['sales']})")
        check(hsd["cost_per_l"] == Decimal("91.09"), f"cost/L = (10,000×90 + 11,04,000) ÷ 22,000 = 91.09 ({hsd['cost_per_l']})")
        check(hsd["margin"] == Decimal("35181.82"), f"margin ₹35,181.82 ({hsd['margin']})")
        check(hsd["stock_diff_l"] == -30 and hsd["stock_diff_value"] == Decimal("-2732.73"), f"stock loss 30 L = −₹2,732.73 ({hsd['stock_diff_l']}, {hsd['stock_diff_value']})")
        check(p["expenses_total"] == 500 and p["staff_total"] == 1000, "expenses ₹500 + staff ₹1,000")
        check(p["net"] == Decimal("30949.09"), f"net profit ₹30,949.09 ({p['net']})")
        check(p["final"], f"all inputs present → final ({p['issues']})")
        c.execute("UPDATE pump.fuel_receipts SET invoice_amount=0 WHERE invoice_no='T1'")
        p2 = profit(cur, D(1), D(2))
        check(not p2["final"] and any("without invoice" in i for i in p2["issues"]), f"missing invoice amount → NOT final: {p2['issues'][:1]}")
        c.execute("UPDATE pump.fuel_receipts SET invoice_amount=1104000 WHERE invoice_no='T1'")
        c.execute("UPDATE pump.days SET status='open' WHERE business_date=%s", (D(2),))
        p3 = profit(cur, D(1), D(2))
        check(not p3["final"] and "not submitted: 02/03" in p3["issues"][0], f"day not submitted → NOT final: {p3['issues'][0]}")
        c.execute("UPDATE pump.days SET status='submitted' WHERE business_date=%s", (D(2),))
        print("Tank variation")
        v = next(x for x in tank_variation(cur, D(1), D(2)) if x["tank"]["id"] == tank)
        check(v["variance"] == -30 and v["sold"] == 9000, f"HSD tank: −30 L on 9,000 L sold ({v['variance']}, {v['sold']})")
        check(v["pct"] == Decimal("-0.33") and v["verdict"] == "normal", f"−0.33 % is inside the 0.5 % tolerance → normal ({v['pct']}, {v['verdict']})")
        print("Tanker receipts")
        rows, tot = receipt_checks(cur, D(1), D(1))
        r = rows[0]
        check(r["short"] == 20 and r["cost_per_l"] == Decimal("92.0000") and r["short_value"] == Decimal("1840.00"),
              f"invoice 12,000 / received 11,980 → short 20 L = ₹1,840 ({r['short']}, {r['short_value']})")
        check(not any("short" in f for f in r["flags"]) and any("density differs by 4.00" in f for f in r["flags"]),
              f"0.17 % short is inside the 0.2 % allowance (not flagged); density 830 vs 834 flagged: {r['flags']}")
        c.execute("INSERT INTO pump.settings(key,value) VALUES ('auto_receipt_short_pct','0.1') ON CONFLICT (key) DO UPDATE SET value='0.1'")
        rows, tot = receipt_checks(cur, D(1), D(1))
        check(any("short 20.00 L" in f for f in rows[0]["flags"]), "with a 0.1 % allowance the same tanker is flagged short")
        c.execute("DELETE FROM pump.settings WHERE key='auto_receipt_short_pct'")
        print("Credit score and month comparison")
        cs = credit_scores(cur, date.today())
        check(cs and all(0 <= x["score"] <= 100 and x["grade"] in "ABCD" for x in cs), f"{len(cs)} transporter(s) scored: " + ", ".join(f"{x['party']['name']} {x['score']} {x['grade']}" for x in cs))
        mc = month_compare(cur, date.today())
        check(mc["rows"] and mc["rows"][0]["label"] == "Total sale", "month comparison builds (this month vs same days last month)")
print(); print("FAILED:" if FAILS else "ALL INSIGHT CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
