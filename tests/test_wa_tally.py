"""Stage 2: the workbook on WhatsApp (owner) — table photo, add day, change rows, daily check, cash, XML, undo."""
import os, sys, json, hmac, hashlib, io, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "supabase" in os.environ["DATABASE_URL"]:
    sys.exit("Refusing to run tests against a real database.")
OWNER, MGR = "919999900001", "919999900002"
os.environ.update(SKIP_SCHEMA="1", WA_TOKEN="tok", WA_PHONE_ID="555", WA_VERIFY_TOKEN="v", WA_APP_SECRET="appsecret",
                  WA_OWNERS=f"{OWNER}:boss,{MGR}:mgr2", CRON_SECRET="c", WA_INLINE_WORK="1")
from datetime import date
import psycopg2, openpyxl
import sfm.wa as wa
FAILS = []
def check(ok, msg):
    print(("  ok  " if ok else "  FAIL ") + msg); FAILS.append(msg) if not ok else None
SENT, IMAGES = [], []
class R:
    def __init__(s, code, body): s.status_code, s._b, s.text = code, body, json.dumps(body)
    def json(s): return s._b
def fake_post(url, json=None, headers=None, timeout=None, files=None, data=None, **k):
    if url.endswith("/media"):
        IMAGES.append(files["file"]); return R(200, {"id": f"M{len(IMAGES)}"})
    if url.endswith("/messages"):
        if json.get("status") == "read": return R(200, {})
        t = json.get("type")
        body = json["text"]["body"] if t == "text" else (f"IMAGE:{json['image'].get('caption', '')}" if t == "image" else f"DOC:{json['document']['filename']}")
        SENT.append((json["to"], body)); return R(200, {"messages": [{"id": "x"}]})
    return R(200, {})
wa.requests.post = fake_post
from sfm import create_app
app = create_app(); cl = app.test_client()
k = psycopg2.connect(os.environ["DATABASE_URL"]); k.autocommit = True; c = k.cursor()
n = {"i": 0}
def send(phone, text):
    n["i"] += 1
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": phone, "id": f"wt{n['i']}", "type": "text", "text": {"body": text}}]}}]}]}).encode()
    b0 = len(SENT)
    cl.post("/api/wa/webhook", data=body, headers={"X-Hub-Signature-256": "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest(), "Content-Type": "application/json"})
    return [t for _, t in SENT[b0:]]
D = date(2026, 11, 1)
print("Table photo")
out = send(OWNER, "excel 1/11")
check(out and out[0].startswith("IMAGE:") and IMAGES and IMAGES[-1][2] == "image/png", f"“excel 1/11” → a table photo: {out[0][:70] if out else ''}")
from PIL import Image
im = Image.open(io.BytesIO(IMAGES[-1][1])); check(im.size[0] == 1180 and im.size[1] > 900, f"picture {im.size} — every sheet of 1 Nov")
open("/tmp/wa_table.png", "wb").write(IMAGES[-1][1])
print("Cash and daily check")
c.execute("INSERT INTO pump.days (business_date, counted_cash) VALUES (%s, 6282) ON CONFLICT (business_date) DO UPDATE SET counted_cash=6282", (D,))
out = send(OWNER, "cash 1/11")
check(out and "6,282" in out[0] and "Matches" in out[0], f"“cash 1/11” → workbook ₹6,282 = counted ₹6,282 ✓: {out[0][:90] if out else ''}")
out = send(OWNER, "daily check 1/11"); check(out and out[0].startswith("IMAGE:") and "6,282" in out[0], "“daily check 1/11” → check table photo with final cash")
print("Change a row (asks YES)")
out = send(OWNER, "excel cng 1/11 cash 11000")
check(out and "CNG SALES" in out[0] and "YES" in out[0], f"“excel cng 1/11 cash 11000” asks YES: {out[0][:80] if out else ''}")
out = send(OWNER, "yes"); check(out and "changed row" in out[0], f"YES → {out[0][:80] if out else ''}")
out = send(OWNER, "cash 1/11"); check(out and "5,648" in out[0] and "Difference" in out[0], f"cash now ₹5,648 vs counted 6,282 → difference shown: {out[0][:100] if out else ''}")
send(OWNER, "excel undo"); out = send(OWNER, "yes"); check(out and "Back to" in out[0], "“excel undo” → previous version")
out = send(OWNER, "cash 1/11"); check(out and "6,282" in out[0] and "Matches" in out[0], "after undo: cash matches again")
print("XML")
out = send(OWNER, "xml 1/11 to 1/11")
check(any(t.startswith("DOC:SFM_Tally_Import_01-11-2026_to_01-11-2026.xml") for t in out) and any("25 vouchers" in t for t in out),
      f"“xml 1/11 to 1/11” → your converter’s XML file (25 vouchers): {[t[:60] for t in out]}")
print("Safety")
out = send(MGR, "excel 1/11"); check(not any(t.startswith("IMAGE:") for t in out), "a manager cannot use workbook commands")
out = send(OWNER, "cash svt OD02CL2129 2000"); check(out and "Cash" in out[0] or "slip" in (out[0] if out else "").lower() or "YES" in (out[0] if out else ""), f"“cash svt … 2000” still a cash slip: {out[0][:60] if out else ''}")
print(); print("FAILED:" if FAILS else "ALL WHATSAPP-TALLY CHECKS PASSED", len(FAILS) or ""); [print(" -", f) for f in FAILS]
