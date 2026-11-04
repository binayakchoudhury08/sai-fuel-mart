"""Website ↔ the owner's Tally workbook: stored versions, Tally names, day rows from Daily Closing, daily check."""
import difflib
import re
from datetime import date
from decimal import Decimal

from .tally_book import LUBE, METER, PAYMENT, RECEIPT, SALES, Adder, read_date, rows_for
from .util import UserError

KINDS = {"party": "Transporter / party → Tally ledger", "mode": "Payment mode → ‘Received In’ ledger",
         "expense": "Expense → ‘Paid To’ ledger", "item": "Lube product → Tally item", "meter": "Nozzle → pump meter"}


# ------------------------------------------------------------------ stored workbook (every version kept)
def current(cur):
    cur.execute("SELECT id, created_at, created_by, note, data FROM tally_books ORDER BY id DESC LIMIT 1")
    r = cur.fetchone()
    return (bytes(r["data"]), r) if r else (None, None)


def save_version(cur, data, note, who):
    cur.execute("INSERT INTO tally_books (data, note, created_by) VALUES (%s,%s,%s) RETURNING id", (data, note[:300], who))
    vid = cur.fetchone()["id"]
    cur.execute("DELETE FROM tally_books WHERE id < %s - 60", (vid,))         # keep the last 60 versions
    return vid


# ------------------------------------------------------------------ names (learned once, confirmed by the owner)
def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def masters(adder):
    v = adder.v
    led = [v["LEDGER MASTER"].cell(r, 1).value for r in range(2, v["LEDGER MASTER"].max_row + 1)]
    items = [v["ITEM MASTER"].cell(r, 1).value for r in range(2, v["ITEM MASTER"].max_row + 1)]
    meters = [v[METER].cell(r, 18).value for r in range(4, 12)]
    return [x for x in led if x], [x for x in items if x], [x for x in meters if x]


def name_map(cur):
    cur.execute("SELECT kind, site_name, tally_name FROM tally_names")
    out = {}
    for r in cur.fetchall():
        out.setdefault(r["kind"], {})[r["site_name"]] = r["tally_name"]
    return out


def suggest(site, choices):
    """A pre-selected guess only when it is clearly the same name (very close, or the same first word);
    otherwise none — the owner picks. A wrong guess (e.g. another transporter) is worse than no guess."""
    k = norm(site)
    if not k or not choices:
        return None
    first = (re.findall(r"[a-z0-9]+", (site or "").lower()) or [""])[0]
    scored = []
    for c in choices:
        ratio = difflib.SequenceMatcher(None, k, norm(c)).ratio()
        same_first = len(first) >= 3 and (re.findall(r"[a-z0-9]+", c.lower()) or [""])[0] == first
        if ratio >= 0.85 or (same_first and ratio >= 0.5):
            scored.append((ratio + (0.2 if same_first else 0), c))
    return max(scored)[1] if scored else None


