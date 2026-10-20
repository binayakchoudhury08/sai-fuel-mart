"""End-to-end test against a real Postgres. Run:
DATABASE_URL=postgresql://postgres@/sfm?host=/tmp&port=5433 SETUP_CODE=x python tests/test_flows.py"""
import os
import re
import sys
import uuid
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2

DSN = os.environ["DATABASE_URL"]
if "supabase" in DSN or "render" in DSN:
    sys.exit("Refusing to run tests against a real database. Use an empty local test database.")
c0 = psycopg2.connect(DSN); c0.autocommit = True
c0.cursor().execute("DROP SCHEMA IF EXISTS pump CASCADE")
c0.close()

from sfm import create_app  # noqa
from sfm.util import today  # noqa

app = create_app()
app.config["TESTING"] = False
T = today()
Y = T - timedelta(days=1)
FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def sql(q, p=None):
    c = psycopg2.connect(DSN)
    cur = c.cursor()
    cur.execute(q, p)
    r = cur.fetchall() if cur.description else None
    c.commit(); c.close()
    return r


class U:
    def __init__(self):
        self.c = app.test_client()

    def token(self):
        with self.c.session_transaction() as s:
            if "_csrf" not in s:
                s["_csrf"] = "t" + uuid.uuid4().hex
            return s["_csrf"]

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, ft=None, follow=True):
        d = dict(data or {})
        d["_csrf"] = self.token()
        d["_ft"] = ft or uuid.uuid4().hex
        return self.c.post(url, data=d, follow_redirects=follow)


def flashes(r):
    return re.findall(r'class="flash (\w+)"[^>]*>([^<]+)<', r.get_data(as_text=True))


def has(r, text):
    return text in r.get_data(as_text=True)


owner = U()
print("Setup")
r = owner.get("/login")
check(r.status_code == 302 and "/setup" in r.location, "fresh install redirects to setup")
os.environ["SETUP_CODE"] = "letmein"
r = owner.post("/setup", {"setup_code": "wrong", "username": "boss", "password": "Pump@2026x", "password2": "Pump@2026x"})
check(has(r, "Setup code is wrong"), "wrong setup code refused")
r = owner.post("/setup", {"setup_code": "letmein", "username": "boss", "full_name": "Owner", "password": "Pump@2026x",
                          "password2": "Pump@2026x"})
check(r.status_code == 200 and has(r, "Owner account created"), "owner account created")
check(owner.get("/setup").status_code == 302, "setup closed after first user")

print("Configuration")
owner.post("/settings/rates", {"effective_from": (T - timedelta(days=10)).isoformat(), "rate_MS": "100.00",
                               "rate_HSD": "90.00", "rate_CNG": "80"})
r = owner.post("/settings/tank", {"name": "MS tank", "fuel_code": "MS", "capacity_l": "15000"})
owner.post("/settings/tank", {"name": "HSD tank", "fuel_code": "HSD", "capacity_l": "20000"})
tanks = dict(sql("SELECT fuel_code, id FROM pump.tanks"))
owner.post("/settings/nozzle", {"name": "MS-1", "machine_no": "1", "fuel_code": "MS", "tank_id": tanks["MS"], "sort": 1})
owner.post("/settings/nozzle", {"name": "HSD-1", "machine_no": "1", "fuel_code": "HSD", "tank_id": tanks["HSD"], "sort": 2})
owner.post("/settings/nozzle", {"name": "HSD-2", "machine_no": "2", "fuel_code": "HSD", "tank_id": tanks["HSD"], "sort": 3})
noz = dict(sql("SELECT name, id FROM pump.nozzles"))
check(len(noz) == 3, "3 nozzles configured")
r = owner.post("/settings/nozzle", {"name": "Bad", "fuel_code": "MS", "tank_id": tanks["HSD"]})
check(has(r, "same fuel"), "nozzle on wrong-fuel tank refused")

print("Party + lube")
r = owner.post("/parties/new", {"name": "Sharma Transport", "mobile": "9876543210", "opening_balance": "10000",
                                "opening_date": (T - timedelta(days=5)).isoformat(), "credit_limit": "0"})
