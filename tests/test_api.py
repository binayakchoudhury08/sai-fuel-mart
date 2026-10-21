"""Automation API tests (what n8n calls)."""
import os, sys, uuid, re
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSN = os.environ["DATABASE_URL"]
if "supabase" in DSN or "render" in DSN:
    sys.exit("Refusing to run tests against a real database.")
os.environ["SKIP_SCHEMA"] = "1"
from sfm import create_app
from sfm.util import today
import psycopg2
app = create_app(); T = today(); Y = T - timedelta(days=1); FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
cl = app.test_client()
with cl.session_transaction() as s: s["_csrf"] = "t"
cl.post("/login", data={"_csrf": "t", "username": "boss", "password": "Pump@2026x"})
api = app.test_client()
print("Key management")
check(api.get("/api/v1/ping").status_code == 401, "no key → 401")
with cl.session_transaction() as s: s["_csrf"] = "t"
h = cl.post("/settings/automation", data={"_csrf": "t", "action": "generate"}).get_data(as_text=True)
key = re.search(r'<code class="secret">(sfm_[^<]+)</code>', h).group(1)
check(key.startswith("sfm_") and len(key) > 40, "owner creates a key; shown once")
c = psycopg2.connect(DSN); k = c.cursor()
k.execute("SELECT value FROM pump.settings WHERE key='api_key_hash'"); stored = k.fetchone()[0]
check(stored != key and len(stored) == 64, "only a SHA-256 hash is stored, never the key")
check(key not in cl.get("/settings/automation").get_data(as_text=True), "key not shown again on the page")
H = {"X-API-Key": key}
r = api.get("/api/v1/ping", headers=H)
check(r.status_code == 200 and r.json["ok"] and r.headers.get("Cache-Control") == "no-store", f"ping with key {r.json}")
check(api.get("/api/v1/ping", headers={"X-API-Key": key + "x"}).status_code == 401, "wrong key → 401")
check(api.post("/api/v1/ping", headers=H).status_code == 405, "API is read-only (POST not allowed)")
print("Bill for the 4 PM workflow")
r = api.get("/api/v1/transport/bill-info?party=svt&date=today", headers=H).json
check(r["found"] and r["party"] == "SVT Logistics" and r["entries"] == 3 and r["total"] == "15200.00", f"bill-info by short name {r.get('total')}")
check(len(r["wa_params"]) == 5 and all("\n" not in p for p in r["wa_params"]), f"WhatsApp params ready {r['wa_params']}")
f = api.get(r["files"]["pdf"]["path"], headers=H)
check(f.status_code == 200 and f.data[:4] == b"%PDF", "bill PDF via API")
f = api.get(r["files"]["xlsx"]["path"], headers=H)
check(f.status_code == 200 and f.data[:2] == b"PK" and "Bill" in f.headers.get("Content-Disposition", ""), "bill Excel via API")
check(api.get(r["files"]["pdf"]["path"]).status_code == 401, "bill file needs the key too")
r = api.get(f"/api/v1/transport/bill-info?party=SVT Logistics&date={(T + timedelta(days=0)).isoformat()}", headers=H).json
r2 = api.get("/api/v1/transport/bill-info?party=SVT Logistics&date=" + (T + timedelta(days=5)).isoformat(), headers=H)
r3 = api.get(f"/api/v1/transport/bill-info?party=SVT&date={(T - timedelta(days=0)).isoformat()}", headers=H).json
r4 = api.get(f"/api/v1/transport/bill-info?party=SVT&date={(T + timedelta(days=0)).isoformat()}&fallback=none", headers=H).json
fb = api.get("/api/v1/transport/bill-info?party=Sharma&date=today", headers=H).json
check(fb["found"] and fb["is_fallback"] and fb["date"] == Y.isoformat(), f"no entries today → falls back to latest day {fb.get('date')}")
check(api.get("/api/v1/transport/bill-info?party=Nobody", headers=H).status_code == 404, "unknown transporter → clear 404")
amb = api.get("/api/v1/transport/bill-info?party=s", headers=H)
check(amb.status_code == 404 and "several" in amb.json["error"], "ambiguous name refused, lists matches")
print("12 AM reminder")
r = api.get("/api/v1/closing/overdue", headers=H).json
check(r["ok"] and "count" in r and all("\n" not in p for p in r.get("wa_params", [])), f"overdue check {r['count']} day(s) {r.get('wa_params')}")
print("Summary")
r = api.get("/api/v1/summary/day?date=yesterday", headers=H).json
check(r["ok"] and len(r["wa_params"]) == 8, f"day summary params {r['wa_params']}")
print("Switch off / revoke")
with cl.session_transaction() as s: s["_csrf"] = "t"
cl.post("/settings/automation", data={"_csrf": "t", "action": "disable"})
check(api.get("/api/v1/ping", headers=H).status_code == 401, "switched off → key refused")
with cl.session_transaction() as s: s["_csrf"] = "t"
cl.post("/settings/automation", data={"_csrf": "t", "action": "enable"})
check(api.get("/api/v1/ping", headers=H).status_code == 200, "switched on again")
k.execute("SELECT COUNT(*) FROM pump.audit_log WHERE action='api'"); check(k.fetchone()[0] >= 8, "every API call is in the audit log")
k.execute("SELECT COUNT(*) FROM pump.audit_log WHERE summary LIKE %s", (f"%{key}%",)); check(k.fetchone()[0] == 0, "the key never appears in the audit log")
print(); print("FAILED:" if FAILS else "ALL API CHECKS PASSED", len(FAILS) or ""); [print(" -", x) for x in FAILS]