# ------------------------------------------------------------------ one day from Daily Closing → rows
def day_rows(cur, d, adder):
    """What the website can fill for date d (the owner adjusts MS / CNG / digital himself).
    → (rows, missing): rows = [(sheet, values...)], missing = [(kind, site_name)] still without a Tally name."""
    from .services import rates_for
    names = name_map(cur)
    missing, rows = [], []

    def tn(kind, site):
        t = names.get(kind, {}).get(site)
        if not t:
            missing.append((kind, site))
        return t or site

    rates = rates_for(cur, d)
    # meters: closing readings of every nozzle
    cur.execute("""SELECT n.name, r.closing FROM nozzle_readings r JOIN nozzles n ON n.id=r.nozzle_id
                   WHERE r.business_date=%s AND r.closing IS NOT NULL ORDER BY n.fuel_code, n.sort, n.id""", (d,))
    closings = {}
    for r in cur.fetchall():
        closings[tn("meter", r["name"])] = Decimal(r["closing"])
    if closings:
        rows.append((METER, closings))
    # credit given (declared totals of the day) → HSD SALES
    cur.execute("""SELECT p.name, c.amount FROM day_credit c JOIN parties p ON p.id=c.party_id
                   WHERE c.business_date=%s AND c.amount > 0 ORDER BY p.name""", (d,))
    for r in cur.fetchall():
        rows.append(("HSD", tn("party", r["name"]), Decimal(r["amount"]), rates.get("HSD")))
    # lube sales
    cur.execute("""SELECT p.name, SUM(-m.qty) AS qty, m.rate FROM lube_moves m JOIN lube_products p ON p.id=m.product_id
                   WHERE m.business_date=%s AND m.kind='sale' AND NOT m.voided GROUP BY p.name, m.rate ORDER BY p.name""", (d,))
    for r in cur.fetchall():
        rows.append((LUBE, "Cash", tn("item", r["name"]), Decimal(r["qty"]), Decimal(r["rate"])))
    # expenses → PAYMENT (Cash → ledger)
    cur.execute("SELECT category, description, amount FROM expenses WHERE business_date=%s AND NOT COALESCE(voided,FALSE) ORDER BY id", (d,))
    for r in cur.fetchall():
        key = (r["description"] or r["category"] or "").strip()
        t = names.get("expense", {}).get(key) or names.get("expense", {}).get(r["category"])
        if not t:
            missing.append(("expense", key))
        rows.append((PAYMENT, "Cash", t or key, Decimal(r["amount"])))
    # payments received → RECEIPT (mode ledger ← party ledger)
    cur.execute("""SELECT p.name, t.credit, t.pay_mode FROM party_txns t JOIN parties p ON p.id=t.party_id
                   WHERE t.business_date=%s AND t.kind='payment' AND NOT t.voided ORDER BY t.id""", (d,))
    for r in cur.fetchall():
        rows.append((RECEIPT, tn("mode", r["pay_mode"]), tn("party", r["name"]), Decimal(r["credit"])))
    cur.execute("""SELECT m.name FROM day_collections c JOIN collection_modes m ON m.id=c.mode_id
                   WHERE c.business_date=%s AND c.amount > 0""", (d,))
    for r in cur.fetchall():                               # the apps need a Tally name too (asked once)
        tn("mode", r["name"])
    seen, uniq = set(), []
    for m in missing:
        if m not in seen:
            seen.add(m); uniq.append(m)
    return rows, uniq


def already_has(adder, d):
    v = adder.v
    return {sh: len(rows_for(v[sh], d)) for sh in list(SALES.values()) + [LUBE, PAYMENT, RECEIPT]}


def add_day(cur, data, d):
    a = Adder(data)
    have = already_has(a, d)
    if any(have.values()):
        raise UserError(f"{d:%d/%m/%Y} is already in the workbook ({', '.join(f'{k} {n}' for k, n in have.items() if n)}). "
                        "Change it sheet by sheet instead.")
    rows, missing = day_rows(cur, d, a)
    if missing:
        raise UserError("Give these a Tally name first (Settings → Tally names): " + ", ".join(f"{k}: {s}" for k, s in missing))
    if not rows:
        raise UserError(f"No Daily Closing data on {d:%d/%m/%Y}.")
    for row in [r for r in rows if r[0] == METER]:
        a.meters(d, row[1])
    meter_amt = {}                                          # the fuel totals = the meter amounts just written
    for (sh, ref), val in a.cache.items():
        if sh == METER and ref.startswith("D") and isinstance(val, str):
            r_ = ref[1:]
            nm = val.upper()
            f_ = "MS" if "PETROL" in nm else ("HSD" if "DIESEL" in nm else ("CNG" if "CNG" in nm else None))
            amt = a.cache.get((METER, f"L{r_}"))
            if f_ and amt not in (None, ""):
                meter_amt[f_] = meter_amt.get(f_, Decimal(0)) + Decimal(str(amt))
    from .services import rates_for
    names_ = name_map(cur)
    rows = rows + auto_split(cur, d, rows, lambda k, s_: names_.get(k, {}).get(s_, s_), rates_for(cur, d), meter_amt)
    for row in rows:
        if row[0] == METER:
            continue
        elif row[0] in SALES:
            a.sale(row[0], d, row[1], row[2], row[3])
        elif row[0] == LUBE:
            a.lube(d, *row[1:])
        else:
            a.voucher(row[0], d, *row[1:])
    return a.save(), a.added