pid = sql("SELECT id FROM pump.parties WHERE name='Sharma Transport'")[0][0]
r = owner.post("/parties/new", {"name": "sharma transport "})
check(has(r, "already exists"), "duplicate party name refused")
owner.post("/lube/product", {"name": "Servo 4T 1L", "unit": "pcs", "sale_rate": "350", "opening_stock": "10", "reorder_level": "3"})
lp = sql("SELECT id FROM pump.lube_products")[0][0]

print(f"Day 1 ({Y})")
D = f"/day/{Y.isoformat()}"
r = owner.post(f"{D}/readings", {f"closing_{noz['MS-1']}": "1500"})
check(has(r, "enter the opening reading"), "first-ever reading requires an opening")
r = owner.post(f"{D}/readings", {f"opening_{noz['MS-1']}": "1000", f"closing_{noz['MS-1']}": "1500", f"testing_{noz['MS-1']}": "5",
                                 f"opening_{noz['HSD-1']}": "2000", f"closing_{noz['HSD-1']}": "1999"})
check(has(r, "less than opening"), "closing below opening refused")
check(sql("SELECT COUNT(*) FROM pump.nozzle_readings")[0][0] == 0, "nothing saved when one row is bad (all-or-nothing)")
r = owner.post(f"{D}/readings", {f"opening_{noz['MS-1']}": "1000", f"closing_{noz['MS-1']}": "1500", f"testing_{noz['MS-1']}": "5",
                                 f"opening_{noz['HSD-1']}": "2000", f"closing_{noz['HSD-1']}": "2800",
                                 f"opening_{noz['HSD-2']}": "500", f"closing_{noz['HSD-2']}": "700"})
check(sql("SELECT COUNT(*) FROM pump.nozzle_readings")[0][0] == 3, "3 readings saved")
# MS 495 L x 100 = 49,500 ; HSD 800+200=1000 L x 90 = 90,000 ; fuel = 139,500
modes = dict(sql("SELECT name, id FROM pump.collection_modes"))
owner.post(f"{D}/cash", {f"mode_{modes['PhonePe']}": "20000", f"mode_{modes['Card swipe']}": "5000"})
# credit: 100 L HSD = 9,000 ; driver cash 2,000 ; payment cash 5,000 ; bank payment 3,000 (no drawer effect)
r = owner.post("/parties/txn", {"party": pid, "business_date": Y.isoformat(), "kind": "fuel", "fuel_code": "HSD", "qty": "100",
                                "vehicle_no": "wb 23 a 1234", "challan_no": "C1"})
check(has(r, "Fuel on credit"), "fuel credit via day-page quick form (no JS)")
owner.post(f"/parties/{pid}/txn", {"business_date": Y.isoformat(), "kind": "cash_advance", "amount": "2000"})
ft = uuid.uuid4().hex
owner.post(f"/parties/{pid}/txn", {"business_date": Y.isoformat(), "kind": "payment", "amount": "5000", "pay_mode": "Cash"}, ft=ft)
r = owner.post(f"/parties/{pid}/txn", {"business_date": Y.isoformat(), "kind": "payment", "amount": "5000", "pay_mode": "Cash"}, ft=ft)
check(has(r, "already saved once"), "double-tap on payment ignored")
check(sql("SELECT COUNT(*) FROM pump.party_txns WHERE kind='payment'")[0][0] == 1, "only ONE payment stored after double submit")
r = owner.post(f"/parties/{pid}/txn", {"business_date": Y.isoformat(), "kind": "payment", "amount": "3000", "pay_mode": "Bank transfer"})
check(has(r, "UTR"), "bank payment needs a reference")
owner.post(f"/parties/{pid}/txn", {"business_date": Y.isoformat(), "kind": "payment", "amount": "3000", "pay_mode": "Bank transfer", "reference": "UTR123"})
rcpts = [x[0] for x in sql("SELECT receipt_no FROM pump.party_txns WHERE kind='payment' ORDER BY id")]
check(len(set(rcpts)) == 2 and all(rcpts), f"payments got unique receipt numbers {rcpts}")
# lube: 2 cash (700), 1 credit (350)
owner.post(f"{D}/lube-sale", {"product_id": lp, "qty": "2", "pay_mode": "Cash"})
owner.post(f"{D}/lube-sale", {"product_id": lp, "qty": "1", "pay_mode": "Credit", "party_id": pid})
r = owner.post(f"{D}/lube-sale", {"product_id": lp, "qty": "50", "pay_mode": "Cash"})
check(has(r, "Only 7"), "cannot sell more lube than in stock")
owner.post(f"{D}/expense", {"description": "Tea", "category": "Tea & snacks", "amount": "150", "from_counter": "1"})
owner.post(f"{D}/expense", {"description": "Electricity bill", "amount": "4000", "from_counter": "0"})
# expected = 139500 + 1050 - (9000+350) - 25000 + 5000 - 2000 - 150 = 109,050
exp = Decimal("109050.00")
r = owner.post(f"{D}/cash", {"den_500": "200", "den_100": "90", "den_50": "1"})  # 100000+9000+50 = 109,050
with app.app_context():
    from sfm import db
    from sfm.services import compute_day, party_balance
    with db.tx() as cur:
        t = compute_day(cur, Y)
        bal = party_balance(cur, pid)
