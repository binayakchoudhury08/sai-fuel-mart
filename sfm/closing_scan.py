"""Scan a handwritten DAILY CLOSING page (owner's notebook format) → pre-filled Daily Closing, every line checked.

Page layout (27/28/29 Oct 2026 samples): MS and HSD — 3 nozzle columns each (closing / opening / difference), total,
"− 15.00 for testing", net; sale = net × rate; lube lines (qty × rate); CNG qty × rate; COLLECTION; then + payments
received and − digital / credit / expense lines (left column, then right column) with running subtotals; final cash;
dip stock (MS 94.4 = 9328, HSD 196.2 = 29133 − 14 = 29119).

Owner's rules: a − line with a transporter name (either side) = credit given to that transporter; a name not in the
transporters → warning; C.F. / discount and the small minus lines = General expense; driver advance = Misc expense;
expenses may be written anywhere (recognised by name); "Babu car / Babu diesel" = a Credit Transport slip for
Sai Hitech; a line not understood is not saved and is reported with the lines around it; shortage up to ₹10 is fine;
the CNG opening reading is typed by the manager.
"""
import base64
import json
import re
from decimal import Decimal, ROUND_HALF_UP

from .scan import _ask, match_party, num

D1 = Decimal("1")

PROMPT = """Photo of a fuel station's handwritten DAILY CLOSING notebook page. Copy EXACTLY what is written; never calculate.
LAYOUT (always the same):
* Top: "MS =" block — 3 nozzle columns. Row 1 = closing readings, row 2 = opening readings, row 3 = difference. On the far
  right a small column adds the 3 differences (total), then "time − 15.00 for testing", then the net. That small right
  column is NOT a nozzle.
* Then the "HSD =" block — the same: 3 nozzle columns, total, testing, net. Then (if written) the CNG block the same way.
* Then "MS SALE = litres x rate = amount", "HSD SALE = litres x rate = amount", the total of both, then each LUBE line
  (product name, quantity x rate, + amount), the total after lube, "C.N.G = qty x rate + amount", and COLLECTION.
* Below COLLECTION, the LEFT column: transporter / party lines with + (payments received) and − (PhonePe, Card swipe,
  HPCL OTP, HP Pay…), with underlined running totals.
* The RIGHT column (to the right of the sales lines, top to bottom) starts with the carried total (often written
  "C.n" or "C/F" + amount — that is a running total, sign "="), then − lines: transporters (credit given), "EXP" total,
  small expenses (loading, tiffin, ...). Its LAST total is the final cash.
* Bottom right: "MS stock: dip = litres", "HSD stock: dip = litres − 14 = net". These are DIP STOCK, NOT cash.
Numbers: digits and decimal point only, no commas. Unreadable → "". Every line needs sign "+", "-" or "=" (running total).
"""

_N = {"type": "STRING"}
_FUEL = {"type": "OBJECT", "properties": {
    "nozzles": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"closing": _N, "opening": _N, "diff": _N}}},
    "total": _N, "testing": _N, "net": _N}}
_SALE = {"type": "OBJECT", "properties": {"litres": _N, "rate": _N, "amount": _N}}
SCHEMA = {"type": "OBJECT", "properties": {
    "date": _N, "ms": _FUEL, "hsd": _FUEL, "ms_sale": _SALE, "hsd_sale": _SALE, "fuel_total": _N,
    "lube": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"text": _N, "qty": _N, "rate": _N, "amount": _N},
                                         "required": ["text", "qty", "rate", "amount"]}},
    "after_lube": _N, "cng": {"type": "OBJECT", "properties": {"qty": _N, "rate": _N, "amount": _N}}, "collection": _N,
    "cng_meters": _FUEL,
    "left": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"sign": {"type": "STRING", "enum": ["+", "-", "="]}, "name": _N, "amount": _N},
                                         "required": ["sign", "name", "amount"]}},
    "right": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"sign": {"type": "STRING", "enum": ["+", "-", "="]}, "name": _N, "amount": _N},
                                         "required": ["sign", "name", "amount"]}},
    "lines": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"sign": {"type": "STRING", "enum": ["+", "-", "="]}, "name": _N, "amount": _N},
                                         "required": ["sign", "name", "amount"]}},
    "final_cash": _N,
    "dips": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"fuel": _N, "dip": _N, "litres": _N, "adjust": _N, "net": _N}}}},
    "required": ["ms", "hsd", "lines"]}


