"""The automatic adjustment from Daily Closing + WhatsApp change-by-row (owner's real workbook, real 2 Nov petrol readings)."""
import os, sys, io, json, hmac, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "supabase" in os.environ["DATABASE_URL"]:
    sys.exit("Refusing to run tests against a real database.")
OWNER = "919999900001"
os.environ.update(SKIP_SCHEMA="1", WA_TOKEN="tok", WA_PHONE_ID="555", WA_VERIFY_TOKEN="v", WA_APP_SECRET="appsecret", WA_OWNERS=f"{OWNER}:boss", CRON_SECRET="c",
                  GITHUB_TOKEN="ghp_test", GITHUB_REPO="o/r")
from datetime import date
from decimal import Decimal as D
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
        t = json.get("type"); SENT.append(json["text"]["body"] if t == "text" else (t.upper() + ":" + str((json.get(t) or {}).get("caption", "")) if t != "interactive" else "LIST:" + json["interactive"]["body"]["text"] + " || " + " | ".join(r["title"] for sec in json["interactive"]["action"]["sections"] for r in sec["rows"])))
        return R(200, {"messages": [{"id": "x"}]})
    return R(200, {"id": "M"})
wa.requests.post = fake_post
import sfm.tally_jobs as tj
DISPATCH = []
_fp = fake_post
def _route(url, json=None, **k):
    if "api.github.com" in url:
        DISPATCH.append(json); return R(204, {})
    return _fp(url, json=json, **k)
wa.requests.post = _route
from sfm import create_app
cl = create_app().test_client()
k = psycopg2.connect(os.environ["DATABASE_URL"]); k.autocommit = True; c = k.cursor()
Dt = date(2026, 11, 2)
# --- Daily Closing of 2 Nov on the website
c.execute("INSERT INTO pump.days (business_date, counted_cash) VALUES (%s, NULL) ON CONFLICT (business_date) DO NOTHING", (Dt,))
for code, rate in (("MS", "110.32"), ("HSD", "101.98")):
    c.execute("INSERT INTO pump.fuel_rates (fuel_code, rate, effective_from) VALUES (%s,%s,'2026-11-01') ON CONFLICT DO NOTHING", (code, rate))
c.execute("SELECT id, name, fuel_code FROM pump.nozzles WHERE active ORDER BY fuel_code, sort, id"); noz = c.fetchall()
reading = {"MS": (D("690991.33"), D("691082.31"), D(0)), "HSD": [(D("5700007.85"), D("5704500.00"), D(5)), (D("4538209.12"), D("4541000.00"), D(5))]}
hsd_i = 0
for nid, name, fuel in noz:
    if fuel == "MS": o, cl_, t = reading["MS"]
    else: o, cl_, t = reading["HSD"][hsd_i]; hsd_i += 1
    c.execute("INSERT INTO pump.nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (business_date, nozzle_id) DO UPDATE SET opening=EXCLUDED.opening, closing=EXCLUDED.closing, testing=EXCLUDED.testing, rate=EXCLUDED.rate",
              (Dt, nid, o, cl_, t, "110.32" if fuel == "MS" else "101.98"))
c.execute("SELECT id FROM pump.parties WHERE name='SVT Logistics'"); svt = c.fetchone()[0]
c.execute("INSERT INTO pump.day_credit (business_date, party_id, amount) VALUES (%s,%s,400000) ON CONFLICT (business_date, party_id) DO UPDATE SET amount=400000", (Dt, svt))
c.execute("INSERT INTO pump.party_txns (party_id, business_date, kind, credit, pay_mode, receipt_no) VALUES (%s,%s,'payment',300000,'HPCL OTP','R-AS1')", (svt, Dt))
c.execute("SELECT id, name FROM pump.collection_modes ORDER BY id"); modes = c.fetchall()
amounts = [D("15000"), D("8000"), D("320000")]            # PhonePe, card, OTP (300,000 of it = SVT's payment)
for (mid, mname), amt in zip(modes, amounts):
    c.execute("INSERT INTO pump.day_collections (business_date, mode_id, amount) VALUES (%s,%s,%s) ON CONFLICT (business_date, mode_id) DO UPDATE SET amount=EXCLUDED.amount", (Dt, mid, amt))
