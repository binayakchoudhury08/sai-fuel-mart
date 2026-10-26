"""Scan a handwritten credit bill page → Credit transport entries (checked by the manager before saving).

1. The page photo is read by Google Gemini (free tier) into a plain transcription (rows exactly as written).
2. Everything else is done HERE, by fixed rules that can be tested: ditto marks, pairing a vehicle line with the
   CASH line under it, cash-only lines, number clean-up, qty × rate = amount, page totals, vehicle list,
   duplicates and the transporter name.
"""
import base64
import difflib
import json
import os
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import requests

from .util import UserError

GEMINI_URL = os.environ.get("GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta/models")
# Free-tier Flash models, best first (Google's pricing page, October 2026). GEMINI_MODEL in Vercel overrides.
DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview", "gemini-2.5-flash"]

PROMPT = """You are reading a photo of a handwritten credit bill page from an Indian fuel station (Sai Fuel Mart).
The printed table has columns: Sl. No. | Date | Credit Bill No. | Particulars | Quantity | Rate | Amount.
Transcribe it EXACTLY as written. Do not calculate, correct or guess missing values.

Rules:
- One object per filled table row, top to bottom. Skip empty rows. Ignore tick marks after the amount.
- "party" is the name after "To M/s" exactly as written (e.g. "S.V.P"). "bill_no" is the printed "Bill No." at the top right.
- Ditto marks (″, ", 11, ,,) → write ''. A dash or blank → "".
- Particulars: vehicle numbers without spaces (e.g. OD02CL2129); write CASH for cash lines.
- Numbers: digits and decimal point only, no commas. Indian vehicle numbers look like OD02CL2129 (2 letters, 2 digits,
  1–3 letters, 4 digits) — the 3rd and 4th characters are digits, never the letter O.
- total_quantity and total_amount come from the TOTAL line at the bottom; amount_in_words from "Rupees".
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "bill_no": {"type": "STRING"}, "bill_date": {"type": "STRING"}, "party": {"type": "STRING"},
        "rows": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "sl": {"type": "STRING"}, "date": {"type": "STRING"}, "credit_bill_no": {"type": "STRING"},
            "particulars": {"type": "STRING"}, "quantity": {"type": "STRING"}, "rate": {"type": "STRING"},
            "amount": {"type": "STRING"}},
            "required": ["particulars", "quantity", "amount"]}},
        "total_quantity": {"type": "STRING"}, "total_amount": {"type": "STRING"}, "amount_in_words": {"type": "STRING"},
    },
    "required": ["bill_no", "party", "rows"],
}


def gemini_ready():
    return bool(os.environ.get("GEMINI_API_KEY"))


def _models():
    own = [m.strip() for m in os.environ.get("GEMINI_MODEL", "").split(",") if m.strip()]
    return own + [m for m in DEFAULT_MODELS if m not in own]


class _Busy(Exception):
    pass


def _call(model, parts, schema=True):
    """One generateContent call. Returns parsed JSON, or raises _Busy (try another model) / UserError."""
    key = os.environ.get("GEMINI_API_KEY", "")
    cfg = {"temperature": 0, "responseMimeType": "application/json"}
    if schema:
        cfg["responseSchema"] = SCHEMA
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}
    try:
        r = requests.post(f"{GEMINI_URL}/{model}:generateContent", json=body, timeout=50,
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    except requests.RequestException:
        raise _Busy("network")
    if r.status_code == 200:
        try:
            j = r.json()
            text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"] if not p.get("thought"))
            text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
            return json.loads(text)
        except Exception:
            raise _Busy("unreadable answer")
    msg = ""
    try:
        msg = r.json().get("error", {}).get("message", "")
    except Exception:
        pass
    low = msg.lower()
    if r.status_code in (400, 403) and ("api key" in low or "api_key" in low or "permission" in low and "key" in low):
        raise UserError("Google refused the key (GEMINI_API_KEY). Copy it again from aistudio.google.com/apikey.")
    if r.status_code == 400 and schema and ("schema" in low or "response" in low or "field" in low):
        return _call(model, parts, schema=False)     # older models: plain JSON answer instead of a schema
    if r.status_code in (404, 429, 500, 503, 504) or "not found" in low or "not supported" in low:
        raise _Busy(f"{r.status_code} {msg[:80]}")
    raise UserError(f"The page reader refused the photo ({r.status_code}): {msg[:160]}")


def _ask(parts):
    """Try each free model; a busy / limited / retired model falls through to the next one."""
    if not gemini_ready():
        raise UserError("Page scanning is not set up yet. The owner must add GEMINI_API_KEY in Vercel (see the setup guide).")
    last = ""
    for attempt, model in enumerate(_models()):
        try:
            out = _call(model, parts)
            out["_model"] = model
            return out
        except _Busy as e:
            last = str(e)
            if last.startswith("429") and attempt == 0:
                import time
                time.sleep(4)        # free tier: short per-minute limit — wait a moment, then the next model
            continue
    if last.startswith("429"):
        raise UserError("The free page reader has reached its limit for now (per minute or per day). "
                        "Wait a minute and try again; the daily limit resets the next day.")
    raise UserError("The page reader is not answering right now. Try again in a minute.")


def read_page(image_bytes, mime="image/jpeg"):
    """Photo → transcription. If the page's own totals do not add up, a second, focused look is taken
    at the doubtful lines, and the reading whose totals match is kept."""
    img = {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode()}}
    first = _ask([img, {"text": PROMPT}])
    entries, checks, notes = build_entries(first)
    bad_rows = [i + 1 for i, r in enumerate(first.get("rows") or []) if _row_mismatch(r, first)]
    if (checks["qty_ok"] and checks["amount_ok"]) and not bad_rows:
        first["_pass"] = 1
        return first
    hint = (f"Your first reading was: {json.dumps({k: v for k, v in first.items() if not k.startswith('_')})}\n"
            f"Problems: {'; '.join(notes) or 'none on totals'}"
            f"{'; rows ' + ', '.join(map(str, bad_rows)) + ' have Quantity × Rate not equal to Amount' if bad_rows else ''}.\n"
            "Look at the photo again very carefully, digit by digit, especially those rows and the TOTAL line, "
            "and return the corrected full transcription in the same format. Still copy exactly what is written.")
    try:
        second = _ask([img, {"text": PROMPT + "\n" + hint}])
    except UserError:
        first["_pass"] = 1
        return first
    e2, c2, n2 = build_entries(second)
    score = lambda c, rows, raw: (c["qty_ok"] + c["amount_ok"]) * 10 - sum(1 for r in rows if _row_mismatch(r, raw))
    if score(c2, second.get("rows") or [], second) > score(checks, first.get("rows") or [], first):
        second["_pass"] = 2
        return second
    first["_pass"] = 2
    return first


def _row_mismatch(row, raw):
    """True when a row's written amount is not quantity × rate (rate may be a ditto of a row above)."""
    q, a = num(row.get("quantity")), num(row.get("amount"))
    if not q or a is None:
        return False
    rate = None
    for r in raw.get("rows") or []:
        if not is_ditto(r.get("rate")) and num(r.get("rate")):
            rate = num(r.get("rate"))
        if r is row:
            break
    return bool(rate) and abs(rupee(q * rate) - a) > 1


