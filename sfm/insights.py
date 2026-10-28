"""Business insights — one place for the numbers the Insights page, the scheduled jobs and the WhatsApp
assistant all use, so they can never disagree.

Rules for exactness (owner's request: "no error, no malpractice"):
* Only stored entries are used; nothing is estimated silently.
* Whenever an input is missing (day not closed, tanker without invoice amount, lube sold without purchase rate,
  no dip), the result carries an "issues" list and is marked not final.
* Money is rounded only at the end (2 decimals), never in between.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from .services import tank_series
from .util import ZERO

D2 = Decimal("0.01")


def q2(v):
    return Decimal(v or 0).quantize(D2, ROUND_HALF_UP)


def _setting(cur, key, default):
    cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
    r = cur.fetchone()
    try:
        return Decimal(r["value"]) if r and r["value"] not in (None, "") else Decimal(default)
    except Exception:
        return Decimal(default)


# ====================================================================== 1. tank variation (stock loss / gain)
def tank_variation(cur, start, end):
    """Per tank: daily book vs actual (dip/ATG), totals, % of sale, value, and a verdict against the tolerance
    (setting auto_variation_pct, default 0.5 % of sale)."""
    tol = _setting(cur, "auto_variation_pct", "0.5")
    rows = tank_series(cur, start, end)
    tanks = {}
    for r in rows:
        t = tanks.setdefault(r["tank"]["id"], {"tank": r["tank"], "days": [], "sold": ZERO, "variance": ZERO,
                                               "value": ZERO, "checked_days": 0, "missing_dip": 0, "bad_days": 0})
        t["days"].append(r)
        if r["variance"] is None:
            t["missing_dip"] += 1
            continue
        t["checked_days"] += 1
        t["sold"] += r["sold"] or ZERO
        t["variance"] += r["variance"]
        t["value"] += r["value"] or ZERO
        if r["sold"] and abs(r["variance"]) > abs(r["sold"]) * tol / 100:
            t["bad_days"] += 1
    out = []
    for t in tanks.values():
        pct = (t["variance"] / t["sold"] * 100).quantize(D2, ROUND_HALF_UP) if t["sold"] else None
        verdict = ("no data" if not t["checked_days"] else
                   "loss" if pct is not None and pct < -tol else
                   "gain" if pct is not None and pct > tol else "normal")
        # trend: is the loss growing? compare first and second half of the checked days
        checked = [d for d in t["days"] if d["variance"] is not None and d["sold"]]
        half = len(checked) // 2
        trend = None
        if half >= 3:
            a = sum((d["variance"] for d in checked[:half]), ZERO) / half
            b = sum((d["variance"] for d in checked[half:]), ZERO) / (len(checked) - half)
            trend = "worse" if b < a - 5 else ("better" if b > a + 5 else "steady")
        out.append(dict(t, pct=pct, verdict=verdict, tolerance=tol, trend=trend, value=q2(t["value"])))
    return out


# ====================================================================== 2. tanker receipt checks
def receipt_checks(cur, start, end):
    """Each tanker receipt: invoice vs received litres, challan vs observed density; per-day and total shortage."""
    short_tol = _setting(cur, "auto_receipt_short_pct", "0.2")       # % of invoice
    dens_tol = _setting(cur, "auto_density_diff", "3")               # kg/m³
    cur.execute("""SELECT r.*, t.name AS tank_name, t.fuel_code FROM fuel_receipts r JOIN tanks t ON t.id=r.tank_id
                   WHERE NOT r.voided AND r.business_date BETWEEN %s AND %s ORDER BY r.business_date, r.id""", (start, end))
    out, tot = [], defaultdict(lambda: {"invoice": ZERO, "received": ZERO, "short": ZERO, "value": ZERO, "count": 0})
    for r in cur.fetchall():
        inv, rec = Decimal(r["invoice_qty_l"] or 0), Decimal(r["received_qty_l"] or 0)
        short = inv - rec if inv else None
        cost = (Decimal(r["invoice_amount"]) / inv) if inv and r["invoice_amount"] else None
        flags = []
        if not inv:
            flags.append("invoice litres not entered")
        elif short > inv * short_tol / 100:
            flags.append(f"short {q2(short)} L ({q2(short / inv * 100)} %)")
        if r["challan_density"] and r["observed_density"] and abs(Decimal(r["challan_density"]) - Decimal(r["observed_density"])) > dens_tol:
            flags.append(f"density differs by {q2(abs(Decimal(r['challan_density']) - Decimal(r['observed_density'])))} kg/m³")
        if not r["invoice_amount"]:
            flags.append("invoice amount not entered (needed for profit)")
        if r["dip_before_mm"] is None or r["dip_after_mm"] is None:
            flags.append("dip before/after not recorded")
        row = {"r": r, "short": short, "cost_per_l": cost.quantize(Decimal("0.0001")) if cost else None,
               "short_value": q2(short * cost) if (short and cost and short > 0) else ZERO, "flags": flags}
        out.append(row)
        t = tot[r["fuel_code"]]
        t["invoice"] += inv; t["received"] += rec; t["count"] += 1
        if short and short > 0:
            t["short"] += short; t["value"] += row["short_value"]
    return out, dict(tot)


# ====================================================================== 3. profit (exact, with issues)
def _avg_cost_before(cur, fuel, before, days=90):
    """Weighted average invoice cost per litre of the last `days` days of tankers before a date."""
    cur.execute("""SELECT SUM(r.invoice_amount) AS a, SUM(r.invoice_qty_l) AS q FROM fuel_receipts r
                   JOIN tanks t ON t.id=r.tank_id
                   WHERE NOT r.voided AND t.fuel_code=%s AND r.invoice_amount > 0 AND r.invoice_qty_l > 0
                     AND r.business_date < %s AND r.business_date >= %s""", (fuel, before, before - timedelta(days=days)))
    x = cur.fetchone()
    return (Decimal(x["a"]) / Decimal(x["q"])) if x and x["q"] else None


def _stock_on(cur, fuel, d):
    """Actual stock of a fuel at the end of day d (sum of the tanks' last dip on/before d)."""
    cur.execute("""SELECT SUM(v) AS v FROM (SELECT DISTINCT ON (k.tank_id) k.volume_l AS v FROM tank_dips k
                   JOIN tanks t ON t.id=k.tank_id WHERE t.fuel_code=%s AND t.active AND k.business_date <= %s
                   ORDER BY k.tank_id, k.business_date DESC) x""", (fuel, d))
    x = cur.fetchone()
    return Decimal(x["v"]) if x and x["v"] is not None else None


def profit(cur, start, end):
    """Profit for a period, weighted-average cost method.
    Fuel: cost/L = (opening stock × previous avg cost + tankers' invoice amounts) ÷ (opening litres + invoice litres).
          Gross margin = sales − litres sold × cost/L.  Stock loss/gain (tank variation) is valued at cost/L, shown apart.
    Lube: margin = sales − units sold × weighted average purchase rate (purchases up to the end date).
    Expenses and staff payments (salary, advance, bonus — cash paid) as entered."""
    issues = []
    cur.execute("SELECT code, name FROM fuels WHERE active ORDER BY sort")
    fuels = cur.fetchall()
    # sales from meter readings (litres × that day's price)
    cur.execute("""SELECT n.fuel_code AS f, SUM(r.closing - r.opening - r.testing) AS l,
                          SUM((r.closing - r.opening - r.testing) * r.rate) AS amt
                   FROM nozzle_readings r JOIN nozzles n ON n.id=r.nozzle_id
                   WHERE r.business_date BETWEEN %s AND %s GROUP BY 1""", (start, end))
    sales = {x["f"]: (Decimal(x["l"] or 0), Decimal(x["amt"] or 0)) for x in cur.fetchall()}
    var_by_fuel = defaultdict(lambda: ZERO)
    for v in tank_variation(cur, start, end):
        var_by_fuel[v["tank"]["fuel_code"]] += v["variance"]
    rows, fuel_total = [], {"sales": ZERO, "cost": ZERO, "margin": ZERO, "litres": ZERO, "stock_diff": ZERO}
    for f in fuels:
        code = f["code"]
        litres, amount = sales.get(code, (ZERO, ZERO))
        cur.execute("""SELECT COALESCE(SUM(r.invoice_qty_l),0) AS q, COALESCE(SUM(r.invoice_amount),0) AS a,
                              COUNT(*) FILTER (WHERE r.invoice_amount <= 0 OR r.invoice_qty_l <= 0) AS missing, COUNT(*) AS n
                       FROM fuel_receipts r JOIN tanks t ON t.id=r.tank_id
                       WHERE NOT r.voided AND t.fuel_code=%s AND r.business_date BETWEEN %s AND %s""", (code, start, end))
        p = cur.fetchone()
        if p["missing"]:
            issues.append(f"{code}: {p['missing']} tanker receipt(s) without invoice litres/amount")
        open_l = _stock_on(cur, code, start - timedelta(days=1))
        prev_cost = _avg_cost_before(cur, code, start)
        if open_l and prev_cost is None:
            issues.append(f"{code}: no earlier tanker invoice to value the opening stock")
        open_v = (open_l or ZERO) * (prev_cost or ZERO)
        buy_l = Decimal(p["q"])
        buy_v = Decimal(p["a"])
        denom = (open_l or ZERO) + buy_l
        cost_l = ((open_v + buy_v) / denom) if denom > 0 and (open_v + buy_v) > 0 else prev_cost
        if litres and cost_l is None:
            issues.append(f"{code}: sold {q2(litres)} L but no purchase cost is known")
        cogs = litres * (cost_l or ZERO)
        margin = amount - cogs
        sd = var_by_fuel.get(code, ZERO) * (cost_l or ZERO)
        if not litres and not buy_l:
            continue
        rows.append({"code": code, "name": f["name"], "litres": litres, "sales": q2(amount), "cost_per_l": q2(cost_l) if cost_l else None,
                     "cogs": q2(cogs), "margin": q2(margin), "margin_per_l": q2(margin / litres) if litres else None,
                     "bought_l": buy_l, "bought_value": q2(buy_v), "stock_diff_l": var_by_fuel.get(code, ZERO), "stock_diff_value": q2(sd)})
        fuel_total["sales"] += amount; fuel_total["cost"] += cogs; fuel_total["margin"] += margin
        fuel_total["litres"] += litres; fuel_total["stock_diff"] += sd
    # lube
    cur.execute("""SELECT m.product_id, p.name, SUM(-m.qty) AS q, SUM(m.amount) AS amt FROM lube_moves m
                   JOIN lube_products p ON p.id=m.product_id
                   WHERE NOT m.voided AND m.kind='sale' AND m.business_date BETWEEN %s AND %s GROUP BY 1, 2""", (start, end))
    lube_rows, lube = [], {"sales": ZERO, "cost": ZERO, "margin": ZERO}
    for x in cur.fetchall():
        cur.execute("""SELECT SUM(qty * rate) AS v, SUM(qty) AS q FROM lube_moves WHERE NOT voided AND kind IN ('purchase','opening')
                       AND product_id=%s AND rate > 0 AND business_date <= %s""", (x["product_id"], end))
        c = cur.fetchone()
        unit_cost = (Decimal(c["v"]) / Decimal(c["q"])) if c and c["q"] else None
        if unit_cost is None:
            issues.append(f"Lube “{x['name']}”: sold but no purchase rate recorded")
        cost = Decimal(x["q"] or 0) * (unit_cost or ZERO)
        lube_rows.append({"name": x["name"], "qty": Decimal(x["q"] or 0), "sales": q2(x["amt"]), "cost": q2(cost),
                          "margin": q2(Decimal(x["amt"] or 0) - cost), "unit_cost": q2(unit_cost) if unit_cost else None})
        lube["sales"] += Decimal(x["amt"] or 0); lube["cost"] += cost; lube["margin"] += Decimal(x["amt"] or 0) - cost
    # expenses + staff
    cur.execute("""SELECT category, SUM(amount) AS a FROM expenses WHERE NOT voided AND business_date BETWEEN %s AND %s
                   GROUP BY 1 ORDER BY 2 DESC""", (start, end))
    exp_rows = [{"category": x["category"], "amount": q2(x["a"])} for x in cur.fetchall()]
    cur.execute("""SELECT kind, SUM(amount) AS a FROM staff_payments WHERE NOT voided AND business_date BETWEEN %s AND %s
                   GROUP BY 1""", (start, end))
    staff = {x["kind"]: q2(x["a"]) for x in cur.fetchall()}
    expenses = sum((e["amount"] for e in exp_rows), ZERO)
    staff_total = sum(staff.values(), ZERO)
    # completeness: every day of the period closed?
    cur.execute("""SELECT g::date AS d FROM generate_series(%s::date, %s::date, interval '1 day') g
                   WHERE NOT EXISTS (SELECT 1 FROM days x WHERE x.business_date=g::date
                                     AND (x.status IN ('submitted','locked') OR x.is_legacy))""", (start, end))
    open_days = [x["d"] for x in cur.fetchall()]
    cur.execute("""SELECT COUNT(*) AS n FROM days x WHERE x.is_legacy AND x.business_date BETWEEN %s AND %s
                   AND NOT EXISTS (SELECT 1 FROM nozzle_readings r WHERE r.business_date=x.business_date)""", (start, end))
    legacy_n = cur.fetchone()["n"]
    if legacy_n:
        issues.append(f"{legacy_n} day(s) from the old app have no meter readings here — their sales are not in this profit")
    if open_days:
        issues.insert(0, f"{len(open_days)} day(s) not submitted: " + ", ".join(d.strftime("%d/%m") for d in open_days[:8])
                      + (" …" if len(open_days) > 8 else ""))
    gross = fuel_total["margin"] + lube["margin"]
    net = gross + fuel_total["stock_diff"] - expenses - staff_total
    return {"start": start, "end": end, "fuels": rows, "fuel": {k: q2(v) for k, v in fuel_total.items()},
            "lube_rows": lube_rows, "lube": {k: q2(v) for k, v in lube.items()}, "expenses": exp_rows, "expenses_total": q2(expenses),
            "staff": staff, "staff_total": q2(staff_total), "gross": q2(gross), "net": q2(net),
            "stock_diff": q2(fuel_total["stock_diff"]), "issues": issues, "final": not issues, "open_days": open_days}


# ====================================================================== 4. transporter credit score
def credit_scores(cur, today):
    """Score 0–100 per transporter from: days to pay (FIFO), oldest unpaid bill, use of credit limit, payment
    regularity (last 90 days). Grade A ≥ 80, B ≥ 60, C ≥ 40, D below. Suggested limit = 1.5 × average monthly
    credit of the last 90 days (rounded to ₹10,000)."""
    cur.execute("SELECT id, name, credit_limit FROM parties WHERE active ORDER BY name")
    out = []
    for p in cur.fetchall():
        cur.execute("""SELECT business_date AS d, debit, credit FROM party_txns WHERE party_id=%s AND NOT voided
                       ORDER BY business_date, id""", (p["id"],))
        txns = cur.fetchall()
        if not txns:
            continue
        # FIFO: payments settle the oldest bills first
        bills, waits = [], []
        for t in txns:
            if Decimal(t["debit"] or 0) > 0:
                bills.append([t["d"], Decimal(t["debit"])])
            pay = Decimal(t["credit"] or 0)
            while pay > 0 and bills:
                b = bills[0]
                use = min(pay, b[1])
                b[1] -= use; pay -= use
                if b[1] <= 0:
                    waits.append(((t["d"] - b[0]).days, use))
                    bills.pop(0)
                else:
                    waits.append(((t["d"] - b[0]).days, use))
        balance = sum((b[1] for b in bills), ZERO)
        oldest = (today - bills[0][0]).days if bills and balance > 0 else 0
        paid = sum((w for _, w in waits), ZERO)
        avg_days = (sum((Decimal(d) * w for d, w in waits), ZERO) / paid) if paid else None
        since = today - timedelta(days=90)
        credit_90 = sum((Decimal(t["debit"] or 0) for t in txns if t["d"] >= since), ZERO)
        pays_90 = [t for t in txns if t["d"] >= since and Decimal(t["credit"] or 0) > 0]
        limit = Decimal(p["credit_limit"] or 0)
        use_pct = (balance / limit * 100) if limit else None
        # points
        s_days = 40 if avg_days is None and balance == 0 else (40 if (avg_days or 0) <= 15 else 30 if avg_days <= 30 else 18 if avg_days <= 45 else 8 if avg_days <= 60 else 0)
        s_old = 25 if oldest <= 30 else 15 if oldest <= 45 else 6 if oldest <= 60 else 0
        s_lim = 20 if use_pct is None or use_pct <= 70 else 12 if use_pct <= 90 else 4 if use_pct <= 100 else 0
        s_reg = 15 if len(pays_90) >= 3 or (credit_90 == 0 and balance == 0) else 9 if len(pays_90) == 2 else 4 if len(pays_90) == 1 else 0
        score = s_days + s_old + s_lim + s_reg
        grade = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"
        suggested = (credit_90 / 3 * Decimal("1.5") / 10000).quantize(Decimal("1"), ROUND_HALF_UP) * 10000 if credit_90 else None
        out.append({"party": p, "balance": q2(balance), "oldest_days": oldest, "avg_days": q2(avg_days) if avg_days is not None else None,
                    "use_pct": q2(use_pct) if use_pct is not None else None, "payments_90": len(pays_90), "credit_90": q2(credit_90),
                    "score": score, "grade": grade, "suggested_limit": suggested,
                    "parts": {"days to pay": s_days, "oldest bill": s_old, "limit use": s_lim, "regular payments": s_reg}})
    out.sort(key=lambda x: (x["score"], -x["balance"]))
    return out


# ====================================================================== 5. this month vs last month
def month_compare(cur, today):
    """This month up to today vs the same days of last month (fair), plus the whole of last month."""
    from .services import range_summary
    m1 = today.replace(day=1)
    lm_end = m1 - timedelta(days=1)
    lm1 = lm_end.replace(day=1)
    same_end = min(lm1 + (today - m1), lm_end)

    def block(a, b):
        days = range_summary(cur, a, b)
        litres = defaultdict(lambda: ZERO)
        for r in days:
            for k, v in (r.get("qty") or {}).items():
                litres[k] += Decimal(v or 0)
        tot = lambda k: sum((Decimal(r.get(k) or 0) for r in days), ZERO)
        cur.execute("SELECT COALESCE(SUM(amount),0) AS a FROM expenses WHERE NOT voided AND business_date BETWEEN %s AND %s", (a, b))
        exp = Decimal(cur.fetchone()["a"])
        n = (b - a).days + 1
        return {"from": a, "to": b, "days": n, "sale": q2(tot("gross_sale")), "fuel": q2(tot("fuel_total")), "lube": q2(tot("lube_total")),
                "credit": q2(tot("credit")), "payments": q2(tot("payments")), "expenses": q2(exp),
                "litres": {k: q2(v) for k, v in litres.items()}, "per_day": q2(tot("gross_sale") / n) if n else ZERO}
    now, same, full = block(m1, today), block(lm1, same_end), block(lm1, lm_end)

    def pct(a, b):
        return None if not b else ((Decimal(a) - Decimal(b)) / Decimal(b) * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
    rows = []
    for key, label in (("sale", "Total sale"), ("fuel", "Fuel sale"), ("lube", "Lube sale"), ("credit", "Credit given"),
                       ("payments", "Payments received"), ("expenses", "Expenses"), ("per_day", "Average sale per day")):
        rows.append({"label": label, "now": now[key], "same": same[key], "full": full[key], "pct": pct(now[key], same[key]), "money": True})
    for code in sorted(set(now["litres"]) | set(same["litres"])):
        if code in ("MS", "HSD", "CNG"):
            rows.append({"label": f"{code} litres", "now": now["litres"].get(code, ZERO), "same": same["litres"].get(code, ZERO),
                         "full": full["litres"].get(code, ZERO), "pct": pct(now["litres"].get(code, 0), same["litres"].get(code, 0)), "money": False})
    return {"now": now, "same": same, "full": full, "rows": rows}
