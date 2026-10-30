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


def _key(job="scan"):
    if job == "message":
        return os.environ.get("GEMINI_API_KEY_MESSAGE") or os.environ.get("GEMINI_API_KEY", "")
    return os.environ.get("GEMINI_API_KEY", "")


def gemini_ready(job="scan"):
    return bool(_key(job))


MESSAGE_MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash"]


def _models(job="scan"):
    env = "GEMINI_MODEL_MESSAGE" if job == "message" else "GEMINI_MODEL"
    own = [m.strip() for m in os.environ.get(env, "").split(",") if m.strip()]
    base = MESSAGE_MODELS if job == "message" else DEFAULT_MODELS
    return own + [m for m in base if m not in own]


def _thinking(model):
    """Switch Gemini's 'thinking' off/minimal — reading a page needs no reasoning, and it costs seconds."""
    if model.startswith("gemini-2.5"):
        return {"thinkingBudget": 0} if "lite" in model or "flash" in model else None
    if model.startswith("gemini-3"):
        return {"thinkingLevel": "minimal" if "lite" in model else "low"}
    return None


class _Busy(Exception):
    pass


_AVAILABLE = {}     # key → (time, set of model names)


def _available(job):
    """Model names this key can use for generateContent (one quick call, remembered for an hour)."""
    import time
    key = _key(job)
    hit = _AVAILABLE.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        r = requests.get(GEMINI_URL, headers={"x-goog-api-key": key}, params={"pageSize": 200}, timeout=6)
        if r.status_code == 200:
            names = {m.get("name", "").replace("models/", "") for m in r.json().get("models", [])
                     if "generateContent" in (m.get("supportedGenerationMethods") or [])}
            if names:
                _AVAILABLE[key] = (time.time(), names)
                return names
    except Exception:
        pass
    return None


BUDGET = 45        # seconds; Vercel stops a request at 60 s


def _call(model, parts, schema=True, job="scan", think=True, timeout=None):
    """One generateContent call. Returns parsed JSON, or raises _Busy (try another model) / UserError."""
    key = _key(job)
    cfg = {"temperature": 0, "responseMimeType": "application/json"}
    if schema:
        cfg["responseSchema"] = schema if isinstance(schema, dict) else (MESSAGE_SCHEMA if job == "message" else SCHEMA)
    t = _thinking(model) if think else None
    if t:
        cfg["thinkingConfig"] = t
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}
    try:
        r = requests.post(f"{GEMINI_URL}/{model}:generateContent", json=body,
                          timeout=timeout or (25 if job == "message" else 40),
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
        raise UserError(f"Google refused the key ({'GEMINI_API_KEY_MESSAGE' if job == 'message' and os.environ.get('GEMINI_API_KEY_MESSAGE') else 'GEMINI_API_KEY'}). "
                        "Copy it again from aistudio.google.com/apikey.")
    if r.status_code == 400 and think and "thinking" in low:
        return _call(model, parts, schema=schema, job=job, think=False, timeout=timeout)    # no thinking setting
    if r.status_code == 400 and schema and ("schema" in low or "response" in low or "field" in low):
        return _call(model, parts, schema=False, job=job, think=think, timeout=timeout)    # plain JSON instead
    if r.status_code in (404, 429, 500, 503, 504) or "not found" in low or "not supported" in low:
        raise _Busy(f"{r.status_code} {msg[:80]}")
    raise UserError(f"The page reader refused the photo ({r.status_code}): {msg[:160]}")


def _ask(parts, job="scan", started=None, schema=True):
    """Try each free model this key has; a busy / limited / retired model falls through to the next one.
    Stops in time (BUDGET) so the request always gets a proper answer before Vercel's 60 s limit."""
    import time
    if not gemini_ready(job):
        raise UserError("Page scanning is not set up yet. The owner must add GEMINI_API_KEY in Vercel (see the setup guide).")
    started = started or time.time()
    have = _available(job)
    models = [m for m in _models(job) if not have or m in have] or _models(job)
    last = ""
    for attempt, model in enumerate(models):
        left = BUDGET - (time.time() - started)
        if left < 6:
            break
        try:
            out = _call(model, parts, schema=schema, job=job, timeout=min(25 if job == "message" else 40, left - 2))
            out["_model"] = model
            return out
        except _Busy as e:
            last = str(e)
            if last.startswith("429") and attempt == 0 and BUDGET - (time.time() - started) > 15:
                time.sleep(3)        # free tier: short per-minute limit — wait a moment, then the next model
            continue
    if last.startswith("429"):
        raise UserError("The free reader has reached its limit for now. Wait a minute and press Retry.")
    if last == "network" or time.time() - started >= BUDGET - 6:
        raise UserError("Google took too long to answer. Press Retry.")
    raise UserError("The reader is not answering right now. Press Retry in a minute.")


def read_page(image_bytes, mime="image/jpeg", job="scan"):
    """Photo → transcription. If the page's own totals do not add up, a second, focused look is taken
    at the doubtful lines, and the reading whose totals match is kept."""
    img = {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode()}}
    first = _ask([img, {"text": PROMPT}], job)
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
        second = _ask([img, {"text": PROMPT + "\n" + hint}], job)
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