def read_closing(image_bytes, mime="image/jpeg"):
    img = {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode()}}
    return _ask([img, {"text": PROMPT}], "scan", schema=SCHEMA)


# The page is read in two halves at the same time (each its own request), so neither runs into Vercel's 60 s limit.
PART_KEYS = {"meters": ["date", "ms", "hsd", "cng_meters", "ms_sale", "hsd_sale"],
             "sales": ["fuel_total", "lube", "after_lube", "cng", "collection"],
             "left": ["left"],
             "right": ["right", "final_cash", "dips"]}
PART_LABEL = {"meters": "meter readings", "sales": "lube, CNG & collection", "left": "left column", "right": "right column & cash"}
PART_NOTE = {
    "meters": "Read ONLY the nozzle readings: MS columns, HSD columns and CNG columns (closing row, opening row, difference row), "
              "each block's total / testing / net, and the MS SALE and HSD SALE lines. Nothing else.",
    "sales": "Read ONLY the middle block: the total of MS + HSD sale, EVERY lube line between the HSD SALE line and COLLECTION "
             "(one item per product line, e.g. text 'RACER 2T 20ml', qty '20', rate '14', amount '280'), the total after lube, "
             "the C.N.G line and COLLECTION. Never put transporter, PhonePe or EXP lines into lube.",
    "left": "Read ONLY the LEFT column below COLLECTION, top to bottom: every + and − line (name and amount) and every underlined "
            "running total (sign \"=\"). Return them in 'left'. Do not skip any line. Ignore the right column.",
    "right": "Read ONLY the RIGHT column (beside the sales lines, top to bottom). Its first line is the carried total "
             "(e.g. 'C.n 1637666' → sign '=', name 'C.n'). Then every − line (transporters, EXP total as sign '=' name 'EXP', "
             "small expenses) and every running total. final_cash = the LAST total of this column (NOT the dip stock numbers). "
             "dips = the 'MS stock' / 'HSD stock' lines at the bottom right. Do not skip any line."}


PART_KEY = {"meters": 0, "left": 0, "sales": 1, "right": 1}     # with two keys: half the parts on each


def read_part(image_b64, mime, part, careful=False, key_no=None):
    from . import scan as _scan
    keys = _scan.closing_keys()
    _scan._TL.key = keys[(PART_KEY[part] if key_no is None else key_no) % len(keys)] if keys else None
    try:
        return _read_part(image_b64, mime, part, careful)
    finally:
        _scan._TL.key = None


def _read_part(image_b64, mime, part, careful=False):
    keys = PART_KEYS[part]
    schema = {"type": "OBJECT", "properties": {k: SCHEMA["properties"][k] for k in keys}}
    img = {"inline_data": {"mime_type": mime, "data": image_b64}}
    return _ask([img, {"text": PROMPT + "\n" + PART_NOTE[part]}], "closing" if careful else "closing-careful",
                schema=schema, level="low" if careful else "minimal", budget=55)


def _clean_lube(items):
    good = []
    for l in items or []:
        t = (l.get("text") or "")
        if len(t) > 60 or re.search(r"collection|phone|swipe|otp|exp\b|roadline|roadway|logist", t, re.I):
            continue
        good.append(l)
    return good


def _part_ok(part, got):
    if part == "sales":
        got["lube"] = _clean_lube(got.get("lube"))
    if part == "meters":
        return bool((got.get("ms") or {}).get("nozzles") or (got.get("hsd") or {}).get("nozzles")), "the meter readings"
    if part == "sales":
        return bool(got.get("collection") or got.get("lube") or got.get("fuel_total")), "lube / CNG / collection"
    if part == "left":
        return bool(got.get("left")), "the left column lines"
    return bool(got.get("right") or got.get("final_cash")), "the right column"


