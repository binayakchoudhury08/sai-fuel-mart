"""v3.1: temporary access (owner → user, dates, expiry, auto re-lock) and whole-rupee credit slips."""
import os, sys, uuid, subprocess
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
        s.c = app.test_client(); s.h = {"User-Agent": ua} if ua else {}; s.post("/login", {"username": user, "password": pw})
    def post(s, url, data, follow=True):
        with s.c.session_transaction() as x: x["_csrf"] = "t"
        return s.c.post(url, data=dict(data, _csrf="t", _ft=uuid.uuid4().hex), follow_redirects=follow, headers=s.h)
    def get(s, url, **kw): return s.c.get(url, headers=s.h, **kw)
txt = lambda r: r.get_data(as_text=True)
owner, mgr = U("boss", "Pump@2026x"), U("mgr2", "Mgr@123456x")
svt = sql("SELECT id FROM pump.parties WHERE name='SVT Logistics'")[0][0]
mid = sql("SELECT id FROM pump.users WHERE username='mgr2'")[0][0]
OLD, OUT = T - timedelta(days=6), T - timedelta(days=7)
for d in (OLD, OUT):
    sql("INSERT INTO pump.days (business_date, status, is_legacy) VALUES (%s,'locked',TRUE) ON CONFLICT (business_date) DO UPDATE SET status='locked', is_legacy=TRUE", (d,))
slip = lambda u, d, s, **kw: u.post("/transport/entry", dict({"party": svt, "business_date": d.isoformat(), "qty": "10", "slip_no": s}, **kw))

print("Temporary access")
r = slip(mgr, OLD, "G0")
check("Only the owner can change entries older" in txt(r) or "locked" in txt(r), "before: manager blocked on an old, locked (old-system) day")
r = owner.post("/settings/access", {"action": "create", "user_id": mid, "preset_old_days": "1", "date_from": OLD.isoformat(),
                                    "date_to": OLD.isoformat(), "valid_hours": "48", "open_locked": "1", "reason": "fix imported SVT slips"})
check("can now work on" in txt(r) and sql("SELECT status FROM pump.days WHERE business_date=%s", (OLD,))[0][0] == "open",
      "owner gives 2-day access for that date → locked old-system day opened")
h = txt(mgr.get("/dashboard"))
check("Temporary access from the owner" in h, "manager sees a banner with dates and end time")
r = slip(mgr, OLD, "G1")
check("saved" in txt(r) and sql("SELECT COUNT(*) FROM pump.party_txns WHERE slip_no='G1'")[0][0] == 1, "manager corrects the old day within the access")
r = slip(mgr, OUT, "G2")
check(sql("SELECT COUNT(*) FROM pump.party_txns WHERE slip_no='G2'")[0][0] == 0, "a date outside the access stays blocked")
h = txt(mgr.get(f"/day/{OLD.isoformat()}"))
check('name="closing_' in h and "disabled" not in h.split('name="closing_')[1][:80], "Daily closing of that old day is editable for the manager")
gid = sql("SELECT id FROM pump.access_grants ORDER BY id DESC LIMIT 1")[0][0]
owner.post("/settings/access", {"action": "revoke", "id": gid})
check(sql("SELECT status FROM pump.days WHERE business_date=%s", (OLD,))[0][0] == "locked", "owner stops the access → the day locks again at once")
r = slip(mgr, OLD, "G3")
check(sql("SELECT COUNT(*) FROM pump.party_txns WHERE slip_no='G3'")[0][0] == 0, "after stopping, the manager is blocked again")
owner.post("/settings/access", {"action": "create", "user_id": mid, "preset_old_days": "1", "date_from": OLD.isoformat(),
                                "date_to": OLD.isoformat(), "valid_hours": "24", "open_locked": "1", "reason": "short"})
sql("UPDATE pump.access_grants SET valid_until = NOW() - interval '1 minute' WHERE revoked_at IS NULL")
owner.get("/settings/access")
check(sql("SELECT status FROM pump.days WHERE business_date=%s", (OLD,))[0][0] == "locked", "when the time runs out, the day locks again automatically")
check(sql("SELECT COUNT(*) FROM pump.audit_log WHERE entity='access_grant'")[0][0] >= 4, "giving, stopping and ending are all in the audit log")
r = owner.post("/settings/access", {"action": "create", "user_id": mid, "preset_att_photo": "1", "date_from": (T - timedelta(days=5)).isoformat(),
                                    "date_to": (T - timedelta(days=5)).isoformat(), "valid_hours": "72", "reason": "attendance catch-up"})
sid = sql("SELECT id FROM pump.staff WHERE active ORDER BY id LIMIT 1")[0][0]
ph = U("mgr2", "Mgr@123456x", ua="Mozilla/5.0 (Linux; Android 14) Chrome/129 Mobile Safari/537.36")
with ph.c.session_transaction() as x: x["_csrf"] = "t"
r = ph.c.post("/staff/attendance/start", data={"_csrf": "t", "staff_id": sid, "work_date": (T - timedelta(days=5)).isoformat()}, headers=ph.h)
check("previous" not in (r.json or {}).get("error", "") and "between" not in (r.json or {}).get("error", ""),
      f"attendance access lets the manager mark 5 days back (rule check passed: {r.json.get('error')})")
check(mgr.get("/settings/access").status_code == 403, "only the owner opens Temporary access")

print("Whole-rupee credit slips")
owner.post("/settings/rates", {"effective_from": T.isoformat(), "rate_HSD": "90.37"})
slip(owner, T, "R1", qty="100.5")
slip(owner, T, "R2", qty="", amount="9082.5")
slip(owner, T, "R3", qty="10", cash_taken="499.6")
rows = dict(sql("SELECT slip_no, debit FROM pump.party_txns WHERE slip_no IN ('R1','R2','R3') AND NOT voided"))
check(rows.get("R1") == Decimal("9082.00"), f"100.5 L × 90.37 = 9,082.19 → saved ₹9,082 ({rows.get('R1')})")
check(rows.get("R2") == Decimal("9083.00"), f"amount 9,082.50 → ₹9,083 ({rows.get('R2')})")
check(rows.get("R3") == Decimal("1404.00"), f"10 L (₹904) + cash 499.60 (₹500) → ₹1,404 ({rows.get('R3')})")
h = txt(owner.get(f"/transport?d={T.isoformat()}"))
check("₹9,082<" in h and "₹9,082.00" not in h, "Credit transport page shows whole rupees")
pdf = owner.get(f"/transport/download?party={svt}&period=day&d={T.isoformat()}&fmt=pdf").data
open("/tmp/rbill.pdf", "wb").write(pdf)
t = subprocess.run(["pdftotext", "-layout", "/tmp/rbill.pdf", "-"], capture_output=True, text=True).stdout
check("9,082" in t and "9,082.00" not in t and "100.50" in t and "90.37" in t, "bill: whole-rupee amounts; litres and rate keep decimals")
print(); print("FAILED:" if FAILS else "ALL V3.1 CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