def test_connection():
    """Owner's “Test page reader”: which free models this key can use, and a tiny live call."""
    import time
    if not gemini_ready():
        return {"ok": False, "error": "GEMINI_API_KEY is not set in Vercel."}
    key = os.environ.get("GEMINI_API_KEY", "")
    out = {"ok": False, "models": [], "chosen": None, "ms": None, "error": None}
    try:
        r = requests.get(GEMINI_URL, headers={"x-goog-api-key": key}, params={"pageSize": 200}, timeout=20)
        if r.status_code == 200:
            names = {m.get("name", "").replace("models/", "") for m in r.json().get("models", [])
                     if "generateContent" in (m.get("supportedGenerationMethods") or [])}
            out["models"] = [m for m in _models() if m in names]
        elif r.status_code in (400, 403):
            out["error"] = "Google refused the key. Copy it again from aistudio.google.com/apikey."
            return out
    except requests.RequestException:
        out["error"] = "Could not reach Google from the server."
        return out
    for model in (out["models"] or _models()):
        t = time.time()
        try:
            r = requests.post(f"{GEMINI_URL}/{model}:generateContent", timeout=30,
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                              json={"contents": [{"parts": [{"text": "Reply with the single word OK."}]}],
                                    "generationConfig": {"temperature": 0}})
        except requests.RequestException:
            continue
        if r.status_code == 200:
            out.update(ok=True, chosen=model, ms=int((time.time() - t) * 1000))
            return out
        if r.status_code == 429:
            out["error"] = "The key works, but the free limit is used up right now (resets later)."
    out["error"] = out["error"] or "No free model answered. Check the key and try again later."
    return out


