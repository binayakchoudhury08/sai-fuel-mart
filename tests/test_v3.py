"""v3 checks: permissions, admin limits, lube notebook, attendance rules, schema gating, big downloads."""
import os, sys, uuid, re, time
from datetime import timedelta
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSN = os.environ["DATABASE_URL"]
if "supabase" in DSN:
    sys.exit("Refusing to run tests against a real database.")
os.environ["SKIP_SCHEMA"] = "1"
import psycopg2
from sfm import create_app
from sfm.util import today
app = create_app(); T = today(); FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
def sql(q, p=None):
    c = psycopg2.connect(DSN); cu = c.cursor(); cu.execute(q, p); r = cu.fetchall() if cu.description else None; c.commit(); c.close(); return r
class U:
    def __init__(s, user, pw, ua=None):
        s.c = app.test_client(); s.h = {"User-Agent": ua} if ua else {}
        s.post("/login", {"username": user, "password": pw})
    def tok(s):
        with s.c.session_transaction() as x: x["_csrf"] = "t"
    def post(s, url, data, follow=True):
        s.tok(); return s.c.post(url, data=dict(data, _csrf="t", _ft=uuid.uuid4().hex), follow_redirects=follow, headers=s.h)
    def get(s, url, **kw): return s.c.get(url, headers=s.h, **kw)
def txt(r): return r.get_data(as_text=True)
owner = U("boss", "Pump@2026x")

print("Roles & permissions")
owner.post("/users/new", {"username": "adm", "full_name": "Admin", "role": "admin", "password": "Adm@123456"})
owner.post("/users/new", {"username": "mgr2", "full_name": "Manager", "role": "manager", "password": "Mgr@123456"})
for u, p in (("adm", "Adm@123456"), ("mgr2", "Mgr@123456")):
    x = U(u, p); x.post("/account/password", {"old_password": p, "new_password": p + "x", "new_password2": p + "x"})
adm, mgr = U("adm", "Adm@123456x"), U("mgr2", "Mgr@123456x")
check(adm.get("/settings").status_code == 200 and mgr.get("/settings").status_code == 403, "Admin opens Settings, Manager can't (defaults)")
check(adm.get("/settings/permissions").status_code == 403 and owner.get("/settings/permissions").status_code == 200, "only the owner opens Permissions")
r = adm.post("/users/new", {"username": "adm2", "role": "admin", "password": "Zzz@123456"})
check("Only the owner can create Owner or Admin" in txt(r), "Admin cannot create another Admin/Owner")
r = adm.post("/users/new", {"username": "op9", "role": "operator", "password": "Zzz@123456"})
check(sql("SELECT COUNT(*) FROM pump.users WHERE username='op9'")[0][0] == 1, "Admin can create Staff accounts (owner allowed 'users')")
oid = sql("SELECT id FROM pump.users WHERE username='boss'")[0][0]
r = adm.post(f"/users/{oid}", {"action": "reset", "password": "Hack@123456"})
check("Only the owner can change Owner or Admin" in txt(r), "Admin cannot reset the owner's password")
# owner removes 'setup' from admin and gives 'reports' to staff
form = {}
from sfm.security import PERMISSIONS
for k, _, _, d in PERMISSIONS:
    for role in ("admin", "manager", "operator"):
        if role in d: form[f"{role}:{k}"] = "1"
form.pop("admin:setup", None); form["operator:reports"] = "1"
owner.post("/settings/permissions", form)
time.sleep(0)  # cache is per process; reset happens on save
check(adm.get("/settings").status_code == 403, "owner takes 'setup' away from Admin → Settings blocked at once")
op = U("op9", "Zzz@123456"); op.post("/account/password", {"old_password": "Zzz@123456", "new_password": "Zzz@123456x", "new_password2": "Zzz@123456x"})
op = U("op9", "Zzz@123456x")
check(op.get("/reports").status_code == 200, "owner gives 'reports' to Staff → Staff opens Reports")
check(sql("SELECT COUNT(*) FROM pump.audit_log WHERE entity='permissions'")[0][0] >= 1, "permission change is in the audit log")
h = txt(mgr.get("/dashboard"))
check("Permissions" not in h and "Attendance" in h, "menu shows only what the role may open")