def _issues(part, got, need):
    """How many things in this part do not add up (0 = trustworthy). need = fuels the station has (MS/HSD/CNG)."""
    bad = 0
    if part == "meters":
        for code, key in (("MS", "ms"), ("HSD", "hsd"), ("CNG", "cng_meters")):
            cols = ((got.get(key) or {}).get("nozzles")) or []
            if code in need and len(cols) < need[code]:
                bad += 1 + need[code] - len(cols)          # a whole block (e.g. HSD) missing counts heavily
            for c in cols:
                cl, op, df = num(c.get("closing")), num(c.get("opening")), num(c.get("diff"))
                if cl is None or op is None or (df is not None and abs((cl - op) - df) > Decimal("0.02")):
                    bad += 1
    elif part == "sales":
        ft, al = num(got.get("fuel_total")), num(got.get("after_lube"))
        ls = sum((num(l.get("amount")) or 0 for l in got.get("lube") or []), Decimal(0))
        if ft is not None and al is not None and abs((al - ft) - ls) > 1:
            bad += 2
        if num(got.get("collection")) is None:
            bad += 1
    else:
        lines = got.get(part) or []
        run = None
        for l in lines:
            amt = num(l.get("amount"))
            if l.get("sign") == "=":
                if run is not None and amt is not None and abs(amt - run) > 1:
                    bad += 1
                run = amt if amt is not None else run
            elif amt is None or l.get("sign") not in ("+", "-"):
                bad += 1
            elif run is not None:
                run += amt if l["sign"] == "+" else -amt
        if part == "right" and num(got.get("final_cash")) is None:
            bad += 1
    return bad


def merge(parts):
    raw = {}
    for k in ("meters", "sales", "right"):
        raw.update({x: y for x, y in (parts.get(k) or {}).items() if x not in ("left", "right")})
    raw["lines"] = list((parts.get("left") or {}).get("left") or []) + list((parts.get("right") or {}).get("right") or [])
    return raw


def work_part(sid, part, careful=False):
    """Read one part of the page; when all four are in, the scan is ready for review."""
    from . import db
    from .util import UserError
    row = db.one("SELECT img, parts, status FROM closing_scans WHERE id=%s", (sid,))
    if not row or row["status"] not in ("reading", "failed") or part in (row["parts"] or {}):
        return
    try:
        import time as _time
        t0 = _time.time()
        need = {}
        if part == "meters":
            for r_ in db.query("SELECT fuel_code, COUNT(*) AS n FROM nozzles WHERE active GROUP BY fuel_code") or []:
                need[r_["fuel_code"]] = r_["n"]
        best, best_bad, last_err = None, 10 ** 6, None
        for attempt, mode in enumerate((careful, not careful)):        # read; if it does not add up, read again with the other model
            if attempt and (_time.time() - t0 > 24 or best_bad == 0):
                break
            try:
                got = read_part(row["img"], "image/jpeg", part, mode, key_no=PART_KEY[part] + attempt)    # re-read: the other key
            except UserError as e:
                last_err = e
                continue
            got.pop("_model", None)
            if part == "sales":
                got["lube"] = _clean_lube(got.get("lube"))
            bad = _issues(part, got, need)
            if bad < best_bad:
                best, best_bad = got, bad
        if best is None:
            raise last_err or UserError("could not be read — press Retry")
        got = best
        ok, what = _part_ok(part, got)
        if not ok:
            raise UserError(f"{what} could not be read — press Retry")
        with db.tx() as cur:
            cur.execute("UPDATE closing_scans SET parts = parts || %s::jsonb WHERE id=%s", (json.dumps({part: got}), sid))
    except UserError as e:
        with db.tx() as cur:
            cur.execute("UPDATE closing_scans SET error=%s WHERE id=%s", (f"{PART_LABEL[part]}: {e}", sid))
    except Exception as e:
        with db.tx() as cur:
            cur.execute("UPDATE closing_scans SET error=%s WHERE id=%s", (f"{PART_LABEL[part]}: reading failed ({type(e).__name__})", sid))
    with db.tx() as cur:
        cur.execute("SELECT parts, error, status FROM closing_scans WHERE id=%s FOR UPDATE", (sid,))
        r = cur.fetchone()
        if r["status"] not in ("reading", "failed"):
            return
        p = r["parts"] or {}
        if all(k in p for k in PART_KEYS):
            cur.execute("UPDATE closing_scans SET raw=%s, status='review', img=NULL, error=NULL WHERE id=%s", (json.dumps(merge(p)), sid))
        elif r["error"]:
            cur.execute("UPDATE closing_scans SET status='failed' WHERE id=%s", (sid,))


def _r(v):
    return Decimal(v).quantize(D1, rounding=ROUND_HALF_UP)