# ------------------------------------------------------------------ the day as the workbook has it + the cash check
def day_view(data, d):
    a = data if isinstance(data, Adder) else Adder(data)
    v = a.v
    out = {}
    for fuel, sh in SALES.items():
        out[sh] = [{"row": r, "party": v[sh].cell(r, 3).value, "qty": v[sh].cell(r, 4).value, "rate": v[sh].cell(r, 5).value,
                    "amount": v[sh].cell(r, 6).value} for r in rows_for(v[sh], d)]
    out[LUBE] = [{"row": r, "party": v[LUBE].cell(r, 3).value, "item": v[LUBE].cell(r, 4).value, "qty": v[LUBE].cell(r, 5).value,
                  "rate": v[LUBE].cell(r, 6).value, "amount": v[LUBE].cell(r, 7).value} for r in rows_for(v[LUBE], d)]
    for sh in (PAYMENT, RECEIPT):
        out[sh] = [{"row": r, "from": v[sh].cell(r, 3).value, "to": v[sh].cell(r, 4).value, "amount": v[sh].cell(r, 5).value}
                   for r in rows_for(v[sh], d)]
    m = v[METER]
    out[METER] = [{"row": r, "meter": m.cell(r, 4).value, "opening": m.cell(r, 6).value, "closing": m.cell(r, 7).value,
                   "testing": m.cell(r, 8).value, "net": m.cell(r, 9).value, "amount": m.cell(r, 12).value}
                  for r in range(90, m.max_row + 1) if read_date(m.cell(r, 2).value) == d]
    return out


def daily_check(view):
    """Totals per sheet and the cash balance, the way the DAILY CHECK sheet works."""
    num = lambda x: Decimal(str(x or 0))
    t = {}
    for fuel, sh in SALES.items():
        t[f"{fuel} qty"] = sum((num(r["qty"]) for r in view[sh]), Decimal(0))
        t[f"{fuel} amount"] = sum((num(r["amount"]) for r in view[sh]), Decimal(0))
    t["Lube amount"] = sum((num(r["amount"]) for r in view[LUBE]), Decimal(0))
    cash_sales = sum((num(r["amount"]) for sh in SALES.values() for r in view[sh] if (r["party"] or "").strip().lower() == "cash"), Decimal(0)) \
        + sum((num(r["amount"]) for r in view[LUBE] if (r["party"] or "").strip().lower() == "cash"), Decimal(0))
    cash_pay = sum((num(r["amount"]) for r in view[PAYMENT] if (r["from"] or "").strip().lower() == "cash"), Decimal(0))
    cash_rec = sum((num(r["amount"]) for r in view[RECEIPT] if (r["from"] or "").strip().lower() == "cash"), Decimal(0))
    t.update({"Cash sales": cash_sales, "Cash receipt": cash_rec, "Cash payment": cash_pay, "Final cash balance": cash_sales + cash_rec - cash_pay})
    digital = {}
    for sh in SALES.values():
        for r in view[sh]:
            p = (r["party"] or "").strip()
            if norm(p) in ("phonepe", "paytm", "dtplushpcl", "hppay", "card"):
                digital[p] = digital.get(p, Decimal(0)) + num(r["amount"])
    for r in view[RECEIPT]:
        p = (r["from"] or "").strip()
        if p.lower() != "cash":
            digital[p] = digital.get(p, Decimal(0)) + num(r["amount"])
    t["digital"] = digital
    meter = {}
    for r in view[METER]:
        n = (r["meter"] or "").upper()
        fuel = "MS" if "PETROL" in n else ("HSD" if "DIESEL" in n else ("CNG" if "CNG" in n else None))
        if fuel:
            q, a_ = meter.get(fuel, (Decimal(0), Decimal(0)))
            meter[fuel] = (q + num(r["net"]), a_ + num(r["amount"]))
    t["match"] = []
    for fuel in ("HSD", "MS", "CNG"):
        mq, ma = meter.get(fuel, (Decimal(0), Decimal(0)))
        sq, sa = t[f"{fuel} qty"], t[f"{fuel} amount"]
        if not (mq or ma or sq or sa):
            continue
        t["match"].append((f"{fuel} litres", mq, sq, sq - mq, abs(sq - mq) <= Decimal("1")))
        t["match"].append((f"{fuel} amount", ma, sa, sa - ma, abs(sa - ma) <= Decimal("10")))
    return t


