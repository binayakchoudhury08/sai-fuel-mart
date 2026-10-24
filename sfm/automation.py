"""Automation: live alerts (bell + Alerts page), owner-set rules, and the daily automatic job.

Everything here only READS the books, except the daily job, which can (if the owner switches it on)
lock days that are completely clean, and ends expired temporary access."""
import json
from datetime import timedelta
from decimal import Decimal

from . import db
from .util import ZERO, inr, now_ist, qty, today

RULES = [
    # key, label, default, unit
    ("auto_tank_low_pct", "Tank stock below", "20", "% of capacity"),
    ("auto_tank_days", "Tank stock lasts less than", "3", "days"),
    ("auto_atg_stale_hours", "ATG not synced for more than", "3", "hours"),
    ("auto_cash_short", "Cash short more than", "500", "₹ in a day"),
    ("auto_party_days", "No payment from a party with dues for", "30", "days"),
    ("auto_closing_grace", "Daily closing overdue after", "1", "day(s)"),
    ("auto_lock_after_days", "Auto-lock clean submitted days after", "2", "days"),
]


def rules(cur):
    cur.execute("SELECT key, value FROM settings WHERE key LIKE 'auto_%%'")
    st = {r["key"]: r["value"] for r in cur.fetchall()}
    out = {}
    for k, _, d, _ in RULES:
        try:
            out[k] = Decimal(st.get(k) or d)
        except Exception:
            out[k] = Decimal(d)
    out["auto_lock_enabled"] = st.get("auto_lock_enabled") == "1"
    return out