DIGITAL = (("phone", "phonepe"), ("phonpe", "phonepe"), ("card", "card"), ("swipe", "card"), ("otp", "otp"),
           ("hp pay", "hp pay"), ("hppay", "hp pay"), ("paytm", "paytm"), ("gpay", "gpay"), ("upi", "upi"), ("bharat", "upi"))
EXPENSE_WORDS = r"\b(c\.?\s?f\.?|discount|exp|expense|tif+in|tiffin|lo+ding|loading|tea|snacks?|light|electric|repair|labour|stationery|misc)\b"
MISC_WORDS = r"\b(adv|advance|driver)\b"
BABU = r"\bbabu\b"


def _digital_mode(cur, name):
    low = name.lower()
    cur.execute("SELECT id, name FROM collection_modes WHERE active ORDER BY sort, id")
    modes = cur.fetchall()
    for word, key in DIGITAL:
        if word in low:
            for m in modes:
                ml = m["name"].lower()
                if key in ml or key.replace(" ", "") in ml.replace(" ", "") or (key == "card" and "swipe" in ml) or (key == "otp" and "hpcl" in ml):
                    return m
    return None


def classify(cur, sign, name):
    """→ (kind, target_id, target_name, note). kind: payment | digital | credit | sai | expense | misc | unknown."""
    low = (name or "").lower()
    if sign == "+":
        p, _ = match_party(cur, name)
        return ("payment", p["id"], p["name"], "") if p else ("unknown", None, "", "Payment from a name that is not a transporter")
    m = _digital_mode(cur, name)
    if m:
        return "digital", m["id"], m["name"], ""
    if re.search(BABU, low):
        p, _ = match_party(cur, "Sai Hitech")
        return ("sai", p["id"], p["name"], "Babu → Credit Transport slip for Sai Hitech") if p else \
               ("unknown", None, "", "Babu line, but no transporter named Sai Hitech")
    if re.search(MISC_WORDS, low):
        return "misc", None, "Misc", ""
    if re.search(EXPENSE_WORDS, low):
        return "expense", None, "General", ""
    p, _ = match_party(cur, name)
    if p:
        return "credit", p["id"], p["name"], ""
    return "unknown", None, "", "Name not matched to a transporter — check"


def _fuel_rows(cur, d, code, page):
    """Page columns → nozzles. Each column is matched to the nozzle whose closing yesterday equals the column's
    opening (so the order in Settings does not matter); columns that match nothing keep Settings order."""
    cur.execute("SELECT id, name FROM nozzles WHERE fuel_code=%s AND active ORDER BY sort, id", (code,))
    noz = cur.fetchall()
    last = {}
    for n in noz:
        cur.execute("""SELECT closing FROM nozzle_readings WHERE nozzle_id=%s AND business_date<%s
                       ORDER BY business_date DESC LIMIT 1""", (n["id"], d))
        x = cur.fetchone()
        last[n["id"]] = Decimal(x["closing"]) if x else None
    cols = []
    for i, col in enumerate((page or {}).get("nozzles") or []):
        c, o, df = num(col.get("closing")), num(col.get("opening")), num(col.get("diff"))
        if c is not None and o is not None and c < o and len(cols) >= len(noz):
            continue                       # a totals column read as a nozzle (negative) — not a nozzle
        cols.append((i, c, o, df))
    cols = cols[:max(len(noz), 1) + 1]
    assigned, free = {}, [n for n in noz]
    for i, c, o, df in cols:               # exact opening = yesterday's closing (or within 1 litre)
        hit = next((n for n in free if last[n["id"]] is not None and o is not None and abs(last[n["id"]] - o) <= 1), None)
        if hit:
            assigned[i] = hit; free.remove(hit)
    for i, c, o, df in cols:               # the rest in Settings order
        if i not in assigned and free:
            assigned[i] = free.pop(0)
    rows, flags = [], []
    for i, c, o, df in cols:
        n = assigned.get(i)
        if n is None:
            if c is not None and o is not None and c >= o:
                flags.append(f"{code}: page column {i + 1} has no nozzle in the system")
            continue
        sys_open = last[n["id"]]
        f = []
        if c is None or o is None:
            f.append("reading not clear")
        elif df is not None and abs((c - o) - df) > Decimal("0.01"):
            f.append(f"difference written {df}, but {c} − {o} = {c - o}")
        if sys_open is not None and o is not None and o != sys_open:
            f.append(f"opening {o} ≠ yesterday’s closing in the system {sys_open}")
        rows.append({"col": i + 1, "nozzle": n, "closing": c, "opening": o, "diff": df, "sys_open": sys_open, "flags": f})
        flags += [f"{code} {n['name']}: {x}" for x in f]
    rows.sort(key=lambda r: r["col"])
    page_noz = cols
    mov = sum(((r["closing"] - r["opening"]) for r in rows if r["closing"] is not None and r["opening"] is not None), Decimal(0))
    tot, test, net = num(page.get("total")), num(page.get("testing")), num(page.get("net"))
    if test is None:                       # testing not read: total − net, if both are written
        test = (tot - net) if (tot is not None and net is not None and Decimal(0) <= tot - net <= 100) else Decimal(0)
    if tot is not None and abs(tot - mov) > Decimal("0.01"):
        flags.append(f"{code}: total written {tot}, nozzles add up to {mov}")
    if net is not None and abs(net - (mov - test)) > Decimal("0.01"):
        flags.append(f"{code}: after testing written {net}, but {mov} − {test} = {mov - test}")
    if len(noz) > len(page_noz):
        flags.append(f"{code}: the system has {len(noz)} nozzles, the page shows {len(page_noz)}")
    return {"rows": rows, "movement": mov, "testing": test, "net": mov - test}, flags