# ------------------------------------------------------------------ clean-up helpers
DITTO = re.compile(r"^\s*(''|\"|″|〃|11|,,|ll|II|-?\s*do\s*-?)\s*$", re.I)


def is_ditto(v):
    return bool(v) and bool(DITTO.match(str(v)))


def num(v):
    """'1,000' → 1000 ; 'O' read as zero; '' / '-' → None."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace(" ", "")
    s = s.translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5"}))
    if s in ("", "-", "—", "–", "."):
        return None
    try:
        d = Decimal(s)
        return d if d >= 0 else None
    except Exception:
        return None


def rupee(d):
    return Decimal(d).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def parse_date(v, fallback=None):
    s = str(v or "").strip()
    m = re.match(r"^(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})$", s)
    if not m:
        return fallback
    dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    yy = yy + 2000 if yy < 100 else yy
    try:
        return date(yy, mm, dd)
    except ValueError:
        return fallback


PLATE = re.compile(r"^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{1,4})$")


def vehicle(v):
    """Clean a vehicle number: upper case, no spaces; fix O/0 and I/1 where a plate needs digits."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()
    if len(s) < 6:
        return s
    # state code = letters, district = digits, then series letters, then digits
    head, rest = s[:2], s[2:]
    head = head.replace("0", "O").replace("1", "I")
    dist = rest[:2].replace("O", "0").replace("I", "1").replace("L", "1")
    tail = rest[2:]
    m = re.match(r"^([A-Z0-9]*?)(\d{3,4})$", tail)
    if m:
        series = m.group(1).replace("0", "O").replace("1", "I")
        tail = series + m.group(2)
    return head + dist + tail


def is_cash(particulars):
    return "CASH" in re.sub(r"[^A-Z]", "", str(particulars or "").upper()) or str(particulars or "").strip().upper().startswith("CAS")


# ------------------------------------------------------------------ page → entries
def build_entries(raw, page_date=None):
    """Transcription → list of entries {date, vehicle, qty, rate, fuel_amount, cash, slip_no, lines, notes[]}
    plus page checks. Pure function (no database) so it can be tested exactly."""
    rows = raw.get("rows") or []
    last_date, last_rate = parse_date(raw.get("bill_date")), None
    entries, notes_page = [], []
    sum_qty, sum_amt = Decimal(0), Decimal(0)
    for i, r in enumerate(rows, 1):
        d = last_date if is_ditto(r.get("date")) or not r.get("date") else parse_date(r.get("date"), last_date)
        last_date = d
        rate = last_rate if is_ditto(r.get("rate")) else (num(r.get("rate")) or last_rate)
        if not is_ditto(r.get("rate")) and num(r.get("rate")):
            last_rate = rate
        q = num(r.get("quantity"))
        amt = num(r.get("amount"))
        slip = "" if is_ditto(r.get("credit_bill_no")) else str(r.get("credit_bill_no") or "").strip()
        sum_qty += q or 0
        sum_amt += amt or 0
        if is_cash(r.get("particulars")):
            # a CASH line belongs to the vehicle line just above it
            prev = entries[-1] if entries else None
            if prev and prev["cash"] == 0 and prev["qty"]:
                prev["cash"] = amt or Decimal(0)
                prev["lines"].append(i)
            else:
                entries.append({"date": d, "vehicle": prev["vehicle"] if prev else "", "qty": None, "rate": None,
                                "fuel_amount": Decimal(0), "cash": amt or Decimal(0), "slip_no": slip, "lines": [i],
                                "written_amount": None, "notes": ["Cash line without a vehicle above it"] if not prev else []})
            continue
        veh = vehicle(r.get("particulars"))
        e = {"date": d, "vehicle": veh, "qty": q, "rate": rate if q else None, "fuel_amount": Decimal(0), "cash": Decimal(0),
             "slip_no": slip, "lines": [i], "written_amount": amt, "notes": []}
        if q:
            if rate:
                calc = rupee(q * rate)
                e["fuel_amount"] = calc
                if amt is not None and abs(calc - amt) > 1:
                    e["notes"].append(f"Amount written {amt} but {q} × {rate} = {calc}")
            elif amt:
                e["fuel_amount"] = amt
                e["notes"].append("No rate on the page")
        elif amt:
            # vehicle line with no quantity, only an amount → cash only for that vehicle
            e["cash"] = amt
            e["notes"].append("No quantity — taken as cash only")
        else:
            e["notes"].append("No quantity or amount")
        entries.append(e)
    tq, ta = num(raw.get("total_quantity")), num(raw.get("total_amount"))
    checks = {"sum_qty": sum_qty, "sum_amount": sum_amt, "total_qty": tq, "total_amount": ta,
              "qty_ok": tq is None or abs(sum_qty - tq) < Decimal("0.5"),
              "amount_ok": ta is None or abs(sum_amt - ta) <= 1}
    if not checks["qty_ok"]:
        notes_page.append(f"Litres add up to {sum_qty} but the page total says {tq}")
    if not checks["amount_ok"]:
        notes_page.append(f"Amounts add up to {sum_amt} but the page total says {ta}")
    return entries, checks, notes_page