def compute(cur, can):
    """List of alerts the signed-in person may act on. can(perm) decides what they see."""
    from .services import avg_daily_sales, range_summary, tank_series
    R = rules(cur)
    t = today()
    A = []

    def add(sev, title, detail, link, area):
        A.append({"sev": sev, "title": title, "detail": detail, "link": link, "area": area})

    # ---- tanks
    if can("tanks") or can("reports") or can("closing_enter"):
        rows = tank_series(cur, t - timedelta(days=1), t)
        latest = {}
        for r in rows:
            if r["actual"] is not None:
                latest[r["tank"]["id"]] = r
        avgs = avg_daily_sales(cur, t)
        for r in latest.values():
            tk = r["tank"]
            if r["fill_pct"] is not None and r["fill_pct"] < R["auto_tank_low_pct"]:
                add("high", f"{tk['name']} low: {r['fill_pct']}%", f"{qty(r['actual'], 0)} L left of {qty(tk['capacity_l'], 0)} L. Order a tanker.", "/tanks", "Tanks")
            avg = avgs.get(tk["fuel_code"])
            if avg and r["actual"] / avg < R["auto_tank_days"]:
                add("medium", f"{tk['name']}: about {(r['actual'] / avg).quantize(Decimal('0.1'))} days of stock",
                    f"Average sale {qty(avg, 0)} L/day.", "/tanks", "Tanks")
            if r["receipt_mismatch"]:
                add("medium", f"{tk['name']}: tanker not recorded", f"ATG shows {qty(r['atg_receipt'])} L received; "
                    f"{qty(r['entered_receipt'])} L entered as tanker receipts.", "/tanks", "Tanks")
        cur.execute("""SELECT t.name, MAX(d.updated_at) AS last FROM tank_dips d JOIN tanks t ON t.id=d.tank_id
                       WHERE d.source='atg' AND t.active GROUP BY t.name""")
        for r in cur.fetchall():
            hrs = (now_ist() - r["last"]).total_seconds() / 3600
            if hrs > float(R["auto_atg_stale_hours"]):
                add("low", f"ATG not synced: {r['name']}", f"Last reading {r['last'].astimezone(now_ist().tzinfo):%d %b %I:%M %p}. "
                    "Click the bookmarklet on the ATG computer.", "/tanks", "Tanks")

    # ---- daily closing: overdue, credit mismatch, cash short, waiting for verification
    if can("closing_enter") or can("day_lock"):
        grace = int(R["auto_closing_grace"])
        cur.execute("SELECT MIN(business_date) AS m FROM days WHERE NOT is_legacy")
        started = cur.fetchone()["m"]
        if started:
            first = max(started, t - timedelta(days=10))
            last = t - timedelta(days=grace + 1)
            if last >= first:
                cur.execute("""SELECT d::date AS day FROM generate_series(%s::date, %s::date, interval '1 day') d
                               LEFT JOIN days dy ON dy.business_date=d::date
                               WHERE COALESCE(dy.status,'open') NOT IN ('submitted','locked')
                                 AND NOT COALESCE(dy.is_legacy, FALSE) ORDER BY d""", (first, last))
                for r in cur.fetchall():
                    add("high", f"Daily closing not submitted: {r['day']:%d %b}", "Finish the steps and submit the day.",
                        f"/day/{r['day'].isoformat()}", "Daily closing")
        cur.execute("""
            WITH reg AS (SELECT business_date, party_id, SUM(debit) AS s FROM party_txns
                         WHERE NOT voided AND kind IN ('fuel','cash_advance') AND business_date >= %s GROUP BY 1,2),
                 dec AS (SELECT business_date, party_id, amount AS s FROM day_credit WHERE business_date >= %s)
            SELECT COALESCE(reg.business_date, dec.business_date) AS d, p.name, reg.s AS slips, dec.s AS closing
            FROM reg FULL JOIN dec ON reg.business_date=dec.business_date AND reg.party_id=dec.party_id
            JOIN parties p ON p.id = COALESCE(reg.party_id, dec.party_id)
            WHERE COALESCE(reg.business_date, dec.business_date) IN (SELECT business_date FROM day_credit WHERE business_date >= %s)
              AND COALESCE(reg.s,0) <> COALESCE(dec.s,0)
            ORDER BY 1 DESC""", (t - timedelta(days=10),) * 3)
        for r in cur.fetchall():
            add("high", f"Credit mismatch {r['d']:%d %b}: {r['name']}",
                f"Closing {inr(r['closing'] or 0, decimals=0)} vs Credit transport {inr(r['slips'] or 0, decimals=0)}.",
                f"/day/{r['d'].isoformat()}#credit", "Daily closing")
        if can("reports") or can("day_lock"):
            for r in range_summary(cur, t - timedelta(days=7), t - timedelta(days=1)):
                if r["difference"] is not None and r["difference"] < -R["auto_cash_short"]:
                    add("high", f"Cash short {r['date']:%d %b}: {inr(-r['difference'], decimals=0)}",
                        "Counted cash is less than expected.", f"/day/{r['date'].isoformat()}#finish", "Cash")
        if can("day_lock"):
            cur.execute("""SELECT business_date FROM days WHERE status='submitted' AND NOT is_legacy
                           AND business_date >= %s ORDER BY business_date""", (t - timedelta(days=30),))
            days = [r["business_date"] for r in cur.fetchall()]
            if days:
                add("low", f"{len(days)} day(s) waiting for your verification",
                    ", ".join(d.strftime("%d %b") for d in days[:6]), f"/day/{days[0].isoformat()}#finish", "Daily closing")

    # ---- parties
    if can("accounts_view"):
        cur.execute("""
            SELECT p.id, p.name, p.credit_limit,
                   COALESCE(SUM(t.debit - t.credit) FILTER (WHERE NOT t.voided), 0) AS bal,
                   MAX(t.business_date) FILTER (WHERE NOT t.voided AND t.kind='payment') AS last_pay,
                   MIN(t.business_date) FILTER (WHERE NOT t.voided AND t.kind IN ('fuel','cash_advance','lube')) AS first_credit
            FROM parties p LEFT JOIN party_txns t ON t.party_id=p.id WHERE p.active GROUP BY p.id""")
        lim = int(R["auto_party_days"])
        for r in cur.fetchall():
            bal = Decimal(r["bal"])
            if r["credit_limit"] and bal > Decimal(r["credit_limit"]):
                add("high", f"{r['name']} over credit limit", f"Owes {inr(bal, decimals=0)} (limit {inr(r['credit_limit'], decimals=0)}).",
                    f"/parties/{r['id']}", "Parties")
            since = r["last_pay"] or r["first_credit"]
            if bal > 0 and since and (t - since).days > lim:
                add("medium", f"{r['name']}: no payment for {(t - since).days} days", f"Owes {inr(bal, decimals=0)}.",
                    f"/parties/{r['id']}", "Parties")

    # ---- lube
    if can("lube_stock") or can("closing_enter"):
        cur.execute("""SELECT p.name, COALESCE(SUM(m.qty) FILTER (WHERE NOT m.voided),0) AS s, p.reorder_level
                       FROM lube_products p LEFT JOIN lube_moves m ON m.product_id=p.id WHERE p.active
                       GROUP BY p.id HAVING COALESCE(SUM(m.qty) FILTER (WHERE NOT m.voided),0) <= p.reorder_level""")
        low = cur.fetchall()
        if low:
            add("low", f"{len(low)} lube product(s) to reorder", ", ".join(f"{r['name']} ({qty(r['s'], 0)})" for r in low[:5]),
                "/lube", "Lube")

    # ---- proofs
    if can("proofs") and now_ist().hour >= 12:
        cur.execute("SELECT COUNT(*) AS n FROM proofs WHERE business_date=%s AND category LIKE '%%Dip Check'", (t,))
        if cur.fetchone()["n"] == 0:
            add("low", "No dip-check photo today", "Take the MS and HSD dip proofs on the phone.", "/proofs/new", "Proofs")

    order = {"high": 0, "medium": 1, "low": 2}
    A.sort(key=lambda a: order[a["sev"]])
    return A