check(t["fuel_total"] == Decimal("139500.00"), f"fuel sale 139,500 (got {t['fuel_total']})")
check(t["expected_cash"] == exp, f"expected cash 1,09,050 (got {t['expected_cash']})")
check(t["counted_cash"] == exp and t["difference"] == 0, f"note count = 1,09,050, difference 0 (got {t['difference']})")
# balance = 10000 + 9000 + 2000 + 350 - 5000 - 3000 = 13,350
check(bal == Decimal("13350.00"), f"party balance 13,350 (got {bal})")
r = owner.get(D)
check(r.status_code == 200 and has(r, "₹1,09,050.00"), "day page renders with Indian formatting")

print("Workflow + locks")
r = owner.post(f"{D}/submit", {})
check(sql("SELECT status FROM pump.days WHERE business_date=%s", (Y,))[0][0] == "submitted", "day submitted")
owner.post(f"{D}/lock", {})
check(sql("SELECT status FROM pump.days WHERE business_date=%s", (Y,))[0][0] == "locked", "day locked")
r = owner.post(f"/parties/{pid}/txn", {"business_date": Y.isoformat(), "kind": "payment", "amount": "1", "pay_mode": "Cash"})
check(has(r, "locked"), "owner cannot add to locked day through app")
try:
    sql("INSERT INTO pump.expenses (business_date, description, amount) VALUES (%s,'sneaky',1)", (Y,))
    check(False, "database blocks direct insert on locked day")
except psycopg2.Error as e:
    check("locked" in str(e), "database itself blocks direct insert on locked day")
try:
    sql("DELETE FROM pump.party_txns WHERE id=(SELECT MIN(id) FROM pump.party_txns)")
    check(False, "database blocks deleting party entries")
except psycopg2.Error:
    check(True, "database blocks deleting party entries")
try:
    sql("UPDATE pump.audit_log SET summary='x'")
    check(False, "audit log is read-only")
except psycopg2.Error:
    check(True, "audit log cannot be edited")
r = owner.post(f"{D}/unlock", {"reason": ""})
check(has(r, "Write why"), "unlock needs a reason")
owner.post(f"{D}/unlock", {"reason": "Correcting test"})
check(sql("SELECT status FROM pump.days WHERE business_date=%s", (Y,))[0][0] == "open", "owner unlocked with reason")
check(sql("SELECT COUNT(*) FROM pump.audit_log WHERE action='unlock' AND summary LIKE %s", ("%Correcting test%",))[0][0] == 1,
      "unlock reason recorded in audit log")

print(f"Day 2 ({T}) – openings carried forward")
D2 = f"/day/{T.isoformat()}"
r = owner.get(D2)
check(has(r, 'value="1500.00" readonly'), "today's MS-1 opening = yesterday's closing, read-only")
owner.post(f"{D2}/readings", {f"closing_{noz['MS-1']}": "1600", f"closing_{noz['HSD-1']}": "2900", f"closing_{noz['HSD-2']}": "750"})
op = sql("SELECT opening FROM pump.nozzle_readings WHERE business_date=%s AND nozzle_id=%s", (T, noz["MS-1"]))[0][0]
check(op == Decimal("1500.00"), "server used carried-forward opening")

