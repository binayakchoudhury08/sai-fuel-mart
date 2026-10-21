"""ATG bookmarklet compatibility test (old HP Smart Connect payload)."""
import os, sys, uuid
from datetime import timedelta
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2
DSN = os.environ["DATABASE_URL"]
if "supabase" in DSN or "render" in DSN:
    sys.exit("Refusing to run tests against a real database.")
c = psycopg2.connect(DSN); c.autocommit = True; c.cursor().execute("DROP SCHEMA IF EXISTS pump CASCADE"); c.close()
os.environ["SETUP_CODE"] = "x"; os.environ.pop("TANK_SYNC_SECRET", None)
from sfm import create_app
from sfm.util import today
app = create_app(); T = today(); FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
def sql(q, p=None):
    cc = psycopg2.connect(DSN); cu = cc.cursor(); cu.execute(q, p); r = cu.fetchall() if cu.description else None; cc.commit(); cc.close(); return r
cl = app.test_client()
def post(url, data):
    with cl.session_transaction() as s: s["_csrf"] = "t"
    d = dict(data); d["_csrf"] = "t"; d["_ft"] = uuid.uuid4().hex
    return cl.post(url, data=d, follow_redirects=True)
post("/setup", {"setup_code": "x", "username": "boss", "password": "Pump@2026x", "password2": "Pump@2026x"})
post("/settings/rates", {"effective_from": (T - timedelta(days=5)).isoformat(), "rate_MS": "102.46", "rate_HSD": "93.72"})
post("/settings/tank", {"name": "MS tank", "fuel_code": "MS", "capacity_l": "0"})
post("/settings/tank", {"name": "HSD tank", "fuel_code": "HSD", "capacity_l": "35000"})
tk = dict(sql("SELECT fuel_code, id FROM pump.tanks"))
post("/settings/nozzle", {"name": "MS-1", "fuel_code": "MS", "tank_id": tk["MS"]})
post("/settings/nozzle", {"name": "HSD-1", "fuel_code": "HSD", "tank_id": tk["HSD"]})
nz = dict(sql("SELECT name, id FROM pump.nozzles"))
api = app.test_client()
old_payload = {"secret": "saifuelmart-tank-4021", "tanks": [
    {"fuel_type": "MS", "net_volume": 8000.0, "today_receipt_ltr": 0, "today_sale_ltr": 500, "capacity": 22000,
     "water_ltr": 0, "temperature_c": 31.2, "dip_mm": 1450, "gross_volume": 8020, "water_dip_mm": 0, "ullage_ltr": 14000,
     "decantation_status": "Idle", "density_status": "OK", "density_at_15": 745.1, "density_kg_m3": 741.9, "den_float_height": 0},
    {"fuel_type": "HSD", "net_volume": "12,345.60", "today_receipt_ltr": "4000", "today_sale_ltr": "1000",
     "capacity": "35000", "water_ltr": "15", "temperature_c": "30.5", "dip_mm": "1600", "density_kg_m3": "830.2"}]}

print("Connection & security (same as old app)")
r = api.open("/api/tank-sync", method="OPTIONS")
check(r.status_code == 200 and r.headers.get("Access-Control-Allow-Origin") == "*", "CORS preflight answered")
r = api.post("/api/tank-sync", json=old_payload)
check(r.status_code == 403, "refused while TANK_SYNC_SECRET is not set on the server")
os.environ["TANK_SYNC_SECRET"] = "saifuelmart-tank-4021"
r = api.post("/api/tank-sync", json=dict(old_payload, secret="wrong"))
check(r.status_code == 403 and r.headers.get("Access-Control-Allow-Origin") == "*", "wrong secret refused (with CORS header so the bookmarklet can show it)")
r = api.post("/api/tank-sync", data=__import__("json").dumps(old_payload), content_type="text/plain")
check(r.status_code == 200 and r.json["status"] == "success" and r.json["synced"] == ["MS", "HSD"],
      f"old payload accepted even when sent as text/plain {r.json}")
check(set(r.json) >= {"status", "synced", "date"}, "response keeps the old keys (status, synced, date)")