def daily_job(actor="automation"):
    """Runs once a day (Vercel Cron) or when the owner presses 'Run now'."""
    from flask import g
    from .grants import close_ended
    from .security import audit
    from .services import compute_day
    report = {"at": now_ist().strftime("%d %b %Y %I:%M %p"), "access_ended": 0, "auto_locked": []}
    report["access_ended"] = close_ended(force=True)
    g.user = {"username": actor, "role": "system"}
    with db.tx() as cur:
        R = rules(cur)
        if R["auto_lock_enabled"]:
            cur.execute("SELECT value FROM settings WHERE key='cash_tolerance'")
            r = cur.fetchone()
            tol = Decimal(r["value"]) if r and r["value"] else ZERO
            cut = today() - timedelta(days=int(R["auto_lock_after_days"]))
            cur.execute("""SELECT business_date FROM days WHERE status='submitted' AND NOT is_legacy
                           AND business_date <= %s ORDER BY business_date""", (cut,))
            for row in cur.fetchall():
                d = row["business_date"]
                t = compute_day(cur, d)
                clean = (t["readings_done"] == t["readings_needed"] and t["counted_cash"] is not None
                         and t["recon_ok"] and t["difference"] is not None and abs(t["difference"]) <= tol)
                if clean:
                    cur.execute("""UPDATE days SET status='locked', locked_by='auto (clean day)', locked_at=NOW()
                                   WHERE business_date=%s AND status='submitted'""", (d,))
                    audit(cur, "lock", "day", d.isoformat(), f"Auto-locked: readings complete, credit matched, "
                          f"cash difference {t['difference']} within ±{tol}", business_date=d)
                    report["auto_locked"].append(d.isoformat())
        cur.execute("""INSERT INTO settings (key, value) VALUES ('automation_last_run', %s)
                       ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""", (json.dumps(report),))
        audit(cur, "sync", "automation", None, f"Daily automation: {report['access_ended']} access(es) ended, "
              f"{len(report['auto_locked'])} day(s) auto-locked")
    return report