print("Operator restrictions")
owner.post("/users/new", {"username": "ravi", "full_name": "Ravi", "role": "operator", "password": "Ravi@12345"})
op_ = U()
op_.post("/login", {"username": "ravi", "password": "Ravi@12345"})
r = op_.get("/dashboard")
check(r.status_code == 302 and "password" in r.location, "new user forced to change password")
op_.post("/account/password", {"old_password": "Ravi@12345", "new_password": "Ravi#2026a", "new_password2": "Ravi#2026a"})
check(op_.get("/dashboard").status_code == 200, "operator signed in after password change")
r = op_.post(f"{D2}/readings", {f"opening_{noz['MS-1']}": "1400", f"closing_{noz['MS-1']}": "1600"})
check(has(r, "Only the owner can override"), "operator cannot change opening reading")
tid = sql("SELECT id FROM pump.party_txns WHERE kind='payment' ORDER BY id LIMIT 1")[0][0]
r = op_.post(f"/party-txn/{tid}/void", {"reason": "x"}, follow=False)
check(r.status_code == 403, "operator cannot cancel entries")
r = op_.post(f"/parties/{pid}/txn", {"business_date": (T - timedelta(days=5)).isoformat(), "kind": "payment", "amount": "10", "pay_mode": "Cash"})
check(has(r, "Only the owner can change entries older"), "operator cannot back-date beyond window")
r = op_.post(f"/parties/{pid}/txn", {"business_date": (T + timedelta(days=1)).isoformat(), "kind": "payment", "amount": "10", "pay_mode": "Cash"})
check(has(r, "future"), "nobody can enter future dates")
check(op_.get("/settings").status_code == 403 and op_.get("/audit").status_code == 403, "operator blocked from settings & audit")
r = op_.post(f"/parties/{pid}/txn", {"kind": "payment", "amount": "abc", "pay_mode": "Cash"})
check(has(r, "must be a number"), "junk amount rejected, not turned into 0")
bad = U()
for i in range(5):
    bad.post("/login", {"username": "ravi", "password": "wrong"})
r = bad.post("/login", {"username": "ravi", "password": "Ravi#2026a"})
check(has(r, "Try again in"), "5 wrong passwords lock the account for 15 minutes")
r = op_.c.post(f"/parties/{pid}/txn", data={"kind": "payment", "amount": "10", "pay_mode": "Cash", "_csrf": "forged"})
check(r.status_code == 400, "forged form (bad CSRF) rejected")

print("Void + statement")
owner.post(f"/party-txn/{tid}/void", {"reason": "Wrong party"})
with app.app_context():
    with db.tx() as cur:
        bal2 = party_balance(cur, pid)
check(bal2 == Decimal("18350.00"), f"cancelling ₹5,000 payment restores balance to 18,350 (got {bal2})")
r = owner.get(f"/parties/{pid}?from={(T - timedelta(days=10)).isoformat()}")
check(has(r, "Wrong party") and has(r, 'class="void"'), "cancelled payment stays visible, struck through")
lm = sql("SELECT id FROM pump.lube_moves WHERE pay_mode='Credit'")[0][0]
owner.post(f"/lube-move/{lm}/void", {"reason": "returned"})
with app.app_context():
    with db.tx() as cur:
        bal3 = party_balance(cur, pid)
check(bal3 == Decimal("18000.00"), f"cancelling credit lube sale also removes it from party (got {bal3})")
stock = sql("SELECT SUM(qty) FROM pump.lube_moves WHERE NOT voided")[0][0]
check(stock == Decimal("8.00"), f"lube stock back to 8 (got {stock})")

print("Exports + pages")
for url in ["/dashboard", D, D2, f"/day/{Y.isoformat()}/print", "/parties", f"/parties/{pid}", "/lube", "/tanks",
            "/staff", "/staff/salary", "/reports", "/reports/outstanding", "/reports/credit-register", "/settings",
            "/audit", "/users", "/proofs", "/admin/import-legacy", f"/party-txn/{rcpts[1] and sql('SELECT id FROM pump.party_txns WHERE receipt_no=%s',(rcpts[1],))[0][0]}/receipt"]:
    r = owner.get(url)
    check(r.status_code == 200, f"GET {url} → {r.status_code}")
