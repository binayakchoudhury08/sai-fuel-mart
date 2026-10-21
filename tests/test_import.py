"""Builds old-app tables (same columns as v1) with messy data, then imports."""
import os, sys, uuid, re
from datetime import timedelta
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2
DSN = os.environ["DATABASE_URL"]
if "supabase" in DSN or "render" in DSN:
    sys.exit("Refusing to run tests against a real database. Use an empty local test database.")
c = psycopg2.connect(DSN); c.autocommit = True; k = c.cursor()
k.execute("DROP SCHEMA IF EXISTS pump CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
k.execute("""
CREATE TABLE settings (id INT PRIMARY KEY, ms_rate REAL, hsd_rate REAL, cng_rate REAL, station_name TEXT, station_address TEXT, gstin TEXT, phone_number TEXT);
INSERT INTO settings VALUES (1, 102.46, 93.72, 0, 'Sai Fuel Mart', 'Basirhat', '19ABCDE1234F1Z5', '9800000000');
CREATE TABLE nozzle_master (id SERIAL PRIMARY KEY, nozzle_name TEXT, machine_no TEXT, fuel_type TEXT, status TEXT);
INSERT INTO nozzle_master (nozzle_name, machine_no, fuel_type, status) VALUES ('MS 1','1','MS','Active'),('HSD 1','1','HSD','Active'),('HSD 2','2','HSD','Active');
CREATE TABLE nozzle_entries (id SERIAL PRIMARY KEY, entry_date DATE, nozzle_id INT, opening_reading REAL, closing_reading REAL, testing_qty REAL, total_sale REAL, created_at TEXT);
CREATE TABLE daily_closing (id SERIAL PRIMARY KEY, date DATE, ms_litres REAL, hsd_litres REAL, cng_sale REAL, total_fuel_sale REAL, lube_sale REAL, digital_collection REAL, phonepe REAL, card_swipe REAL, hp_pay REAL, hpcl_otp REAL, upi_other REAL, credit_given REAL, transport_received REAL, net_credit_due REAL, total_expense REAL, cash_in_hand REAL);
CREATE TABLE credit_transporters (id SERIAL PRIMARY KEY, party_name TEXT, mobile TEXT, vehicle_no TEXT, opening_balance REAL, credit_given REAL, payment_received REAL, balance_due REAL, status TEXT, fuel_credit REAL, lube_credit REAL, discount REAL);
CREATE TABLE fuel_receipts (id SERIAL PRIMARY KEY, receipt_date TEXT, invoice_no TEXT, vehicle_no TEXT, order_no TEXT, carrier_no TEXT, carrier_name TEXT, po_no TEXT, po_date TEXT, water_checked TEXT, comp1_fuel TEXT, comp1_vol REAL, comp1_challan_dip REAL, comp1_dip REAL, comp2_fuel TEXT, comp2_vol REAL, comp2_challan_dip REAL, comp2_dip REAL, total_ms_vol REAL, total_hsd_vol REAL, density REAL, challan_density REAL, temperature_c REAL, dip_before REAL, dip_after REAL, total_dip_variance REAL);
INSERT INTO credit_transporters (party_name, mobile, vehicle_no, opening_balance, balance_due, status, discount) VALUES
 ('SVT Logistics','98300 11111','WB23A1111',5000, 31872, 'Active', 1.25),
 ('Maa Tara Carriers','9830022222','',0, 9372, 'Active', 0),
 ('maa tara carriers ','','',0, 0, 'Active', 0);
CREATE TABLE transport_entries (id SERIAL PRIMARY KEY, entry_date DATE, sl_no INT, transporter_id INT, transporter_name TEXT, challan_no TEXT, vehicle_no TEXT, slip_no TEXT, hsd_qty REAL, rate REAL, hsd_amount REAL, cash_taken REAL, total_amount REAL);
CREATE TABLE transporter_ledger (id SERIAL PRIMARY KEY, date DATE, transporter_id INT, transporter_name TEXT, entry_type TEXT, fuel_credit REAL, lube_credit REAL, received_amount REAL, balance_after REAL, remarks TEXT);
CREATE TABLE lube_stock (id SERIAL PRIMARY KEY, product_name TEXT, selling_rate REAL, opening_stock REAL, purchase_qty REAL, sale_qty REAL, closing_stock REAL);
INSERT INTO lube_stock (product_name, selling_rate, closing_stock) VALUES ('HP Racer 4T',380,12),('Shell 20W40',420,-2),('hp racer 4t',380,3);
CREATE TABLE staff_master (id SERIAL PRIMARY KEY, emp_id TEXT, staff_name TEXT, role TEXT, department TEXT, joined_date TEXT, bank_account TEXT, shift TEXT, status TEXT);
INSERT INTO staff_master (emp_id, staff_name, role, status, joined_date) VALUES ('EMP001','Raju Das','Attendant','Active','2025-01-05'),('','Bapi','Attendant','Active',NULL);
CREATE TABLE attendance (id SERIAL PRIMARY KEY, date DATE, staff_name TEXT, attendance_status TEXT);
CREATE TABLE salary_payments (id SERIAL PRIMARY KEY, payment_date TEXT, emp_id TEXT, employee_name TEXT, payment_mode TEXT, bank_account TEXT, amount REAL, month_name TEXT, remarks TEXT);
INSERT INTO salary_payments (payment_date, emp_id, employee_name, payment_mode, amount, month_name) VALUES ('2026-10-01','EMP001','Raju Das','Cash',9000,'September 2026');
CREATE TABLE tank_level (id SERIAL PRIMARY KEY, date TEXT, fuel_type TEXT, current_stock REAL, capacity REAL, dip_mm REAL, water_dip_mm REAL, temperature_c REAL, density_kg_m3 REAL);
CREATE TABLE activity_log (id SERIAL PRIMARY KEY, logged_at TIMESTAMP DEFAULT NOW(), username TEXT, role TEXT, module TEXT, action TEXT, description TEXT);
INSERT INTO activity_log (username, role, module, action, description) VALUES ('admin','admin','Credit Transport','Created','Received 5000 from SVT');
""")
from datetime import date
T = date.today()
for i in range(3, 0, -1):
    d = T - timedelta(days=i)
    base = (3 - i) * 500
    k.execute("INSERT INTO nozzle_entries (entry_date,nozzle_id,opening_reading,closing_reading,testing_qty) VALUES (%s,1,%s,%s,5),(%s,2,%s,%s,0),(%s,2,%s,%s,0),(%s,3,%s,%s,0)",
              (d, 1000+base, 1500+base, d, 2000+base, 2500+base, d, 2000+base, 2500+base, d, 900, 800))  # dup HSD1 row + bad HSD2 row
    k.execute("INSERT INTO daily_closing (date, ms_litres, hsd_litres, total_fuel_sale, lube_sale, phonepe, card_swipe, credit_given, transport_received, total_expense, cash_in_hand) VALUES (%s,495,500,97577,760,20000,3000,9372,5000,200,70765)", (d,))
    k.execute("INSERT INTO attendance (date, staff_name, attendance_status) VALUES (%s,'Raju Das','Present'),(%s,'bapi','Absent')", (d, d))
    k.execute("INSERT INTO tank_level (date, fuel_type, current_stock, capacity) VALUES (%s,'MS',8000,15000),(%s,'HSD',12000,20000)", (d, d))
d1 = T - timedelta(days=2)
k.execute("INSERT INTO transport_entries (entry_date,sl_no,transporter_id,transporter_name,challan_no,vehicle_no,slip_no,hsd_qty,rate,hsd_amount,cash_taken,total_amount) VALUES (%s,3,1,'SVT Logistics','C77','WB23A1111','S1',200,93.72,18744,1000,19744)", (d1,))
k.execute("INSERT INTO fuel_receipts (receipt_date, invoice_no, vehicle_no, order_no, carrier_name, comp1_fuel, comp1_vol, comp1_challan_dip, comp1_dip, comp2_fuel, comp2_vol, total_ms_vol, total_hsd_vol, challan_density, density, total_dip_variance) VALUES (%s,'INV1','WB11T','O1','IOC','MS',4000,1200,1196,'HSD',8000,4000,8000,835,836,-4)", (d1.isoformat(),))
k.execute("""INSERT INTO transporter_ledger (date,transporter_id,transporter_name,entry_type,fuel_credit,lube_credit,received_amount) VALUES
 (%s,1,'SVT Logistics','Transport Entry',19744,0,0),
 (%s,1,'SVT Logistics','Fuel Credit',9372,0,0),
 (%s,1,'SVT Logistics','Fuel Payment Received',0,0,5000),
 (%s,2,'Maa Tara Carriers','Fuel Credit',9372,0,0),
 (%s,1,'SVT Logistics','Entry Deleted',-500,0,0)""", (d1, d1, d1, d1, d1))
c.close()
# SVT expected from entries: 5000 + 18744 + 1000 + 9372 - 5000 = 29116 ; old app said 31872 -> adjustment +2756
from sfm import create_app
app = create_app()
cl = app.test_client()
with cl.session_transaction() as s: s["_csrf"] = "t"
os.environ["SETUP_CODE"] = "x"
cl.post("/setup", data={"_csrf": "t", "setup_code": "x", "username": "boss", "password": "Pump@2026x", "password2": "Pump@2026x"})
with cl.session_transaction() as s: s["_csrf"] = "t"
r = cl.get("/admin/import-legacy"); print("inspect page", r.status_code)
r = cl.post("/admin/import-legacy", data={"_csrf": "t", "confirm": "IMPORT"})
import time as _t
while cl.get("/admin/import-legacy/status").json["state"] == "running":
    _t.sleep(0.3)
print("import state:", cl.get("/admin/import-legacy/status").json)
r = cl.get("/admin/import-legacy")
html = r.get_data(as_text=True)
print("import page", r.status_code)
for m in re.findall(r"<b>(\d+)</b><span>([^<]+)</span>", html): print("  ", m[1], m[0])
def q(sql, p=None):
    cc = psycopg2.connect(DSN); cu = cc.cursor(); cu.execute(sql, p); out = cu.fetchall(); cc.close(); return out
print("balances:", q("SELECT p.name, SUM(t.debit-t.credit) FROM pump.parties p LEFT JOIN pump.party_txns t ON t.party_id=p.id AND NOT t.voided GROUP BY p.name ORDER BY 1"))
print("adjustments:", q("SELECT p.name, t.debit, t.credit FROM pump.party_txns t JOIN pump.parties p ON p.id=t.party_id WHERE kind='adjustment'"))
print("nozzle readings:", q("SELECT COUNT(*) FROM pump.nozzle_readings")[0][0], "(3 days x 2 good nozzles; bad HSD2 rows skipped)")
print("days:", q("SELECT business_date, status, is_legacy FROM pump.days ORDER BY 1"))
print("lube stock:", q("SELECT p.name, SUM(m.qty) FROM pump.lube_products p LEFT JOIN pump.lube_moves m ON m.product_id=p.id GROUP BY p.name"))
print("staff:", q("SELECT emp_code, name FROM pump.staff"), "attendance", q("SELECT COUNT(*) FROM pump.attendance")[0][0])
print("register row:", q("SELECT sl_no, debit, cash_taken, qty FROM pump.party_txns WHERE kind='fuel' AND sl_no IS NOT NULL"))
print("discount/L:", q("SELECT name, discount_per_litre FROM pump.parties WHERE discount_per_litre>0"))
print("receipts:", q("SELECT t.fuel_code, received_qty_l, order_no, carrier_name, compartments IS NOT NULL FROM pump.fuel_receipts r JOIN pump.tanks t ON t.id=r.tank_id ORDER BY 1"))
print("issues listed:", html.count("<li>"))
r = cl.post("/admin/import-legacy", data={"_csrf": "t", "confirm": "IMPORT"}); print("second import blocked:", "already imported" in r.get_data(as_text=True).lower() or r.status_code)
# continue today's closing after import: opening must carry from old data
r = cl.get(f"/day/{T.isoformat()}"); h = r.get_data(as_text=True)
print("today opens with old closing 2500:", 'value="2500.00" readonly' in h, "| 1 day ago legacy page:", cl.get(f"/day/{(T-timedelta(days=1)).isoformat()}").status_code)
for u in ["/dashboard", "/reports", "/parties", "/audit", "/lube", "/staff/salary?m=2026-09", "/tanks"]:
    print("  GET", u, cl.get(u).status_code)