# ------------------------------------------------------------------ matching against the books
def initials(name):
    return "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name or "")).upper()


def match_party(cur, written):
    """Best transporter for the name written on the page: learned alias → exact → initials → closest name."""
    key = re.sub(r"[^A-Z0-9]", "", (written or "").upper())
    if not key:
        return None, []
    cur.execute("SELECT value FROM settings WHERE key=%s", (f"party_alias:{key}",))
    r = cur.fetchone()
    cur.execute("SELECT id, name, vehicles FROM parties WHERE active ORDER BY name")
    parties = cur.fetchall()
    if r:
        for p in parties:
            if str(p["id"]) == r["value"]:
                return p, parties
    scored = []
    for p in parties:
        nm = re.sub(r"[^A-Z0-9]", "", p["name"].upper())
        ini = initials(p["name"])
        first = re.sub(r"[^A-Z0-9]", "", (p["name"].split() or [""])[0].upper())
        s = max(difflib.SequenceMatcher(None, key, nm).ratio(), difflib.SequenceMatcher(None, key, ini).ratio(),
                difflib.SequenceMatcher(None, key, first).ratio(),
                1.0 if nm.startswith(key) or ini.startswith(key) else 0)
        scored.append((s, p))
    scored.sort(key=lambda x: -x[0])
    best = scored[0] if scored else (0, None)
    return (best[1] if best[0] >= 0.6 else None), parties


def known_vehicles(cur, party_id):
    cur.execute("SELECT vehicles FROM parties WHERE id=%s", (party_id,))
    p = cur.fetchone()
    vs = {vehicle(v) for v in re.split(r"[,;\s]+", (p or {}).get("vehicles") or "") if len(v) > 4}
    cur.execute("""SELECT DISTINCT vehicle_no FROM party_txns WHERE party_id=%s AND vehicle_no <> ''
                   AND business_date > CURRENT_DATE - 365""", (party_id,))
    vs |= {vehicle(r["vehicle_no"]) for r in cur.fetchall()}
    return sorted(v for v in vs if v)


def check_against_books(cur, entries, party_id, challan, fuel_code="HSD"):
    """Adds notes: unknown vehicle (+ closest), price differs from the day's price, already entered."""
    from .services import rates_for
    known = known_vehicles(cur, party_id) if party_id else []
    for e in entries:
        e["suggest"] = None
        if e["vehicle"] and known and e["vehicle"] not in known:
            close = difflib.get_close_matches(e["vehicle"], known, n=1, cutoff=0.75)
            if close:
                e["suggest"] = close[0]
                e["notes"].append(f"Vehicle not in the list — did you mean {close[0]}?")
            else:
                e["notes"].append("New vehicle for this transporter")
        if e["qty"] and e["date"]:
            day = rates_for(cur, e["date"]).get(fuel_code)
            if day and e["rate"] and abs(Decimal(day) - e["rate"]) > Decimal("0.005"):
                e["notes"].append(f"Page rate {e['rate']} differs from the saved {fuel_code} price {day}")
        if party_id and e["date"] and e["vehicle"]:
            cur.execute("""SELECT 1 FROM party_txns WHERE party_id=%s AND business_date=%s AND vehicle_no=%s AND NOT voided
                           AND kind IN ('fuel','cash_advance') AND COALESCE(qty,0)=%s LIMIT 1""",
                        (party_id, e["date"], e["vehicle"], e["qty"] or 0))
            if cur.fetchone():
                e["notes"].append("Looks already entered (same date, vehicle and litres)")
    if party_id and challan:
        cur.execute("""SELECT COUNT(*) AS n FROM party_txns WHERE party_id=%s AND challan_no=%s AND NOT voided
                       AND kind IN ('fuel','cash_advance')""", (party_id, challan))
        return cur.fetchone()["n"]
    return 0
