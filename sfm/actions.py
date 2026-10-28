"""Actions shared by the website and the WhatsApp assistant (so both behave exactly the same)."""
from datetime import datetime, timedelta
from decimal import Decimal

from .security import audit, current_user
from .services import rates_for
from .util import IST, UserError, now_ist, today


def set_rates(cur, d, new_rates):
    """new_rates = {"HSD": Decimal, ...}. Same rules as Settings → Fuel Prices. Returns list of changes."""
    if d > today() + timedelta(days=7):
        raise UserError("Rates can be set at most 7 days ahead.")
    cur.execute("SELECT status FROM days WHERE business_date>=%s AND status='locked' LIMIT 1", (d,))
    if cur.fetchone():
        raise UserError("A locked day falls on or after that date. Rates for locked days cannot change.")
    cur.execute("SELECT code FROM fuels WHERE active")
    active = {r["code"] for r in cur.fetchall()}
    before = rates_for(cur, d)
    changed = []
    for code, rate in new_rates.items():
        code = code.upper()
        if code not in active:
            raise UserError(f"{code} is not an active fuel.")
        rate = Decimal(rate)
        if rate <= 0 or rate > 1000:
            raise UserError(f"{code} rate looks wrong.")
        if before.get(code) == rate:
            continue
        cur.execute("""INSERT INTO fuel_rates (fuel_code, effective_from, rate, created_by) VALUES (%s,%s,%s,%s)
                       ON CONFLICT (fuel_code, effective_from) DO UPDATE SET rate=EXCLUDED.rate,
                       created_by=EXCLUDED.created_by, created_at=NOW()""", (code, d, rate, current_user()["username"]))
        audit(cur, "update", "fuel_rate", code, f"{code} rate from {d}: ₹{before.get(code, '–')} → ₹{rate}", business_date=d)
        changed.append((code, before.get(code), rate))
    if changed:
        cur.execute("""
            UPDATE nozzle_readings r SET rate = x.new_rate
            FROM (SELECT r2.id, (SELECT fr.rate FROM fuel_rates fr
                                 WHERE fr.fuel_code = nz.fuel_code AND fr.effective_from <= r2.business_date
                                 ORDER BY fr.effective_from DESC LIMIT 1) AS new_rate
                  FROM nozzle_readings r2 JOIN nozzles nz ON nz.id = r2.nozzle_id
                  WHERE r2.business_date >= %s) x
            WHERE r.id = x.id AND x.new_rate IS NOT NULL AND r.rate <> x.new_rate""", (d,))
        if cur.rowcount:
            audit(cur, "update", "nozzle_reading", None, f"Re-priced {cur.rowcount} saved reading(s) from {d} after rate change",
                  business_date=d)
    return changed


def reopen_for(cur, user_id, d1, d2, hours, reason):
    """Temporary access (all editing on those dates) for one user, opening locked days; they re-lock when it ends."""
    if d2 < d1:
        d1, d2 = d2, d1
    if d2 > today():
        raise UserError("Dates cannot be in the future.")
    if (d2 - d1).days > 60:
        raise UserError("Choose 60 days or less.")
    from .grants import PRESETS
    perms = PRESETS["old_days"][1]
    until = now_ist() + timedelta(hours=max(1, min(72, int(hours))))
    cur.execute("UPDATE days SET status='open' WHERE business_date BETWEEN %s AND %s AND status='locked' RETURNING business_date", (d1, d2))
    reopened = sorted(r["business_date"] for r in cur.fetchall())
    cur.execute("""INSERT INTO access_grants (user_id, perms, date_from, date_to, valid_until, reason, reopened_days, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (user_id, ",".join(perms), d1, d2, until, reason, reopened, current_user()["username"]))
    gid = cur.fetchone()["id"]
    audit(cur, "grant", "access_grant", gid, f"Temporary access #{gid}: {d1:%d %b %Y}–{d2:%d %b %Y} until {until:%d %b %I:%M %p}; "
          f"{len(reopened)} locked day(s) opened — {reason}")
    return {"id": gid, "until": until, "reopened": reopened}


def lube_purchase(cur, product_id, qty, rate, d=None):
    """Lube arrived (purchase) with its purchase rate (needed for exact profit)."""
    d = d or today()
    qty, rate = Decimal(qty), Decimal(rate)
    if qty <= 0 or rate <= 0:
        raise UserError("Quantity and purchase rate must be more than zero.")
    cur.execute("SELECT name FROM lube_products WHERE id=%s AND active", (product_id,))
    p = cur.fetchone()
    if not p:
        raise UserError("Product not found.")
    cur.execute("INSERT INTO days (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
    cur.execute("""INSERT INTO lube_moves (product_id, business_date, kind, qty, rate, amount, remarks, created_by)
                   VALUES (%s,%s,'purchase',%s,%s,%s,'via WhatsApp',%s)""",
                (product_id, d, qty, rate, (qty * rate).quantize(Decimal("0.01")), current_user()["username"]))
    audit(cur, "create", "lube_move", product_id, f"Lube arrived: {p['name']} +{qty} @ ₹{rate}", business_date=d)
    return p["name"]