def analyse(cur, d, raw):
    """Everything the review screen shows: pre-filled values + every check."""
    from .services import rates_for
    rates = rates_for(cur, d)
    flags, notdone = [], []
    fuels = {}
    for code, key in (("MS", "ms"), ("HSD", "hsd"), ("CNG", "cng_meters")):
        if code == "CNG" and not (raw.get("cng_meters") or {}).get("nozzles"):
            fuels["CNG"] = {"rows": [], "movement": Decimal(0), "testing": Decimal(0), "net": Decimal(0), "page_amount": None}
            continue
        fuels[code], f = _fuel_rows(cur, d, code, raw.get(key) or {})
        flags += f
        sale = raw.get(f"{key}_sale") or {}
        rate, amt = num(sale.get("rate")), num(sale.get("amount"))
        if rate and rates.get(code) and rate != Decimal(rates[code]):
            flags.append(f"{code}: page rate {rate} ≠ saved price {rates[code]}")
        if amt is not None and rate:
            calc = _r(fuels[code]["net"] * rate)
            if abs(calc - amt) > 1:
                flags.append(f"{code} sale written {amt}, but {fuels[code]['net']} × {rate} = {calc}")
        fuels[code]["page_amount"] = amt
    # lube
    cur.execute("SELECT id, name, sale_rate FROM lube_products WHERE active ORDER BY name")
    products = cur.fetchall()
    lube = []
    for i, l in enumerate(raw.get("lube") or []):
        q, rt, a = num(l.get("qty")), num(l.get("rate")), num(l.get("amount"))
        txt = (l.get("text") or "").strip()
        key = re.sub(r"[^a-z0-9]", "", txt.lower())
        cur.execute("SELECT value FROM settings WHERE key=%s", (f"lube_alias:{key}",))
        al = cur.fetchone()
        prod = next((p for p in products if al and str(p["id"]) == al["value"]), None)
        if not prod:
            import difflib
            best = max(products, key=lambda p: difflib.SequenceMatcher(None, key, re.sub(r"[^a-z0-9]", "", p["name"].lower())).ratio(), default=None)
            if best and difflib.SequenceMatcher(None, key, re.sub(r"[^a-z0-9]", "", best["name"].lower())).ratio() >= 0.5:
                prod = best
        lf = []
        if q is None:
            m = re.search(r"(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)", txt)
            if m:
                q, rt = Decimal(m.group(1)), Decimal(m.group(2))
        if q is not None and rt is not None and a is not None and abs(q * rt - a) > Decimal("0.5"):
            lf.append(f"{q} × {rt} = {q * rt}, written {a}")
        if not prod:
            lf.append("product not matched — choose it")
        elif rt is not None and prod["sale_rate"] and Decimal(prod["sale_rate"]) != rt:
            lf.append(f"page rate {rt} ≠ product price {prod['sale_rate']}")
        lube.append({"i": i, "text": txt, "qty": q, "rate": rt, "amount": a, "product": prod, "flags": lf})
        flags += [f"Lube “{txt}”: {x}" for x in lf]
    # CNG: quantity from its 2 nozzles (when written), rate from Settings
    cng = raw.get("cng") or {}
    cq, cr_, ca = num(cng.get("qty")), num(cng.get("rate")), num(cng.get("amount"))
    if fuels["CNG"]["rows"]:
        if cq is not None and abs(fuels["CNG"]["net"] - cq) > Decimal("0.02"):
            flags.append(f"CNG: nozzles add up to {fuels['CNG']['net']}, the page’s CNG line says {cq}")
        cq = fuels["CNG"]["net"]
    if rates.get("CNG") and cr_ and cr_ != Decimal(rates["CNG"]):
        flags.append(f"CNG: page rate {cr_} ≠ saved price {rates['CNG']}")
    if cq and cr_ and ca is not None and abs(_r(cq * cr_) - ca) > 1:
        flags.append(f"CNG {cq} × {cr_} = {_r(cq * cr_)}, written {ca}")
    cur.execute("SELECT id, name FROM nozzles WHERE fuel_code='CNG' AND active ORDER BY sort, id LIMIT 1")
    cng_noz = cur.fetchone()
    # collection
    fuel_total, after_lube, coll = num(raw.get("fuel_total")), num(raw.get("after_lube")), num(raw.get("collection"))
    sales = sum((x for x in (fuels["MS"]["page_amount"], fuels["HSD"]["page_amount"]) if x is not None), Decimal(0))
    lube_sum = sum((l["amount"] or 0 for l in lube), Decimal(0))
    if fuel_total is not None and abs(fuel_total - sales) > 1:
        flags.append(f"MS + HSD sale = {sales}, written {fuel_total}")
    if fuel_total is not None and after_lube is not None and abs((after_lube - fuel_total) - lube_sum) > 1:
        flags.append(f"Lube: the page’s lube total is {after_lube - fuel_total}, but the lube lines read add up to {lube_sum} — a lube line is missing or misread")
    if coll is not None and abs(coll - (sales + lube_sum + (ca or 0))) > 1:
        flags.append(f"Collection written {coll}, but sales {sales} + lube {lube_sum} + CNG {ca or 0} = {sales + lube_sum + (ca or 0)}")
    # ± lines with running totals
    lines, running = [], coll or Decimal(0)
    raw_lines = raw.get("lines") or []
    in_exp = False                     # every line after the "EXP" total is an expense (owner's page format)
    since = []                         # lines since the last running total — where a mistake must be
    for i, l in enumerate(raw_lines):
        sign, name, amt = (l.get("sign") or "").strip(), (l.get("name") or "").strip(), num(l.get("amount"))
        around = " / ".join(x.get("name") or "" for x in raw_lines[max(0, i - 1):i + 2] if x is not l)
        if sign == "=":
            if re.search(r"\bexp", name.lower()):
                in_exp = True
            ok = amt is not None and abs(amt - running) <= 1
            lines.append({"i": i, "kind": "total", "name": name, "amount": amt, "running": running, "ok": ok})
            if not ok:
                flags.append(f"Running total written {amt}, but it should be {running} — check: " + (", ".join(since) or "the line above"))
                if amt is not None:
                    running = amt          # continue from what is written, so later checks still point at the right place
            since = []
            continue
        if sign not in ("+", "-") or amt is None or not name:
            notdone.append(f"Line {i + 1} “{name or '?'} {amt or ''}” not understood — between: {around}")
            continue
        running += amt if sign == "+" else -amt
        since.append(f"{name} {sign}{amt}")
        kind, tid, tname, note = classify(cur, sign, name)
        if in_exp and sign == "-" and kind in ("unknown", "credit") and not re.search(BABU, name.lower()):
            kind, tid, tname, note = ("misc", None, "Misc", "") if re.search(MISC_WORDS, name.lower()) else ("expense", None, "General", "written under EXP")
        if kind == "unknown":
            flags.append(f"“{name}” {sign}{amt}: {note}")
        lines.append({"i": i, "kind": kind, "sign": sign, "name": name, "amount": amt, "target_id": tid, "target": tname, "note": note})
    final = num(raw.get("final_cash"))
    dip_vals = {num(x.get(k)) for x in (raw.get("dips") or []) for k in ("litres", "net")} - {None}
    if final is not None and final in dip_vals:
        flags.append(f"Final cash read as {final}, which is a dip-stock figure — using the page’s running total {running}")
        final = None
    if final is not None and abs(final - running) > 1:
        flags.append(f"Final cash written {final}, but the page adds up to {running}")
    # dips
    cur.execute("SELECT id, name, fuel_code FROM tanks WHERE active ORDER BY id")
    tanks = cur.fetchall()
    dips = []
    for x in raw.get("dips") or []:
        code = (x.get("fuel") or "").upper().replace("PETROL", "MS").replace("DIESEL", "HSD")
        t = next((t for t in tanks if t["fuel_code"] == code), None)
        lit, adj, net = num(x.get("litres")), num((x.get("adjust") or "").replace("-", "")), num(x.get("net"))
        val = net if net is not None else (lit - (adj or 0) if lit is not None else None)
        dips.append({"tank": t, "fuel": code, "dip": num(x.get("dip")), "litres": val})
    return {"fuels": fuels, "lube": lube, "cng": {"qty": cq, "rate": cr_, "amount": ca, "nozzle": cng_noz},
            "collection": coll, "lines": lines, "final_cash": final, "page_cash": running, "dips": dips,
            "flags": flags, "notdone": notdone, "rates": rates}