for url, mime in [(f"/parties/{pid}/bill.pdf?from={(T - timedelta(days=10)).isoformat()}", "application/pdf"),
                  (f"/parties/{pid}/bill.xlsx", "spreadsheet"), ("/reports?format=xlsx", "spreadsheet"),
                  ("/reports/outstanding?format=xlsx", "spreadsheet"), ("/reports/credit-register?format=xlsx", "spreadsheet"),
                  ("/reports/lube.xlsx", "spreadsheet"), ("/reports/backup.xlsx", "spreadsheet")]:
    r = owner.get(url)
    check(r.status_code == 200 and mime in r.mimetype and len(r.data) > 1000, f"download {url.split('?')[0]} ({len(r.data)} bytes)")
open("/tmp/bill.pdf", "wb").write(owner.get(f"/parties/{pid}/bill.pdf?from={(T - timedelta(days=10)).isoformat()}").data)

print("Tanks + ATG + staff + rate change")
owner.post("/tanks/dip", {"business_date": Y.isoformat(), f"volume_{tanks['MS']}": "8000", f"volume_{tanks['HSD']}": "12000"})
owner.post("/tanks/receipt", {"business_date": T.isoformat(), "tank_id": tanks["HSD"], "invoice_no": "INV9", "invoice_qty_l": "4000",
                              "received_qty_l": "3980"})
r = owner.post("/tanks/receipt", {"business_date": T.isoformat(), "tank_id": tanks["HSD"], "invoice_no": "INV9", "received_qty_l": "1"})
check(has(r, "already recorded"), "same tanker invoice cannot be entered twice")
owner.post("/tanks/dip", {"business_date": T.isoformat(), f"volume_{tanks['HSD']}": "15800"})
with app.app_context():
    from sfm.services import tank_reconciliation
    with db.tx() as cur:
        rec = {r["tank"]["fuel_code"]: r for r in tank_reconciliation(cur, T)}
# HSD book = 12000 + 3980 - (100 + 50) = 15830 ; dip 15800 → -30
check(rec["HSD"]["book"] == Decimal("15830.00") and rec["HSD"]["variance"] == Decimal("-30.00"),
      f"tank variance HSD −30 L (got book {rec['HSD']['book']}, var {rec['HSD']['variance']})")
os.environ["TANK_SYNC_SECRET"] = "s3"
r = app.test_client().post("/api/tank-sync", json={"secret": "nope", "tanks": []})
check(r.status_code == 403, "ATG sync rejects wrong secret")
r = app.test_client().post("/api/tank-sync", json={"secret": "s3", "tanks": [{"fuel_type": "MS", "net_volume": "7500.5", "dip_mm": "1200"}]})
check(r.status_code == 200 and r.json["synced"] == ["MS"], f"ATG sync accepted (old bookmarklet format) {r.json}")
owner.post("/staff/save", {"emp_code": "EMP001", "name": "Raju", "monthly_salary": "12000"})
sid = sql("SELECT id FROM pump.staff")[0][0]
owner.post("/staff/attendance", {"work_date": T.isoformat(), f"st_{sid}": "P"})
owner.post("/staff/payment", {"staff_id": sid, "kind": "advance", "amount": "500", "pay_mode": "Cash from counter",
                              "business_date": T.isoformat(), "for_month": T.strftime("%Y-%m")})
with app.app_context():
    with db.tx() as cur:
        t2 = compute_day(cur, T)
check(t2["staff_cash"] == Decimal("500.00"), "staff advance from counter reduces today's expected cash")
owner.post("/settings/rates", {"effective_from": T.isoformat(), "rate_MS": "101"})
r1 = sql("SELECT rate FROM pump.nozzle_readings WHERE business_date=%s AND nozzle_id=%s", (T, noz["MS-1"]))[0][0]
r0 = sql("SELECT rate FROM pump.nozzle_readings WHERE business_date=%s AND nozzle_id=%s", (Y, noz["MS-1"]))[0][0]
check(r1 == Decimal("101.00") and r0 == Decimal("100.00"), "rate change re-prices today only; yesterday keeps old rate")

print()
print("FAILED:" if FAILS else "ALL CHECKS PASSED", len(FAILS) or "")
for f in FAILS:
    print(" -", f)
sys.exit(1 if FAILS else 0)