print("Lube notebook")
pid = sql("SELECT id FROM pump.lube_products WHERE active ORDER BY id LIMIT 1")[0][0]
book = sql("SELECT COALESCE(SUM(qty),0) FROM pump.lube_moves WHERE product_id=%s AND NOT voided", (pid,))[0][0]
r = owner.post("/lube/notebook", {"business_date": T.isoformat(), f"add_{pid}": "12", f"count_{pid}": str(book + 12 - 1), "bill": "B-901"})
mv = sql("SELECT kind, qty FROM pump.lube_moves WHERE product_id=%s AND business_date=%s AND kind IN ('purchase','adjust') ORDER BY id DESC LIMIT 2", (pid, T))
check(("purchase", Decimal("12.00")) in mv and ("adjust", Decimal("-1.00")) in mv, f"arrived +12, shelf count 1 short → adjust −1 {mv}")
h = txt(owner.get(f"/lube?d={T.isoformat()}"))
check("Notebook for" in h and "+12.00" in h, "notebook shows opening / arrived / sold / left")

print("Attendance")
sid = sql("SELECT id FROM pump.staff WHERE active ORDER BY id LIMIT 1")[0][0]
r = mgr.post("/staff/attendance", {"work_date": T.isoformat(), f"st_{sid}": "P"}, follow=False)
check(r.status_code == 403, "Manager cannot mark present without a photo")
r = owner.post("/staff/attendance", {"work_date": (T - timedelta(days=9)).isoformat(), f"st_{sid}": "L", "reason": "sick"})
check(sql("SELECT status, marked_via, note FROM pump.attendance WHERE staff_id=%s AND work_date=%s", (sid, T - timedelta(days=9)))[0] == ("L", "manual", "sick"), "owner marks any day, any status, with reason")
owner.post("/staff/attendance/rules", {"back": "1", "start": "00:00", "end": "23:59"})
PHONE = "Mozilla/5.0 (Linux; Android 14) Chrome/129 Mobile Safari/537.36"
mph = U("mgr2", "Mgr@123456x", ua=PHONE)
r = mph.c.post("/staff/attendance/start", data={"_csrf": "t", "staff_id": sid, "work_date": (T - timedelta(days=3)).isoformat()}, headers=mph.h)
mph.tok(); r = mph.c.post("/staff/attendance/start", data={"_csrf": "t", "staff_id": sid, "work_date": (T - timedelta(days=3)).isoformat()}, headers=mph.h)
check(r.status_code == 400 and "previous 1 day" in r.json["error"], f"manager limited to owner's window ({r.json})")
owner.post("/staff/attendance/rules", {"back": "2", "start": "00:00", "end": "00:01"})
mph.tok(); r = mph.c.post("/staff/attendance/start", data={"_csrf": "t", "staff_id": sid, "work_date": T.isoformat()}, headers=mph.h)
check(r.status_code == 400 and "only between" in r.json["error"], "outside the owner's time window → refused")
owner.post("/staff/attendance/rules", {"back": "2", "start": "00:00", "end": "23:59"})
mph.tok(); r = mph.c.post("/staff/attendance/start", data={"_csrf": "t", "staff_id": sid, "work_date": T.isoformat()})
check(r.status_code == 400 and "phone" in r.json["error"], "photo attendance refused from a PC")

print("Vercel readiness")
from sfm import db
v = sql("SELECT value FROM pump.settings WHERE key='schema_version'")
check(bool(v), "schema version stored (cold starts skip the setup)")
t0 = time.time(); os.environ.pop("SKIP_SCHEMA", None); db.ensure_schema(); el = time.time() - t0
check(el < 0.5, f"ensure_schema with unchanged schema is quick ({el*1000:.0f} ms)")
from sfm.storage import deliver
with app.test_request_context():
    os.environ.pop("VERCEL", None)
    r = deliver(b"x" * 5_000_000, "big.xlsx", "application/octet-stream")
    check(r.status_code == 200, "big file served directly outside Vercel")
print(); print("FAILED:" if FAILS else "ALL V3 CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