def test_connection(job="scan"):
    """Owner's “Test page reader”: which free models this key can use, and a tiny live call."""
    import time
    if not gemini_ready(job):
        return {"ok": False, "error": "The key is not set in Vercel."}
    key = _key(job)
    out = {"ok": False, "models": [], "chosen": None, "ms": None, "error": None}
    try:
        r = requests.get(GEMINI_URL, headers={"x-goog-api-key": key}, params={"pageSize": 200}, timeout=20)
        if r.status_code == 200:
            names = {m.get("name", "").replace("models/", "") for m in r.json().get("models", [])
                     if "generateContent" in (m.get("supportedGenerationMethods") or [])}
            out["models"] = [m for m in _models(job) if m in names]
        elif r.status_code in (400, 403):
            out["error"] = "Google refused the key. Copy it again from aistudio.google.com/apikey."
            return out
    except requests.RequestException:
        out["error"] = "Could not reach Google from the server."
        return out
    for model in (out["models"] or _models(job)):
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


# ------------------------------------------------------------------ bill message (date + total of each bill)
MESSAGE_PROMPT = """Photo of a handwritten credit bill from an Indian fuel station. Read ONLY these, exactly as written:
- party: the name after "To M/s" (e.g. "S.V.P")
- bill_no: the printed "Bill No." at the top right
- entry_dates: the dates written in the "Date" COLUMN of the table rows, top to bottom (the first row usually
  has the date, the rows below have ditto marks — write those as ''). This is NOT the date at the top of the bill.
- top_date: the date at the top of the bill next to "Date" (only for reference)
- total_amount: the number on the TOTAL line at the bottom of the Amount column
- amounts: every number in the Amount column, top to bottom (not the TOTAL)
Digits only, no commas. Return JSON only."""

MESSAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"party": {"type": "STRING"}, "bill_no": {"type": "STRING"},
                   "entry_dates": {"type": "ARRAY", "items": {"type": "STRING"}}, "top_date": {"type": "STRING"},
                   "total_amount": {"type": "STRING"}, "amounts": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["entry_dates", "total_amount"],
}