ledger = dict(zip([m[1] for m in modes], ["Phone Pe", "Paytm", "DTPLUS HPCL"]))
c.execute("DELETE FROM pump.tally_names")
hmap = iter(["02 NOZZLE DIESEL DU 1", "04 NOZZLE DIESEL DU2"])
for nid, name, fuel in noz:
    c.execute("INSERT INTO pump.tally_names VALUES ('meter',%s,%s)", (name, "01 NOZZLE PETROL DU1" if fuel == "MS" else next(hmap)))
for site, t in list(ledger.items()) + [("HPCL OTP", "DTPLUS HPCL"), ("Cash", "Cash")]:
    c.execute("INSERT INTO pump.tally_names VALUES ('mode',%s,%s) ON CONFLICT DO NOTHING", (site, t))
c.execute("SELECT name FROM pump.parties"); [c.execute("INSERT INTO pump.tally_names VALUES ('party',%s,%s) ON CONFLICT DO NOTHING", (n, "SVT LOGISTICS" if n == "SVT Logistics" else "SRC TRANSPORT")) for (n,) in c.fetchall()]
c.execute("SELECT DISTINCT COALESCE(description, category) FROM pump.expenses WHERE business_date=%s", (Dt,)); [c.execute("INSERT INTO pump.tally_names VALUES ('expense',%s,'Misc Exp') ON CONFLICT DO NOTHING", r) for r in c.fetchall()]
c.execute("SELECT name FROM pump.lube_products"); [c.execute("INSERT INTO pump.tally_names VALUES ('item',%s,'HP RACER 2 40 ML') ON CONFLICT DO NOTHING", r) for r in c.fetchall()]
c.execute("SELECT DISTINCT pay_mode FROM pump.party_txns WHERE business_date=%s", (Dt,)); [c.execute("INSERT INTO pump.tally_names VALUES ('mode',%s,'Cash') ON CONFLICT DO NOTHING", r) for r in c.fetchall()]
n = {"i": 0}
def say(t, tap=None):
    n["i"] += 1
    msg = {"from": OWNER, "id": f"as{n['i']}"}
    msg.update({"type": "interactive", "interactive": {"type": "list_reply", "list_reply": {"id": tap, "title": tap}}} if tap else {"type": "text", "text": {"body": t}})
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}).encode()
    b0 = len(SENT); cl.post("/api/wa/webhook", data=body, headers={"X-Hub-Signature-256": "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest(), "Content-Type": "application/json"})
    return SENT[b0:]
def book():
    c.execute("SELECT data FROM pump.tally_books ORDER BY id DESC LIMIT 1"); return bytes(c.fetchone()[0])
print("Daily autopilot")
out = say("close 2/11")
check(out and out[0].startswith("IMAGE:") and "added and adjusted" in out[0] and "Reply *OK*" in out[0], f"“close 2/11” → added, adjusted, photo, asks OK: {out[0][:110] if out else ''}")
c.execute("SELECT COUNT(*) FROM pump.tally_books"); n1 = c.fetchone()[0]
out = say("close 2/11")
c.execute("SELECT COUNT(*) FROM pump.tally_books"); n2 = c.fetchone()[0]
check(out and out[0].startswith("IMAGE:") and "added" not in out[0] and n1 == n2, "sending it again only shows the day (nothing added twice)")
out = say("ok")
check(DISPATCH and out and "Tally job" in out[0] and "02/11" in out[0], f"OK → Tally import robot started for 2 Nov: {out[0][:70] if out else ''}")
out = say("close 1/10")
check(out and "Daily Closing is not saved" in out[0], "a day without Daily Closing → clear message")
out = say("ok"); check(out and "Nothing is waiting" in out[0], "a stray OK later does nothing")
print(); print("FAILED:" if FAILS else "ALL AUTOPILOT CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