def set_amount(data, sheet, d, name, amount, rate=None, from_ledger=None, litres=False):
    """WhatsApp / website: '<sheet> <date> <party> <amount>' → change that row, or add it after the day's rows."""
    a = Adder(data)
    v = a.v[sheet]
    key = norm(name)
    hit = None
    for r in rows_for(v, d):
        col = 4 if sheet in (PAYMENT, RECEIPT) else 3
        if norm(v.cell(r, col).value) == key or (sheet == LUBE and norm(v.cell(r, 4).value) == key):
            hit = r
            break
    fuel = next((f for f, s in SALES.items() if s == sheet), None)
    if hit:
        if sheet == LUBE:
            a.change(sheet, hit, qty=amount, rate=rate)
        elif sheet in (PAYMENT, RECEIPT):
            a.change(sheet, hit, amount=amount, party=from_ledger)        # also the "received in / paid from" ledger
        elif litres:
            a.change(sheet, hit, qty=amount, rate=rate)
        else:
            a.change(sheet, hit, amount=amount, rate=rate)
        what = f"changed row {hit}"
    else:
        if fuel:
            if litres:
                rt = Decimal(str(rate if rate is not None else a._val(sheet, f"E{data_end_rows(a.v[sheet])}")))
                amount = (Decimal(str(amount)) * rt).quantize(Decimal("1"))
            hit = a.sale(fuel, d, name, amount, rate)
        elif sheet in (PAYMENT, RECEIPT):
            hit = a.voucher(sheet, d, from_ledger or "Cash", name, amount)
        elif sheet == LUBE:
            if rate is None:
                raise UserError("Give the rate too, e.g. 12 @ 14.")
            hit = a.lube(d, "Cash", name, amount, rate)
        else:
            raise UserError("Unknown sheet.")
        what = f"added row {hit}"
    return a.save(), what


def sheet_names(data, sheet, col=None, limit=8):
    """Names most used in this sheet of the owner's Excel (recent rows first) — for the tap-menu."""
    from collections import Counter
    a = Adder(data)
    ws = a.v[sheet]
    col = col or (4 if sheet in (PAYMENT, LUBE) else 3)
    end = data_end_rows(ws)
    cnt = Counter()
    for r in range(max(2, end - 600), end + 1):
        v = ws.cell(r, col).value
        if v:
            cnt[str(v).strip()] += 1
    return [n for n, _ in cnt.most_common(limit)], a


def data_end_rows(ws):
    from .tally_book import data_end
    return data_end(ws)


def search_names(a, sheet, text, limit=9):
    led, items, _m = masters(a)
    pool = items if sheet == LUBE else led
    k = norm(text)
    hits = [x for x in pool if norm(x).startswith(k)] + [x for x in pool if k in norm(x) and not norm(x).startswith(k)]
    return hits[:limit]


