"""All money and stock maths lives here and runs on the server from stored
rows. The browser only ever shows previews; nothing it calculates is saved."""
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from .util import ZERO, money

# How a party payment affects the cash drawer
PAY_MODES = OrderedDict([
    ("Cash", "Cash received at counter"),
    ("UPI at pump", "UPI/QR at the pump (already inside today’s digital totals)"),
    ("Bank transfer", "Bank transfer / NEFT / RTGS (goes straight to bank)"),
    ("Cheque", "Cheque (goes to bank)"),
])
DRAWER_PAY_MODES = ("Cash", "UPI at pump")

EXPENSE_CATEGORIES = ["General", "Electricity", "Tea & snacks", "Repairs", "Transport",
                      "Stationery", "Bank charges", "Govt / licence", "Staff welfare", "Other"]

DENOMINATIONS = [500, 200, 100, 50, 20, 10, 5, 2, 1]

PARTY_KIND_LABEL = {
    "opening": "Opening balance", "fuel": "Fuel on credit", "lube": "Lube on credit",
    "cash_advance": "Cash given to driver", "payment": "Payment received",
    "discount": "Discount", "adjustment": "Adjustment",
}


# ------------------------------------------------------------------ settings
def settings_map(cur):
    cur.execute("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in cur.fetchall()}


def fuels(cur, active_only=True):
    cur.execute("SELECT * FROM fuels %s ORDER BY sort, code" % ("WHERE active" if active_only else ""))
    return cur.fetchall()


def rates_for(cur, d):
    """{fuel_code: Decimal rate} effective on date d (latest change on/before d)."""
    cur.execute("""
        SELECT DISTINCT ON (fuel_code) fuel_code, rate, effective_from
        FROM fuel_rates WHERE effective_from <= %s
        ORDER BY fuel_code, effective_from DESC
    """, (d,))
    return {r["fuel_code"]: Decimal(r["rate"]) for r in cur.fetchall()}


# ------------------------------------------------------------------ nozzles
def opening_readings(cur, d):
    """Last closing reading before d for every nozzle."""
    cur.execute("""
        SELECT DISTINCT ON (nozzle_id) nozzle_id, closing, business_date
        FROM nozzle_readings WHERE business_date < %s
        ORDER BY nozzle_id, business_date DESC
    """, (d,))
    return {r["nozzle_id"]: r for r in cur.fetchall()}


def nozzle_rows(cur, d):
    cur.execute("""
        SELECT n.*, f.name AS fuel_name, f.unit
        FROM nozzles n JOIN fuels f ON f.code = n.fuel_code
        WHERE n.active OR EXISTS (SELECT 1 FROM nozzle_readings r
                                  WHERE r.nozzle_id = n.id AND r.business_date = %s)
        ORDER BY f.sort, n.sort, n.machine_no, n.name
    """, (d,))
    nozzles = cur.fetchall()
    cur.execute("SELECT * FROM nozzle_readings WHERE business_date=%s", (d,))
    saved = {r["nozzle_id"]: r for r in cur.fetchall()}
    prev = opening_readings(cur, d)
    rates = rates_for(cur, d)
    out = []
    for n in nozzles:
        s = saved.get(n["id"])
        p = prev.get(n["id"])
        auto_open = Decimal(p["closing"]) if p else None
        row = {
            "nozzle": n,
            "auto_opening": auto_open,
            "auto_opening_date": p["business_date"] if p else None,
            "saved": s,
            "opening": Decimal(s["opening"]) if s else (auto_open if auto_open is not None else None),
            "closing": Decimal(s["closing"]) if s else None,
            "testing": Decimal(s["testing"]) if s else ZERO,
            "rate": Decimal(s["rate"]) if s else rates.get(n["fuel_code"]),
            "override_reason": s["opening_override_reason"] if s else None,
        }
        # an opening that no longer matches yesterday's closing is a red flag
        row["opening_mismatch"] = bool(s and auto_open is not None and Decimal(s["opening"]) != auto_open)
        if s:
            sale = Decimal(s["closing"]) - Decimal(s["opening"]) - Decimal(s["testing"])
            row["sale_qty"] = sale
            row["amount"] = money(sale * Decimal(s["rate"]))
        else:
            row["sale_qty"] = None
            row["amount"] = None
        out.append(row)
    return out


# ------------------------------------------------------------------ day totals
def compute_day(cur, d):
    """Everything the Daily Closing screen and reports need for one date."""
    noz = nozzle_rows(cur, d)
    by_fuel = OrderedDict()
    for f in fuels(cur, active_only=False):
        by_fuel[f["code"]] = {"name": f["name"], "unit": f["unit"], "qty": ZERO, "amount": ZERO,
                              "testing": ZERO}
    readings_done = 0
    for r in noz:
        if r["saved"]:
            readings_done += 1
            b = by_fuel[r["nozzle"]["fuel_code"]]
            b["qty"] += r["sale_qty"]
            b["amount"] += r["amount"]
            b["testing"] += r["testing"]
    fuel_total = sum((b["amount"] for b in by_fuel.values()), ZERO)

    # lube counter sales (cash + credit)
    cur.execute("""
        SELECT m.*, p.name AS product_name, p.unit, pt.party_id, pa.name AS party_name
        FROM lube_moves m JOIN lube_products p ON p.id = m.product_id
        LEFT JOIN party_txns pt ON pt.id = m.party_txn_id
        LEFT JOIN parties pa ON pa.id = pt.party_id
        WHERE m.business_date=%s AND m.kind='sale'
        ORDER BY m.id
    """, (d,))
    lube_sales = cur.fetchall()
    lube_cash = sum((Decimal(m["amount"]) for m in lube_sales
                     if not m["voided"] and m["pay_mode"] != "Credit"), ZERO)
    lube_credit = sum((Decimal(m["amount"]) for m in lube_sales
                       if not m["voided"] and m["pay_mode"] == "Credit"), ZERO)

    # digital collections
    cur.execute("""
        SELECT cm.id, cm.name, cm.active, COALESCE(dc.amount, 0) AS amount, dc.updated_by
        FROM collection_modes cm
        LEFT JOIN day_collections dc ON dc.mode_id = cm.id AND dc.business_date = %s
        WHERE cm.active OR dc.amount IS NOT NULL
        ORDER BY cm.sort, cm.name
    """, (d,))
    collections = cur.fetchall()
    digital_total = sum((Decimal(c["amount"]) for c in collections), ZERO)

    # party movements dated today
    cur.execute("""
        SELECT t.*, p.name AS party_name
        FROM party_txns t JOIN parties p ON p.id = t.party_id
        WHERE t.business_date=%s AND t.kind <> 'opening'
        ORDER BY t.sl_no NULLS LAST, t.id
    """, (d,))
    party_rows = cur.fetchall()
    live = [t for t in party_rows if not t["voided"]]
    # transport credit = full slip total (diesel + cash taken), exactly like the old daily closing credit figure
    credit_fuel = sum((Decimal(t["debit"]) for t in live if t["kind"] in ("fuel", "cash_advance")), ZERO)
    credit_lube = sum((Decimal(t["debit"]) for t in live if t["kind"] == "lube"), ZERO)
    cash_advance = ZERO  # cash taken is already inside credit_fuel (one line, as before)
    pay_by_mode = OrderedDict((m, ZERO) for m in PAY_MODES)
    for t in live:
        if t["kind"] == "payment":
            pay_by_mode[t["pay_mode"] or "Cash"] = pay_by_mode.get(t["pay_mode"] or "Cash", ZERO) + Decimal(t["credit"])
    payments_total = sum(pay_by_mode.values(), ZERO)
    payments_drawer = sum((pay_by_mode.get(m, ZERO) for m in DRAWER_PAY_MODES), ZERO)
    credit_fuel_qty = OrderedDict()
    for t in live:
        if t["kind"] == "fuel" and t["fuel_code"]:
            credit_fuel_qty[t["fuel_code"]] = credit_fuel_qty.get(t["fuel_code"], ZERO) + Decimal(t["qty"])

    # cash going out of the counter
    cur.execute("SELECT * FROM expenses WHERE business_date=%s ORDER BY id", (d,))
    expenses = cur.fetchall()
    exp_counter = sum((Decimal(e["amount"]) for e in expenses if not e["voided"] and e["from_counter"]), ZERO)
    exp_other = sum((Decimal(e["amount"]) for e in expenses if not e["voided"] and not e["from_counter"]), ZERO)
    cur.execute("""
        SELECT sp.*, s.name AS staff_name FROM staff_payments sp JOIN staff s ON s.id = sp.staff_id
        WHERE sp.business_date=%s ORDER BY sp.id
    """, (d,))
    staff_pay = cur.fetchall()
    staff_cash = sum((Decimal(s["amount"]) for s in staff_pay
                      if not s["voided"] and s["pay_mode"] == "Cash from counter"), ZERO)

    lube_total = lube_cash + lube_credit
    gross_sale = fuel_total + lube_total
    expected_cash = (gross_sale - credit_fuel - credit_lube - digital_total
                     + payments_drawer - cash_advance - exp_counter - staff_cash)

    cur.execute("SELECT * FROM days WHERE business_date=%s", (d,))
    day = cur.fetchone()
    counted = Decimal(day["counted_cash"]) if day and day["counted_cash"] is not None else None
    difference = (counted - expected_cash) if counted is not None else None

    warnings = []
    for r in noz:
        if r["nozzle"]["active"] and not r["saved"]:
            warnings.append(f"No closing reading for {r['nozzle']['name']}.")
        if r["opening_mismatch"]:
            warnings.append(f"{r['nozzle']['name']}: opening {r['opening']} does not match previous "
                            f"closing {r['auto_opening']}" +
                            (f" (reason: {r['override_reason']})" if r["override_reason"] else "."))
        if r["saved"] and r["rate"] is not None and r["rate"] == 0:
            warnings.append(f"{r['nozzle']['name']}: rate is ₹0. Set the fuel rate in Settings.")
    for code, q in credit_fuel_qty.items():
        sold = by_fuel.get(code, {}).get("qty", ZERO)
        if q > sold:
            warnings.append(f"Credit {code} ({q} {by_fuel[code]['unit']}) is more than the meter sale "
                            f"({sold}). Check readings or credit entries.")
    if digital_total > gross_sale + payments_drawer and gross_sale > 0:
        warnings.append("Digital collections are more than total sales. Check the figures.")

    return {
        "date": d, "day": day, "status": day["status"] if day else "open",
        "nozzles": noz, "readings_done": readings_done,
        "readings_needed": sum(1 for r in noz if r["nozzle"]["active"]),
        "by_fuel": by_fuel, "fuel_total": money(fuel_total),
        "lube_sales": lube_sales, "lube_cash": money(lube_cash), "lube_credit": money(lube_credit),
        "lube_total": money(lube_total), "gross_sale": money(gross_sale),
        "collections": collections, "digital_total": money(digital_total),
        "party_rows": party_rows, "credit_fuel": money(credit_fuel), "credit_lube": money(credit_lube),
        "credit_total": money(credit_fuel + credit_lube), "cash_advance": money(cash_advance),
        "pay_by_mode": pay_by_mode, "payments_total": money(payments_total),
        "payments_drawer": money(payments_drawer),
        "expenses": expenses, "exp_counter": money(exp_counter), "exp_other": money(exp_other),
        "staff_pay": staff_pay, "staff_cash": money(staff_cash),
        "expected_cash": money(expected_cash), "counted_cash": counted,
        "difference": money(difference) if difference is not None else None,
        "warnings": warnings,
    }


def snapshot_of(t):
    """A compact JSON-able summary frozen into days.snapshot on submit/lock."""
    return {
        "fuel": {k: {"qty": str(v["qty"]), "amount": str(money(v["amount"]))} for k, v in t["by_fuel"].items()},
        "fuel_total": str(t["fuel_total"]), "lube_total": str(t["lube_total"]),
        "gross_sale": str(t["gross_sale"]), "digital_total": str(t["digital_total"]),
        "credit_total": str(t["credit_total"]), "payments_total": str(t["payments_total"]),
        "cash_advance": str(t["cash_advance"]), "exp_counter": str(t["exp_counter"]),
        "staff_cash": str(t["staff_cash"]), "expected_cash": str(t["expected_cash"]),
        "counted_cash": str(t["counted_cash"]) if t["counted_cash"] is not None else None,
        "difference": str(t["difference"]) if t["difference"] is not None else None,
    }


# ------------------------------------------------------------------ parties
def party_balances(cur, active_only=False):
    cur.execute("""
        SELECT p.*,
               COALESCE(SUM(t.debit)  FILTER (WHERE NOT t.voided), 0) AS total_debit,
               COALESCE(SUM(t.credit) FILTER (WHERE NOT t.voided), 0) AS total_credit,
               COALESCE(SUM(t.debit - t.credit) FILTER (WHERE NOT t.voided), 0) AS balance,
               COALESCE(SUM(t.debit) FILTER (WHERE NOT t.voided AND t.kind IN ('fuel','cash_advance')), 0) AS fuel_credit,
               COALESCE(SUM(t.debit) FILTER (WHERE NOT t.voided AND t.kind='lube'), 0) AS lube_credit,
               COALESCE(SUM(t.credit) FILTER (WHERE NOT t.voided AND t.kind='payment'), 0) AS received,
               COALESCE(SUM(t.credit) FILTER (WHERE NOT t.voided AND t.kind='discount'), 0) AS discounts,
               COALESCE(SUM(t.debit) FILTER (WHERE NOT t.voided AND t.kind='opening'), 0)
                 - COALESCE(SUM(t.credit) FILTER (WHERE NOT t.voided AND t.kind='opening'), 0) AS opening_bal,
               MAX(t.business_date) FILTER (WHERE NOT t.voided AND t.kind='payment') AS last_payment,
               MAX(t.business_date) FILTER (WHERE NOT t.voided AND t.kind IN ('fuel','lube','cash_advance')) AS last_credit
        FROM parties p LEFT JOIN party_txns t ON t.party_id = p.id
        %s
        GROUP BY p.id ORDER BY p.name
    """ % ("WHERE p.active" if active_only else ""))
    return cur.fetchall()


def party_balance(cur, party_id, before=None):
    if before:
        cur.execute("""SELECT COALESCE(SUM(debit-credit),0) AS b FROM party_txns
                       WHERE party_id=%s AND NOT voided AND business_date < %s""", (party_id, before))
    else:
        cur.execute("""SELECT COALESCE(SUM(debit-credit),0) AS b FROM party_txns
                       WHERE party_id=%s AND NOT voided""", (party_id,))
    return Decimal(cur.fetchone()["b"])


def party_statement(cur, party_id, start=None, end=None, include_voided=True):
    """Rows with a running balance computed on the fly — so it is always
    right, even after edits and voids anywhere in the history."""
    opening = party_balance(cur, party_id, before=start) if start else ZERO
    sql = """SELECT t.*, f.unit, lp.name AS lube_name FROM party_txns t
             LEFT JOIN fuels f ON f.code = t.fuel_code
             LEFT JOIN lube_products lp ON lp.id = t.lube_product_id
             WHERE t.party_id=%s"""
    params = [party_id]
    if start:
        sql += " AND t.business_date >= %s"; params.append(start)
    if end:
        sql += " AND t.business_date <= %s"; params.append(end)
    if not include_voided:
        sql += " AND NOT t.voided"
    sql += " ORDER BY t.business_date, CASE WHEN t.kind='opening' THEN 0 ELSE 1 END, t.id"
    cur.execute(sql, params)
    rows = cur.fetchall()
    bal = opening
    out = []
    totals = {"debit": ZERO, "credit": ZERO, "fuel_qty": ZERO}
    for r in rows:
        r = dict(r)
        if not r["voided"]:
            bal += Decimal(r["debit"]) - Decimal(r["credit"])
            totals["debit"] += Decimal(r["debit"])
            totals["credit"] += Decimal(r["credit"])
            if r["kind"] == "fuel":
                totals["fuel_qty"] += Decimal(r["qty"])
        r["running"] = bal
        out.append(r)
    return {"opening": opening, "rows": out, "closing": bal, "totals": totals}


# ------------------------------------------------------------------ lube
def lube_stock(cur):
    cur.execute("""
        SELECT p.*, COALESCE(SUM(m.qty) FILTER (WHERE NOT m.voided), 0) AS stock,
               COALESCE(SUM(-m.qty) FILTER (WHERE NOT m.voided AND m.kind='sale'
                        AND m.business_date >= date_trunc('month', CURRENT_DATE)), 0) AS sold_month
        FROM lube_products p LEFT JOIN lube_moves m ON m.product_id = p.id
        GROUP BY p.id ORDER BY p.active DESC, p.name
    """)
    return cur.fetchall()


def product_stock(cur, product_id):
    cur.execute("SELECT COALESCE(SUM(qty),0) AS s FROM lube_moves WHERE product_id=%s AND NOT voided",
                (product_id,))
    return Decimal(cur.fetchone()["s"])


# ------------------------------------------------------------------ tanks
def tank_reconciliation(cur, d):
    """Book stock vs dip for each tank on date d."""
    cur.execute("""SELECT t.*, f.name AS fuel_name, f.unit FROM tanks t JOIN fuels f ON f.code=t.fuel_code
                   WHERE t.active ORDER BY f.sort, t.name""")
    tanks = cur.fetchall()
    cur.execute("SELECT * FROM tank_dips WHERE business_date=%s", (d,))
    dips = {r["tank_id"]: r for r in cur.fetchall()}
    cur.execute("""SELECT DISTINCT ON (tank_id) * FROM tank_dips WHERE business_date < %s
                   ORDER BY tank_id, business_date DESC""", (d,))
    prev = {r["tank_id"]: r for r in cur.fetchall()}
    cur.execute("""SELECT tank_id, SUM(received_qty_l) AS q FROM fuel_receipts
                   WHERE business_date=%s AND NOT voided GROUP BY tank_id""", (d,))
    recv = {r["tank_id"]: Decimal(r["q"]) for r in cur.fetchall()}
    # meter sales per tank: by explicit nozzle→tank link, else by fuel when that fuel has one tank
    tanks_per_fuel = {}
    for t in tanks:
        tanks_per_fuel.setdefault(t["fuel_code"], []).append(t["id"])
    cur.execute("""
        SELECT n.tank_id, n.fuel_code, SUM(r.closing - r.opening - r.testing) AS q
        FROM nozzle_readings r JOIN nozzles n ON n.id = r.nozzle_id
        WHERE r.business_date=%s GROUP BY n.tank_id, n.fuel_code
    """, (d,))
    sales = {}
    for r in cur.fetchall():
        tid = r["tank_id"]
        if tid is None and len(tanks_per_fuel.get(r["fuel_code"], [])) == 1:
            tid = tanks_per_fuel[r["fuel_code"]][0]
        if tid is not None:
            sales[tid] = sales.get(tid, ZERO) + Decimal(r["q"])
    out = []
    for t in tanks:
        p = prev.get(t["id"])
        dip = dips.get(t["id"])
        opening = Decimal(p["volume_l"]) if p else None
        received = recv.get(t["id"], ZERO)
        sold = sales.get(t["id"], ZERO)
        book = (opening + received - sold) if opening is not None else None
        actual = Decimal(dip["volume_l"]) if dip else None
        variance = (actual - book) if (actual is not None and book is not None) else None
        pct = (variance / sold * 100).quantize(Decimal("0.01"), ROUND_HALF_UP) if (variance is not None and sold) else None
        out.append({"tank": t, "opening": opening, "opening_date": p["business_date"] if p else None,
                    "received": received, "sold": sold, "book": book, "dip": dip, "actual": actual,
                    "variance": variance, "variance_pct": pct,
                    "fill_pct": (actual / Decimal(t["capacity_l"]) * 100).quantize(Decimal("1"))
                    if (actual is not None and t["capacity_l"]) else None})
    return out


def avg_daily_sale(cur, fuel_code, upto, days=7):
    cur.execute("""
        SELECT COALESCE(SUM(r.closing - r.opening - r.testing),0) AS q, COUNT(DISTINCT r.business_date) AS n
        FROM nozzle_readings r JOIN nozzles n ON n.id=r.nozzle_id
        WHERE n.fuel_code=%s AND r.business_date > %s AND r.business_date <= %s
    """, (fuel_code, upto - timedelta(days=days), upto))
    r = cur.fetchone()
    return (Decimal(r["q"]) / r["n"]) if r["n"] else ZERO


# ------------------------------------------------------------------ ranges (reports, charts)
def range_summary(cur, start, end):
    """Per-day totals for a date range using a few grouped queries.
    Same formula as compute_day; legacy (imported) days use their stored totals."""
    def by_date(sql, params=None):
        cur.execute(sql, params or (start, end))
        return {r["d"]: r for r in cur.fetchall()}

    fuel_rows = {}
    cur.execute("""
        SELECT r.business_date AS d, n.fuel_code, SUM(r.closing-r.opening-r.testing) AS q,
               SUM(ROUND((r.closing-r.opening-r.testing)*r.rate, 2)) AS a
        FROM nozzle_readings r JOIN nozzles n ON n.id=r.nozzle_id
        WHERE r.business_date BETWEEN %s AND %s GROUP BY r.business_date, n.fuel_code""", (start, end))
    for r in cur.fetchall():
        fuel_rows.setdefault(r["d"], {})[r["fuel_code"]] = (Decimal(r["q"]), Decimal(r["a"]))
    lube = by_date("""SELECT business_date AS d,
                        COALESCE(SUM(amount) FILTER (WHERE pay_mode<>'Credit' OR pay_mode IS NULL),0) AS cash,
                        COALESCE(SUM(amount) FILTER (WHERE pay_mode='Credit'),0) AS credit
                      FROM lube_moves WHERE kind='sale' AND NOT voided AND business_date BETWEEN %s AND %s
                      GROUP BY business_date""")
    digital = by_date("""SELECT business_date AS d, SUM(amount) AS a FROM day_collections
                         WHERE business_date BETWEEN %s AND %s GROUP BY business_date""")
    party = by_date("""SELECT business_date AS d,
            COALESCE(SUM(debit) FILTER (WHERE kind IN ('fuel','cash_advance')),0) AS credit_fuel,
            COALESCE(SUM(debit) FILTER (WHERE kind='lube'),0) AS credit_lube,
            0 AS cash_advance,
            COALESCE(SUM(credit) FILTER (WHERE kind='payment'),0) AS payments,
            COALESCE(SUM(credit) FILTER (WHERE kind='payment' AND pay_mode IN ('Cash','UPI at pump')),0) AS pay_drawer
            FROM party_txns WHERE NOT voided AND NOT is_legacy AND business_date BETWEEN %s AND %s
            GROUP BY business_date""")
    exp = by_date("""SELECT business_date AS d,
            COALESCE(SUM(amount) FILTER (WHERE from_counter),0) AS counter,
            COALESCE(SUM(amount),0) AS total
            FROM expenses WHERE NOT voided AND business_date BETWEEN %s AND %s GROUP BY business_date""")
    staffc = by_date("""SELECT business_date AS d, SUM(amount) AS a FROM staff_payments
            WHERE NOT voided AND pay_mode='Cash from counter' AND business_date BETWEEN %s AND %s
            GROUP BY business_date""")
    days = by_date("SELECT business_date AS d, * FROM days WHERE business_date BETWEEN %s AND %s")

    dates = sorted(set(fuel_rows) | set(lube) | set(digital) | set(party) | set(exp) | set(staffc) | set(days))
    out = []
    for d in dates:
        day = days.get(d)
        if day and day["is_legacy"] and day["legacy_totals"]:
            L = day["legacy_totals"]
            def g(k):
                try:
                    return money(Decimal(str(L.get(k) or 0)))
                except Exception:
                    return ZERO
            fuel_total = g("total_fuel_sale")
            row = {"date": d, "status": "legacy", "legacy": True,
                   "qty": {"MS": g("ms_litres"), "HSD": g("hsd_litres"), "CNG": g("cng_sale")},
                   "fuel_total": fuel_total, "lube_total": g("lube_sale"),
                   "gross_sale": fuel_total + g("lube_sale"), "digital": g("digital_collection"),
                   "credit": g("credit_given"), "payments": g("transport_received"),
                   "pay_drawer": g("transport_received"), "cash_advance": ZERO,
                   "expenses": g("total_expense"), "staff_cash": ZERO,
                   "expected_cash": g("cash_in_hand"), "counted": None, "difference": None}
            out.append(row)
            continue
        f = fuel_rows.get(d, {})
        fuel_total = sum((a for _, a in f.values()), ZERO)
        lc = Decimal(lube[d]["cash"]) if d in lube else ZERO
        lcr = Decimal(lube[d]["credit"]) if d in lube else ZERO
        dg = Decimal(digital[d]["a"]) if d in digital else ZERO
        p = party.get(d)
        cf = Decimal(p["credit_fuel"]) if p else ZERO
        cl = Decimal(p["credit_lube"]) if p else ZERO
        ca = Decimal(p["cash_advance"]) if p else ZERO
        pd = Decimal(p["pay_drawer"]) if p else ZERO
        pt = Decimal(p["payments"]) if p else ZERO
        ec = Decimal(exp[d]["counter"]) if d in exp else ZERO
        et = Decimal(exp[d]["total"]) if d in exp else ZERO
        sc = Decimal(staffc[d]["a"]) if d in staffc else ZERO
        gross = fuel_total + lc + lcr
        expected = gross - cf - cl - dg + pd - ca - ec - sc
        counted = Decimal(day["counted_cash"]) if day and day["counted_cash"] is not None else None
        out.append({"date": d, "status": day["status"] if day else "open", "legacy": False,
                    "qty": {k: q for k, (q, _) in f.items()}, "fuel_total": money(fuel_total),
                    "lube_total": money(lc + lcr), "gross_sale": money(gross), "digital": money(dg),
                    "credit": money(cf + cl), "payments": money(pt), "pay_drawer": money(pd),
                    "cash_advance": money(ca), "expenses": money(et), "staff_cash": money(sc),
                    "expected_cash": money(expected), "counted": counted,
                    "difference": money(counted - expected) if counted is not None else None})
    return out


# ------------------------------------------------------------------ credit transport register
def transport_register(cur, start, end, party_id=None, search=None, include_voided=True):
    """Rows exactly like the old Credit Transport register: one row per entry,
    diesel + cash taken together, numbered per day (SL No)."""
    sql = """SELECT t.*, p.name AS party_name, p.discount_per_litre,
                    (t.debit - t.cash_taken) AS fuel_amount, f.unit
             FROM party_txns t JOIN parties p ON p.id=t.party_id LEFT JOIN fuels f ON f.code=t.fuel_code
             WHERE t.kind IN ('fuel','cash_advance') AND t.business_date BETWEEN %s AND %s"""
    params = [start, end]
    if party_id:
        sql += " AND t.party_id=%s"; params.append(party_id)
    if not include_voided:
        sql += " AND NOT t.voided"
    if search:
        sql += """ AND (p.name ILIKE %s OR t.vehicle_no ILIKE %s OR t.challan_no ILIKE %s OR t.slip_no ILIKE %s
                   OR t.remarks ILIKE %s)"""
        params += [f"%{search}%"] * 5
    sql += " ORDER BY t.business_date, t.sl_no NULLS LAST, t.id"
    cur.execute(sql, params)
    rows = []
    for r in cur.fetchall():
        r = dict(r)
        if r["kind"] == "cash_advance":
            r["fuel_amount"] = ZERO
            r["cash"] = Decimal(r["debit"])
        else:
            r["cash"] = Decimal(r["cash_taken"])
        rows.append(r)
    return rows


def register_totals(rows):
    live = [r for r in rows if not r["voided"]]
    t = {"qty": sum((Decimal(r["qty"]) for r in live if r["kind"] == "fuel"), ZERO),
         "fuel_amount": sum((Decimal(r["fuel_amount"]) for r in live), ZERO),
         "cash": sum((r["cash"] for r in live), ZERO),
         "total": sum((Decimal(r["debit"]) for r in live), ZERO), "count": len(live)}
    return t


def next_sl_no(cur, d):
    """Per-day serial across all transporters, like the old register. Cancelled
    entries keep their number (a visible gap is part of the audit trail)."""
    cur.execute("""SELECT COALESCE(MAX(sl_no),0)+1 AS n FROM party_txns
                   WHERE business_date=%s AND kind IN ('fuel','cash_advance')""", (d,))
    return cur.fetchone()["n"]
