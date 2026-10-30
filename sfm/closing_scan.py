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

PROMPT = """Photo of a fuel station's handwritten DAILY CLOSING notebook page. Transcribe EXACTLY what is written;
do not calculate or correct anything.
- date: the date at the top right.
- ms / hsd: the nozzle columns. First row = closing readings, second row = opening readings, third row = difference,
  left to right. total = the column sum on the right; testing = the number subtracted "for testing"; net = after testing.
- ms_sale / hsd_sale: "MS SALE = litres x rate = amount".
- fuel_total: the total of the two sales. lube: each lube line (text as written, quantity, rate, amount).
  after_lube: the total after lube lines.
- cng: "C.N.G = qty x rate = amount". collection: the number next to COLLECTION.
- lines: EVERY line after COLLECTION that has a + or − amount, in order: first the left column top to bottom, then the
  right column top to bottom. sign "+" or "-"; name exactly as written (e.g. "S.V.T", "Phonepe", "HPCL OTP",
  "Gajalaxmi", "C.F (Discount)", "Babu car"); amount digits only. Also add each underlined running total as
  sign "=" with that amount, at its place. Words like "EXP" written next to a total belong to that total line's name.
- final_cash: the last total at the bottom of the right column.
- dips: "MS Stock: 94.4 = 9328", "HSD Stock: 196.2 = 29133 − 14 = 29119" → fuel, dip, litres, adjust (e.g. -14), net.
Numbers: digits and decimal point only, no commas. Unreadable → "".
"""

_N = {"type": "STRING"}
_FUEL = {"type": "OBJECT", "properties": {
    "nozzles": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"closing": _N, "opening": _N, "diff": _N}}},
    "total": _N, "testing": _N, "net": _N}}
_SALE = {"type": "OBJECT", "properties": {"litres": _N, "rate": _N, "amount": _N}}
SCHEMA = {"type": "OBJECT", "properties": {
    "date": _N, "ms": _FUEL, "hsd": _FUEL, "ms_sale": _SALE, "hsd_sale": _SALE, "fuel_total": _N,
    "lube": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"text": _N, "qty": _N, "rate": _N, "amount": _N}}},
    "after_lube": _N, "cng": {"type": "OBJECT", "properties": {"qty": _N, "rate": _N, "amount": _N}}, "collection": _N,
    "lines": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"sign": _N, "name": _N, "amount": _N}}},
    "final_cash": _N,
    "dips": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"fuel": _N, "dip": _N, "litres": _N, "adjust": _N, "net": _N}}}},
    "required": ["ms", "hsd", "lines"]}


def read_closing(image_bytes, mime="image/jpeg"):
    img = {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode()}}
    return _ask([img, {"text": PROMPT}], "scan", schema=SCHEMA)


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
    cur.execute("SELECT id, name FROM nozzles WHERE fuel_code=%s AND active ORDER BY sort, id", (code,))
    noz = cur.fetchall()
    rows, flags = [], []
    page_noz = (page or {}).get("nozzles") or []
    for i, col in enumerate(page_noz):
        n = noz[i] if i < len(noz) else None
        c, o, df = num(col.get("closing")), num(col.get("opening")), num(col.get("diff"))
        sys_open = None
        if n:
            cur.execute("""SELECT closing FROM nozzle_readings WHERE nozzle_id=%s AND business_date<%s
                           ORDER BY business_date DESC LIMIT 1""", (n["id"], d))
            x = cur.fetchone()
            sys_open = Decimal(x["closing"]) if x else None
        f = []
        if n is None:
            f.append(f"column {i + 1}: no {code} nozzle {i + 1} in the system")
        if c is None or o is None:
            f.append("reading not clear")
        elif df is not None and abs((c - o) - df) > Decimal("0.01"):
            f.append(f"difference written {df}, but {c} − {o} = {c - o}")
        if sys_open is not None and o is not None and o != sys_open:
            f.append(f"opening {o} ≠ yesterday’s closing in the system {sys_open}")
        rows.append({"col": i + 1, "nozzle": n, "closing": c, "opening": o, "diff": df, "sys_open": sys_open, "flags": f})
        flags += [f"{code} {n['name'] if n else 'col ' + str(i + 1)}: {x}" for x in f]
    mov = sum(((r["closing"] - r["opening"]) for r in rows if r["closing"] is not None and r["opening"] is not None), Decimal(0))
    tot, test, net = num(page.get("total")), num(page.get("testing")) or Decimal(0), num(page.get("net"))
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
    for code, key in (("MS", "ms"), ("HSD", "hsd")):
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
    # CNG
    cng = raw.get("cng") or {}
    cq, cr_, ca = num(cng.get("qty")), num(cng.get("rate")), num(cng.get("amount"))
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
    for code in ("MS", "HSD"):
        test_left = Decimal(g(f"test_{code}") or 0)
        for r in a["fuels"][code]["rows"]:
            n = r["nozzle"]
            if not n:
                continue
            op, cl = Decimal(g(f"open_{n['id']}") or 0), Decimal(g(f"close_{n['id']}") or 0)
            if cl < op:
                raise UserError(f"{n['name']}: closing is less than opening.")
            if r["sys_open"] is not None and op != r["sys_open"]:
                raise UserError(f"{n['name']}: opening {op} is not yesterday’s closing {r['sys_open']}. Correct it before saving.")
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
    if cn and g("cng_open") and g("cng_qty"):
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