def edit_row(data, sheet, row, fields):
    """Website / WhatsApp: change any input of one row (party / item / ledger, amount, qty, rate) — or a meter closing."""
    a = Adder(data)
    if sheet == METER:
        a.change_meter(fields["d"], fields["meter"], fields["closing"])
    else:
        clean = {k: v for k, v in fields.items() if k in ("party", "amount", "item", "qty", "rate", "to") and v not in (None, "")}
        for k in ("amount", "qty", "rate"):
            if k in clean:
                clean[k] = Decimal(str(clean[k]))
        if sheet in SALES.values() and "qty" in clean:
            orig = fields.get("orig_qty")
            if orig not in (None, "") and Decimal(str(orig)) == clean["qty"]:
                clean.pop("qty")                                # litres not changed → the amount decides
            else:
                clean.pop("amount", None)                       # litres changed → amount = litres × rate
        a.change(sheet, int(row), **clean)
    return a.save()


def _app(led):
    k = norm(led)
    return "dtplus" if "dtplus" in k or "hpcl" in k else ("paytm" if "paytm" in k else ("phonepe" if "phonepe" in k else None))


APPS_FOR_RECEIPTS = ("dtplus", "paytm", "phonepe")          # payments received are paid from these first, in this order
APPS_FOR_FUEL = ("dtplus", "phonepe", "paytm")              # then the apps fill what cash did not cover: CNG, MS, HSD


def _app_rank(led, order):
    k = norm(led)
    return next((i for i, key in enumerate(order) if key in k), 9)


def auto_split(cur, d, rows, tn, rates, fuel=None):
    """The owner's adjustment rule (his words, 04 Nov):
    1. Payments received are paid from the app money first — DTPLUS, then Paytm, then PhonePe; the rest is cash.
    2. Cash (before expenses) → lube, then CNG, then MS (then HSD only if still left).
    3. The apps (DTPLUS, PhonePe, Paytm) fill what cash did not cover: CNG, then MS, then HSD.
    Credit parties are HSD. Rebuilds the RECEIPT rows in `rows` and returns the new sales rows."""
    fuel = {f: (fuel or {}).get(f, Decimal(0)).quantize(Decimal("1")) for f in ("HSD", "MS", "CNG")}
    if not any(fuel.values()):
        return []
    credit = sum((r[2] for r in rows if r[0] == "HSD"), Decimal(0))
    cur.execute("""SELECT m.name, c.amount FROM day_collections c JOIN collection_modes m ON m.id=c.mode_id
                   WHERE c.business_date=%s AND c.amount > 0 ORDER BY m.id""", (d,))
    apps = {}
    for r in cur.fetchall():
        led = tn("mode", r["name"])
        apps[led] = apps.get(led, Decimal(0)) + Decimal(r["amount"])
    receipts = [r for r in rows if r[0] == RECEIPT]                      # 1. receipts from the apps first
    for r in receipts:
        rows.remove(r)
    for _sh, _in, party, amt in receipts:
        left = amt
        for led in sorted(apps, key=lambda l: _app_rank(l, APPS_FOR_RECEIPTS)):
            if _app_rank(led, APPS_FOR_RECEIPTS) == 9 or left <= 0:
                continue
            take = min(left, apps[led])
            if take > 0:
                rows.append((RECEIPT, led, party, take)); apps[led] -= take; left -= take
        if left > 0:
            rows.append((RECEIPT, "Cash", party, left))
    need = dict(fuel)
    need["HSD"] -= min(credit, need["HSD"])
    cash_fuel = sum(need.values(), Decimal(0)) - sum(apps.values(), Decimal(0))
    out = []

    def give(f, led, amt):
        amt = min(amt, need[f])
        if amt > 0:
            need[f] -= amt
            out.append((f, led, amt, rates.get(f)))
        return amt

    for f in ("CNG", "MS", "HSD"):                                          # 2. cash (lube is already cash)
        cash_fuel -= give(f, "Cash", max(cash_fuel, Decimal(0)))
    for led in sorted(apps, key=lambda l: _app_rank(l, APPS_FOR_FUEL)):    # 3. the apps: CNG, MS, HSD
        amt = apps[led]
        for f in ("CNG", "MS", "HSD"):
            amt -= give(f, led, amt)
        if amt > 0:
            out.append(("HSD", led, amt, rates.get("HSD")))
    return out