print("Stored correctly")
rows = sql("SELECT t.fuel_code, d.volume_l, d.temperature_c, d.density, d.source FROM pump.tank_dips d JOIN pump.tanks t ON t.id=d.tank_id ORDER BY 1")
check(rows[1][1] == Decimal("8000.00") and rows[0][1] == Decimal("12345.60"), f"'12,345.60' stored as 12,345.60, not 0 {rows}")
check(sql("SELECT capacity_l FROM pump.tanks WHERE fuel_code='MS'")[0][0] == Decimal("22000.00"), "tank capacity filled from ATG when it was 0")
check(sql("SELECT capacity_l FROM pump.tanks WHERE fuel_code='HSD'")[0][0] == Decimal("35000.00"), "existing capacity kept")
api.post("/api/tank-sync", json=old_payload)
check(sql("SELECT COUNT(*) FROM pump.tank_dips")[0][0] == 2, "re-sync replaces today's reading (one row per tank per day)")
check(sql("SELECT COUNT(*) FROM pump.audit_log WHERE action='sync'")[0][0] == 2, "one audit line per sync")

print("Gain / shortage (old ATG logic)")
with app.app_context():
    from sfm import db
    from sfm.services import tank_reconciliation
    with db.tx() as cur:
        rec = {r["tank"]["fuel_code"]: r for r in tank_reconciliation(cur, T)}
ms, hsd = rec["MS"], rec["HSD"]
check(ms["mode"] == "atg" and ms["opening"] == Decimal("8500.00"), f"MS opening = 8000 − 0 + 500 = 8,500 (got {ms['opening']})")
check(ms["variance"] == 0 and ms["sale_source"] == "ATG", "before meter readings: balanced, no fake loss (old behaviour)")
check(hsd["opening"] == Decimal("9345.60"), f"HSD opening = 12,345.60 − 4,000 + 1,000 (got {hsd['opening']})")
check(hsd["receipt_mismatch"], "ATG shows 4,000 L received but no tanker receipt entered → flagged")
post("/tanks/receipt", {"business_date": T.isoformat(), "invoice_no": "INV5", "c1_fuel": "HSD", "c1_vol": "4000"})
post(f"/day/{T.isoformat()}/readings", {f"opening_{nz['MS-1']}": "1000", f"closing_{nz['MS-1']}": "1520",
                                        f"opening_{nz['HSD-1']}": "5000", f"closing_{nz['HSD-1']}": "6000"})
with app.app_context():
    with db.tx() as cur:
        rec = {r["tank"]["fuel_code"]: r for r in tank_reconciliation(cur, T)}
ms, hsd = rec["MS"], rec["HSD"]
check(ms["sale_source"] == "meter" and ms["variance"] == Decimal("20.00"),
      f"after readings: book 8,500 − 520 = 7,980, ATG 8,000 → +20 L gain (got {ms['variance']})")
check(ms["value"] == Decimal("2049.20"), f"gain value 20 × ₹102.46 = ₹2,049.20 (got {ms['value']})")
check(not hsd["receipt_mismatch"] and hsd["variance"] == 0, "HSD balanced and mismatch cleared after receipt entered")

print("Screens & live feed")
check(api.get("/api/tank-status").status_code == 401, "live feed needs login")
r = cl.get("/api/tank-status")
check(r.status_code == 200 and len(r.json["tanks"]) == 2 and r.json["tanks"][0]["stock"] is not None, "live feed returns both tanks")
h = cl.get(f"/tanks?d={T.isoformat()}").get_data(as_text=True)
for txt in ["Detailed live readings", "Gain this month", "data-sync", "Density at 15", "745.10", "Stock now"]:
    check(txt in h, f"tank page shows '{txt}'")
check(cl.get("/dashboard").status_code == 200 and "ATG" in cl.get("/dashboard").get_data(as_text=True), "dashboard shows ATG reading time")
check(cl.get("/reports/tanks?format=xlsx").status_code == 200, "tank report Excel")

print("Edge cases")
r = api.post("/api/tank-sync", json={"secret": "saifuelmart-tank-4021", "tanks": [{"fuel_type": "Petrol", "net_volume": "7990"},
                                                                                 {"fuel_type": "MS", "net_volume": "abc"}]})
check(r.json["synced"] == ["MS"] and r.json["skipped"] == ["MS"], f"'Petrol' understood as MS; junk volume skipped, not saved as 0 {r.json}")
check(sql("SELECT volume_l FROM pump.tank_dips d JOIN pump.tanks t ON t.id=d.tank_id WHERE fuel_code='MS'")[0][0] == Decimal("7990.00"), "valid value kept")
post(f"/day/{T.isoformat()}/cash", {"counted_cash": "0"})
post(f"/day/{T.isoformat()}/lock", {})
r = api.post("/api/tank-sync", json=old_payload)
check(r.status_code == 409, "today locked by owner → sync politely refused (409)")
print(); print("FAILED:" if FAILS else "ALL ATG CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