def save(cur, d, form, a):
    """Save the reviewed page. Same tables and rules as typing in Daily Closing; returns a summary."""
    from flask import request as _rq  # noqa: F401 (form is passed in)
    from .actions import add_expense, add_payment, lube_sale
    from .security import assert_writable, audit, current_user
    from .util import UserError
    from .views.transport import create_entry
    assert_writable(cur, d)
    cur.execute("INSERT INTO days (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
    g = lambda k: (form.get(k) or "").strip()
    done = {"readings": 0, "digital": 0, "credit": 0, "payments": 0, "expenses": 0, "lube": 0, "slips": 0, "dips": 0}
    who = current_user()["username"]
    # meter readings — testing of each fuel goes on its first nozzle (as much as it moved), the rest on the next
    for code in ("MS", "HSD", "CNG"):
        test_left = Decimal(g(f"test_{code}") or 0)
        for r in a["fuels"][code]["rows"]:
            n = r["nozzle"]
            if not n:
                continue
            op, cl = Decimal(g(f"open_{n['id']}") or 0), Decimal(g(f"close_{n['id']}") or 0)
            if cl < op:
                raise UserError(f"{n['name']}: closing is less than opening.")
            if r["sys_open"] is not None and op != r["sys_open"]:
                from .security import role
                if role() != "owner":
                    raise UserError(f"{n['name']}: opening {op} is not yesterday’s closing {r['sys_open']}. Correct it, or ask the owner.")
                audit(cur, "update", "nozzle_reading", n["id"], f"{n['name']} {d:%d %b}: opening {op} saved by the owner although "
                      f"yesterday’s closing is {r['sys_open']} (scanned page)", business_date=d)
            t = min(test_left, cl - op)
            test_left -= t
            rate = a["rates"].get(code)
            if not rate:
                raise UserError(f"No {code} price is set for this date.")
            cur.execute("""INSERT INTO nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate, updated_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (business_date, nozzle_id) DO UPDATE SET
                           opening=EXCLUDED.opening, closing=EXCLUDED.closing, testing=EXCLUDED.testing, rate=EXCLUDED.rate,
                           updated_by=EXCLUDED.updated_by, updated_at=NOW() RETURNING id""", (d, n["id"], op, cl, t, rate, who))
            audit(cur, "update", "nozzle_reading", cur.fetchone()["id"], f"{n['name']}: {op} → {cl} (test {t}) — scanned page", business_date=d)
            done["readings"] += 1
    # CNG: manager types the opening; closing = opening + page quantity
    cn = a["cng"]["nozzle"]
    if cn and not a["fuels"]["CNG"]["rows"] and g("cng_open") and g("cng_qty"):
        op, q = Decimal(g("cng_open")), Decimal(g("cng_qty"))
        rate = a["rates"].get("CNG")
        if rate:
            cur.execute("""INSERT INTO nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate, updated_by)
                           VALUES (%s,%s,%s,%s,0,%s,%s) ON CONFLICT (business_date, nozzle_id) DO UPDATE SET opening=EXCLUDED.opening,
                           closing=EXCLUDED.closing, rate=EXCLUDED.rate, updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                        (d, cn["id"], op, op + q, rate, who))
            done["readings"] += 1
    # lube
    for l in a["lube"]:
        if g(f"lube_use_{l['i']}") != "1":
            continue
        pid, q = int(g(f"lube_prod_{l['i']}") or 0), g(f"lube_qty_{l['i']}")
        if pid and q:
            lube_sale(cur, pid, q, d)
            key = re.sub(r"[^a-z0-9]", "", l["text"].lower())
            if key:
                cur.execute("INSERT INTO settings (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                            (f"lube_alias:{key}", str(pid)))
            done["lube"] += 1
    # ± lines
    credit, digital = {}, {}
    for l in a["lines"]:
        if l["kind"] == "total":
            continue
        i = l["i"]
        kind, target, amt = g(f"kind_{i}"), g(f"target_{i}"), Decimal(g(f"amt_{i}") or 0)
        if kind in ("", "skip") or amt <= 0:
            continue
        if kind == "payment":
            add_payment(cur, int(target), d, amt, "Cash", remarks="scanned closing page"); done["payments"] += 1
        elif kind == "digital":
            digital[int(target)] = digital.get(int(target), Decimal(0)) + amt
        elif kind == "credit":
            credit[int(target)] = credit.get(int(target), Decimal(0)) + amt
        elif kind == "sai":
            r, _ = create_entry(cur, {"party": target, "fuel_code": "HSD", "vehicle_no": l["name"].upper()[:20],
                                      "amount": str(amt), "remarks": "Scanned closing page"}, d)
            audit(cur, "create", "transport_entry", r["id"], f"SL {r['sl_no']} {l['name']} ₹{r['debit']} (Sai Hitech, scanned closing)", business_date=d)
            credit[int(target)] = credit.get(int(target), Decimal(0)) + Decimal(r["debit"]); done["slips"] += 1
        elif kind in ("expense", "misc"):
            add_expense(cur, d, amt, "Misc" if kind == "misc" else (target or "General"), l["name"]); done["expenses"] += 1
        # remember how a transporter is written
        if kind in ("credit", "payment") and target:
            key = re.sub(r"[^A-Z0-9]", "", l["name"].upper())
            if key:
                cur.execute("INSERT INTO settings (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                            (f"party_alias:{key}", target))
    for mid, amt in digital.items():
        cur.execute("""INSERT INTO day_collections (business_date, mode_id, amount, updated_by) VALUES (%s,%s,%s,%s)
                       ON CONFLICT (business_date, mode_id) DO UPDATE SET amount=EXCLUDED.amount, updated_by=EXCLUDED.updated_by,
                       updated_at=NOW()""", (d, mid, amt, who)); done["digital"] += 1
    for pid, amt in credit.items():
        cur.execute("""INSERT INTO day_credit (business_date, party_id, amount, updated_by) VALUES (%s,%s,%s,%s)
                       ON CONFLICT (business_date, party_id) DO UPDATE SET amount=EXCLUDED.amount, updated_by=EXCLUDED.updated_by,
                       updated_at=NOW()""", (d, pid, amt, who)); done["credit"] += 1
    # dips
    for x in a["dips"]:
        t = x["tank"]
        v = g(f"dip_{t['id']}") if t else ""
        if t and v:
            cur.execute("""INSERT INTO tank_dips (business_date, tank_id, dip_mm, volume_l, source, updated_by)
                           VALUES (%s,%s,%s,%s,'manual',%s) ON CONFLICT (business_date, tank_id) DO UPDATE SET
                           dip_mm=EXCLUDED.dip_mm, volume_l=EXCLUDED.volume_l, updated_by=EXCLUDED.updated_by""",
                        (d, t["id"], (x["dip"] or 0) * 10, Decimal(v), who)); done["dips"] += 1
    audit(cur, "create", "closing_scan", None, f"Daily closing {d:%d %b %Y} filled from a scanned page: " +
          ", ".join(f"{k} {v}" for k, v in done.items() if v), business_date=d)
    return done