def read_bill_total(image_bytes, mime="image/jpeg"):
    """Fast reader for Bill Message: one short answer per bill; a second look only if the TOTAL does not match
    the sum of the amounts on the bill (and there is time left)."""
    import time
    started = time.time()
    img = {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode()}}
    first = _ask([img, {"text": MESSAGE_PROMPT}], "message", started)
    s1 = bill_summary(first)
    if s1["ok"] or not first.get("amounts") or time.time() - started > BUDGET - 20:
        first["_pass"] = 1
        return first
    hint = (f"\nFirst reading: TOTAL {first.get('total_amount')} but the amounts add up to {s1['rows_sum']}. "
            "Look again carefully at the Amount column and the TOTAL line, digit by digit.")
    try:
        second = _ask([img, {"text": MESSAGE_PROMPT + hint}], "message", started)
    except UserError:
        first["_pass"] = 1
        return first
    second["_pass"] = 2
    return second if bill_summary(second)["ok"] else first


def bill_summary(raw):
    """One bill page → {label DDMMYY, date, total, rows_sum, ok}. The page TOTAL is used; it is checked against
    the sum of the row amounts (fuel + cash lines)."""
    if "amounts" in raw and not raw.get("rows"):          # fast reader: entry date, TOTAL and the amount column
        written = [parse_date(x) for x in (raw.get("entry_dates") or []) if x and not is_ditto(x)]
        written = [x for x in written if x]
        # the ENTRY date (Date column of the rows) — never the date at the top; several dates → the latest one
        d = max(written) if written else None
        top = parse_date(raw.get("top_date") or raw.get("bill_date"))
        if d is None:
            d = top
        rows_sum = sum((num(a) or 0 for a in raw.get("amounts") or []), Decimal(0))
        tot = num(raw.get("total_amount"))
        total = tot if tot is not None else rows_sum
        return {"date": d, "label": d.strftime("%d%m%y") if d else (raw.get("bill_no") or ""), "bill_no": raw.get("bill_no") or "",
                "total": rupee(total or 0), "rows_sum": rupee(rows_sum or 0),
                "ok": tot is not None and bool(raw.get("amounts")) and abs(tot - rows_sum) <= 1, "party": raw.get("party") or "",
                "top_date": top, "several_dates": len(set(written)) > 1, "no_entry_date": not written}
    entries, checks, _ = build_entries(raw)
    d = max((e["date"] for e in entries if e["date"]), default=None) or parse_date(raw.get("bill_date"))
    rows_sum = checks["sum_amount"]
    total = checks["total_amount"] if checks["total_amount"] is not None else rows_sum
    return {"date": d, "label": d.strftime("%d%m%y") if d else (raw.get("bill_no") or ""), "bill_no": raw.get("bill_no") or "",
            "total": rupee(total or 0), "rows_sum": rupee(rows_sum or 0),
            "ok": checks["total_amount"] is not None and abs((total or 0) - rows_sum) <= 1,
            "party": raw.get("party") or ""}


def indian(n):
    """54251 → '54,251'; 436978 → '4,36,978'."""
    n = int(rupee(n))
    s, neg = str(abs(n)), n < 0
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + s


def bill_message(party_name, bills, aligned=False):
    """The message exactly as the owner sends it."""
    bills = [b for b in bills if b.get("use", True)]
    if not bills:
        return ""
    upto = max((b["date"] for b in bills if b.get("date")), default=None)
    total = sum((Decimal(b["total"]) for b in bills), Decimal(0))
    lines = [party_name, f"Bill upto {upto:%d/%m/%Y}" if upto else "Bill upto", ""]
    if aligned:
        w = max(len(b["label"]) for b in bills)
        aw = max(len(indian(b["total"])) for b in bills + [{"total": total}])
        for b in bills:
            lines.append(f"Bill {b['label']:<{w}} = {indian(b['total']):>{aw}}")
        lines.append(" " * (w + 8) + "-" * (aw + 2))
        lines.append(f"{'TOTAL':<{w + 5}} = {indian(total):>{aw}}")
        return "```\n" + "\n".join(lines) + "\n```"
    for b in bills:
        lines.append(f"Bill {b['label']} = {indian(b['total'])}")
    lines.append("--------------------")
    lines.append(f"TOTAL = {indian(total)}")
    return "\n".join(lines)
