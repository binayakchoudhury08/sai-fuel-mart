import os
import uuid
from datetime import timedelta
from decimal import Decimal

import requests
from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import db
from ..security import audit, can, claim_form_token, current_user, login_required
from ..services import (avg_daily_sale, compute_day, lube_stock, party_balances, range_summary,
                        tank_reconciliation)
from ..util import ZERO, UserError, clean, inr, now_ist, parse_date, today
from decimal import Decimal
from ..util import IST
from ..security import audit, login_required as _lr

bp = Blueprint("main", __name__)


@bp.route("/")
def home():
    return redirect(url_for("main.dashboard"))


@bp.route("/dashboard")
@login_required()
def dashboard():
    t0 = today()
    with db.tx() as cur:
        span = range_summary(cur, t0 - timedelta(days=13), t0)
        td = compute_day(cur, t0)
        cur.execute("""SELECT (SELECT COUNT(*) FROM staff WHERE active) AS staff,
                              (SELECT COUNT(*) FROM attendance a JOIN staff s ON s.id=a.staff_id
                                WHERE s.active AND a.work_date=%s AND a.status IN ('P','H')) AS present,
                              (SELECT COUNT(*) FROM party_txns WHERE business_date=%s AND NOT voided
                                AND kind IN ('fuel','cash_advance')) AS slips,
                              (SELECT COUNT(*) FROM proofs WHERE business_date=%s) AS proofs""", (t0, t0, t0))
        counts = cur.fetchone()
        parties = party_balances(cur, active_only=False)
        lube = [p for p in lube_stock(cur) if p["active"] and Decimal(p["stock"]) <= Decimal(p["reorder_level"])]
        from ..services import tank_series, avg_daily_sales
        two = tank_series(cur, t0 - timedelta(days=1), t0)      # yesterday + today in one go
        tanks = [r for r in two if r["date"] == t0]
        if all(r["actual"] is None for r in tanks):
            tanks = [r for r in two if r["date"] != t0]
        avgs = avg_daily_sales(cur, t0)
        days_left = {}
        for r in tanks:
            avg = avgs.get(r["tank"]["fuel_code"], ZERO)
            if r["actual"] is not None and avg > 0:
                days_left[r["tank"]["id"]] = (r["actual"] / avg).quantize(Decimal("0.1"))
        cur.execute("""SELECT business_date, status FROM days
                       WHERE business_date BETWEEN %s AND %s AND status <> 'locked' AND NOT is_legacy
                       ORDER BY business_date""", (t0 - timedelta(days=30), t0 - timedelta(days=1)))
        pending = cur.fetchall()
        cur.execute("SELECT (SELECT COUNT(*) FROM nozzles WHERE active) AS nz, (SELECT COUNT(*) FROM fuel_rates) AS fr")
        cnt = cur.fetchone()
        setup_nozzles, setup_rates = cnt["nz"], cnt["fr"]
    by_date = {r["date"]: r for r in span}
    # 7-day chart (amount bars + litres line), yesterday comparison, fuel mix
    week = []
    for i in range(6, -1, -1):
        dd = t0 - timedelta(days=i)
        r = by_date.get(dd)
        litres = sum((Decimal(v) for k, v in (r["qty"].items() if r else []) if k in ("MS", "HSD")), ZERO)
        week.append({"date": dd, "sale": r["gross_sale"] if r else ZERO, "litres": litres})
    wk_max = max((w["sale"] for w in week), default=ZERO) or Decimal(1)
    lt_max = max((w["litres"] for w in week), default=ZERO) or Decimal(1)
    yday = by_date.get(t0 - timedelta(days=1))
    def pct(a, b):
        return None if not b else ((Decimal(a) - Decimal(b)) / Decimal(b) * 100).quantize(Decimal("1"))
    litres_today = sum((Decimal(b["qty"]) for c, b in td["by_fuel"].items() if c in ("MS", "HSD")), ZERO)
    litres_y = sum((Decimal(v) for k, v in (yday["qty"].items() if yday else []) if k in ("MS", "HSD")), ZERO)
    kpi = {"sale_pct": pct(td["gross_sale"], yday["gross_sale"] if yday else 0),
           "litres": litres_today, "litres_pct": pct(litres_today, litres_y)}
    mix_src = td if td["gross_sale"] else None
    mix = []
    colors = {"HSD": "#1D5BEB", "MS": "#22C55E", "CNG": "#F5B301"}
    if mix_src:
        for c, b in mix_src["by_fuel"].items():
            if b["amount"]:
                mix.append((c, Decimal(b["amount"]), colors.get(c, "#EF4444")))
        if mix_src["lube_total"]:
            mix.append(("Lube", Decimal(mix_src["lube_total"]), "#86EFAC"))
    else:
        agg = {}
        for r in span[-7:]:
            for c, q in r["qty"].items():
                pass
        for r in span[-7:]:
            agg["Fuel"] = agg.get("Fuel", ZERO) + r["fuel_total"]
            agg["Lube"] = agg.get("Lube", ZERO) + r["lube_total"]
        mix = [(k, v, "#1D5BEB" if k == "Fuel" else "#2563EB") for k, v in agg.items() if v]
    mix_total = sum((m[1] for m in mix), ZERO)
    # transactions = credit slips + lube bills + payments received (what the system records one by one)
    with db.tx() as cur:
        cur.execute("""SELECT business_date AS d, COUNT(*) AS n, COALESCE(SUM(debit),0) AS amt
                       FROM party_txns WHERE NOT voided AND kind IN ('fuel','cash_advance') AND business_date IN (%s,%s)
                       GROUP BY 1""", (t0, t0 - timedelta(days=1)))
        slips = {r["d"]: r for r in cur.fetchall()}
        cur.execute("""SELECT business_date AS d, COUNT(*) AS n, COALESCE(SUM(amount),0) AS amt FROM lube_moves
                       WHERE NOT voided AND kind='sale' AND business_date IN (%s,%s) GROUP BY 1""", (t0, t0 - timedelta(days=1)))
        lubes = {r["d"]: r for r in cur.fetchall()}
        cur.execute("""SELECT business_date AS d, COUNT(*) AS n FROM party_txns WHERE NOT voided AND kind='payment'
                       AND business_date IN (%s,%s) GROUP BY 1""", (t0, t0 - timedelta(days=1)))
        pays = {r["d"]: r for r in cur.fetchall()}
    def tx(d):
        n = sum(x.get(d, {}).get("n", 0) for x in (slips, lubes, pays))
        amt = Decimal(slips.get(d, {}).get("amt", 0)) + Decimal(lubes.get(d, {}).get("amt", 0))
        bills = slips.get(d, {}).get("n", 0) + lubes.get(d, {}).get("n", 0)
        return n, (amt / bills).quantize(Decimal("1")) if bills else ZERO
    kpi["tx"], kpi["avg"] = tx(t0)
    ytx, yavg = tx(t0 - timedelta(days=1))
    kpi["tx_pct"], kpi["avg_pct"] = pct(kpi["tx"], ytx), pct(kpi["avg"], yavg)
    from ..automation import compute as _alerts
    from ..security import can as _can
    with db.tx() as cur:
        top_alerts = _alerts(cur, _can)[:5]
    chart = []
    for i in range(13, -1, -1):
        d = t0 - timedelta(days=i)
        r = by_date.get(d)
        chart.append({"date": d, "sale": r["gross_sale"] if r else ZERO,
                      "diff": r["difference"] if r else None, "status": r["status"] if r else None})
    peak = max((c["sale"] for c in chart), default=ZERO) or Decimal(1)
    month_rows = [r for r in span if r["date"].month == t0.month]
    dues = sorted((p for p in parties if Decimal(p["balance"]) > 0), key=lambda p: -Decimal(p["balance"]))
    total_due = sum((Decimal(p["balance"]) for p in dues), ZERO)
    diffs = [c["diff"] for c in chart[-7:] if c["diff"] is not None]
    # ---- v3.5 dashboard data
    with db.tx() as cur:
        span30 = range_summary(cur, t0 - timedelta(days=29), t0)
        cur.execute("""SELECT business_date AS d, COUNT(*) AS n FROM (
                         SELECT business_date FROM party_txns WHERE NOT voided AND kind IN ('fuel','cash_advance','payment') AND business_date >= %s
                         UNION ALL SELECT business_date FROM lube_moves WHERE NOT voided AND kind='sale' AND business_date >= %s) x GROUP BY 1""",
                    (t0 - timedelta(days=29), t0 - timedelta(days=29)))
        txd = {r["d"]: r["n"] for r in cur.fetchall()}
        cur.execute("""SELECT EXTRACT(HOUR FROM at AT TIME ZONE 'Asia/Kolkata')::int AS h, COUNT(*) AS n, COALESCE(SUM(amt),0) AS amt FROM (
                         SELECT created_at AS at, debit AS amt FROM party_txns WHERE NOT voided AND kind IN ('fuel','cash_advance') AND business_date=%s
                         UNION ALL SELECT created_at, amount FROM lube_moves WHERE NOT voided AND kind='sale' AND business_date=%s
                         UNION ALL SELECT updated_at, (closing-opening-testing)*rate FROM nozzle_readings WHERE business_date=%s) x GROUP BY 1""",
                    (t0, t0, t0))
        hours = {r["h"]: (Decimal(r["amt"]), r["n"]) for r in cur.fetchall()}
        cur.execute("""SELECT DISTINCT ON (fuel_code) fuel_code, rate, effective_from FROM fuel_rates WHERE effective_from <= %s
                       ORDER BY fuel_code, effective_from DESC""", (t0,))
        prices = cur.fetchall()
        # day-to-day work only — owner-level changes (users, permissions, access, settings) stay in the Audit Log
        cur.execute("""SELECT at, username, action, entity, summary FROM audit_log
                       WHERE entity IN ('party_txn','nozzle_reading','day','day_credit','lube_move','lube_stock','attendance',
                                        'proof','tank_dip','fuel_receipt','expense','staff_payment','collections')
                         AND action NOT IN ('api','sync','login','logout','legacy') ORDER BY id DESC LIMIT 5""")
        activity = cur.fetchall()
        cur.execute(f"SELECT * FROM ({SALES_SQL}) y ORDER BY at DESC NULLS LAST LIMIT 8", {"a": t0, "b": t0})
        latest = cur.fetchall()
        cur.execute("""SELECT s.name, s.designation, s.shift, a.status, a.captured_at, a.checkout_at, a.marked_via
                       FROM staff s LEFT JOIN attendance a ON a.staff_id=s.id AND a.work_date=%s WHERE s.active ORDER BY s.name LIMIT 6""", (t0,))
        att_today = cur.fetchall()
    bd30 = {r["date"]: r for r in span30}
    def series(n):
        out = []
        for i in range(n - 1, -1, -1):
            dd = t0 - timedelta(days=i)
            r = bd30.get(dd)
            out.append({"label": dd.strftime("%d %b"), "sale": float(r["gross_sale"]) if r else 0.0, "tx": txd.get(dd, 0),
                        "litres": float(sum((Decimal(v) for k, v in (r["qty"].items() if r else []) if k in ("MS", "HSD")), ZERO))})
        return out
    today_series = []
    for h in range(6, 23, 2):            # 2-hour buckets, 6 AM – 11 PM
        amt = float(hours.get(h, (ZERO, 0))[0] + hours.get(h + 1, (ZERO, 0))[0])
        n = hours.get(h, (ZERO, 0))[1] + hours.get(h + 1, (ZERO, 0))[1]
        today_series.append({"label": f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}", "sale": amt, "tx": n})
    charts = {"today": today_series, "7d": series(7), "30d": series(30)}
    spark7 = series(7)
    sparks = {"sale": [x["sale"] for x in spark7], "tx": [x["tx"] for x in spark7],
              "avg": [(x["sale"] / x["tx"]) if x["tx"] else 0 for x in spark7], "litres": [x["litres"] for x in spark7]}
    nozzle_cards = [{"name": r["nozzle"]["name"], "fuel": r["nozzle"]["fuel_code"], "rate": r["rate"],
                     "litres": r["sale_qty"] if r["saved"] else None,
                     "active": bool(r["saved"] and r["sale_qty"] and r["sale_qty"] > 0)}
                    for r in td["nozzles"] if r["nozzle"]["active"]][:4]
    return render_template("dashboard.html", td=td, counts=counts, week=week, wk_max=wk_max, lt_max=lt_max,
                           charts=charts, sparks=sparks, nozzle_cards=nozzle_cards, prices=prices, activity=activity,
                           latest=latest, att_today=att_today, IST=IST,
                           kpi=kpi, mix=mix, mix_total=mix_total, top_alerts=top_alerts, wx=_weather_cfg(), chart=chart, peak=peak, dues=dues[:6],
                           total_due=total_due, due_count=len(dues), lube=lube, tanks=tanks,
                           days_left=days_left, pending=pending, week_diff=sum(diffs, ZERO),
                           week_diff_days=len(diffs),
                           needs_setup=(setup_nozzles == 0 or setup_rates == 0))


@bp.route("/branding/banner")
@login_required()
def banner():
    """The owner's own station photo for the dashboard banner (private storage → short signed link)."""
    from flask import abort
    from .. import storage
    r = db.one("SELECT value FROM settings WHERE key='banner_photo_path'")
    if not r or not r["value"] or not storage.ready():
        abort(404)
    link = storage.signed_download(r["value"], 3600)
    if not link:
        abort(404)
    return redirect(link)


@bp.route("/branding/login")
def login_photo():
    """Sign-in page background photo (shown before sign-in, so no login needed; short signed link)."""
    from flask import abort
    from .. import storage
    r = db.one("SELECT value FROM settings WHERE key='login_photo_path'")
    if not r or not r["value"] or not storage.ready():
        abort(404)
    link = storage.signed_download(r["value"], 3600)
    if not link:
        abort(404)
    return redirect(link)


PHOTO_SLOTS = {"banner": ("banner_photo_path", "Dashboard banner photo"), "login": ("login_photo_path", "Sign-in page photo")}


@bp.route("/settings/banner", methods=["POST"])
@login_required("owner")
def banner_upload():
    import uuid as _u
    from .. import storage
    slot = request.form.get("slot") if request.form.get("slot") in PHOTO_SLOTS else "banner"
    key, label = PHOTO_SLOTS[slot]
    if request.form.get("action") == "remove":
        with db.tx() as cur:
            cur.execute("DELETE FROM settings WHERE key=%s", (key,))
            audit(cur, "update", "settings", slot, f"{label} removed")
        flash(f"{label} removed.", "success")
        return redirect(url_for("admin.settings") + "#photo")
    f = request.files.get("photo")
    if not f or not f.filename:
        raise UserError("Choose a photo.")
    data = f.read(4_000_001)
    if len(data) > 4_000_000:
        raise UserError("Photo is too big — use one under 4 MB.")
    kind = "jpg" if data[:3] == b"\xff\xd8\xff" else ("png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ("webp" if data[8:12] == b"WEBP" else None))
    if not kind:
        raise UserError("Use a JPG, PNG or WEBP photo.")
    if not storage.ready():
        raise UserError("Photo storage is not set up yet (Settings → Photo storage check).")
    path = f"branding/{slot}-{_u.uuid4().hex}.{kind}"
    storage.put(path, data, {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind])
    with db.tx() as cur:
        cur.execute("INSERT INTO settings (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, path))
        audit(cur, "update", "settings", slot, f"{label} uploaded")
    flash(f"{label} saved.", "success")
    return redirect(url_for("admin.settings") + "#photo")


def _weather_cfg():
    from ..services import settings_map
    with db.tx() as cur:
        st = settings_map(cur)
    return {"place": st.get("station_place", ""), "lat": st.get("station_lat", ""), "lng": st.get("station_lng", ""),
            "tagline": st.get("brand_tagline", ""), "photo": bool(st.get("banner_photo_path"))}


@bp.route("/nozzles")
@login_required()
def nozzles():
    from ..services import compute_day, fuels, rates_for
    t0 = today()
    with db.tx() as cur:
        t = compute_day(cur, t0)
        fl = fuels(cur)
        rates = rates_for(cur, t0)
    groups = []
    order = {"HSD": 0, "MS": 1, "CNG": 2}
    fl = sorted(fl, key=lambda f: order.get(f["code"], 9))
    for f in fl:
        rows = [r for r in t["nozzles"] if r["nozzle"]["fuel_code"] == f["code"] and r["nozzle"]["active"]]
        if rows:
            groups.append((f, rows))
    return render_template("nozzles.html", groups=groups, rates=rates, t=t)


# ------------------------------------------------------------------ alerts + automation
@bp.route("/alerts")
@login_required()
def alerts():
    from flask import session
    from ..automation import compute
    from ..security import can
    with db.tx() as cur:
        items = compute(cur, can)
    session["ac"], session["ac_at"] = len(items), __import__("time").time()
    return render_template("alerts.html", items=items)


@bp.route("/settings/automation", methods=["GET", "POST"])
@login_required("owner")
def automation_settings():
    import json
    from ..automation import RULES, daily_job, rules
    if request.method == "POST":
        if request.form.get("action") == "run":
            rep = daily_job(actor="owner (run now)")
            flash(f"Automation ran: {rep['access_ended']} access(es) ended, {len(rep['auto_locked'])} day(s) auto-locked.", "success")
            return redirect(url_for("main.automation_settings"))
        changes = []
        with db.tx() as cur:
            old = rules(cur)
            for k, label, d, unit in RULES:
                v = (request.form.get(k) or "").strip()
                try:
                    num = Decimal(v)
                    if num < 0 or num > 100000:
                        raise ValueError
                except Exception:
                    raise UserError(f"“{label}” must be a number.")
                if num != old[k]:
                    cur.execute("INSERT INTO settings (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (k, v))
                    changes.append(f"{label} {old[k]} → {v} {unit}")
            on = "1" if request.form.get("auto_lock_enabled") == "1" else "0"
            if (on == "1") != old["auto_lock_enabled"]:
                cur.execute("INSERT INTO settings (key,value) VALUES ('auto_lock_enabled',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (on,))
                changes.append("Auto-lock clean days " + ("ON" if on == "1" else "OFF"))
            if changes:
                audit(cur, "update", "automation", None, "; ".join(changes))
        flash("Automation rules saved." if changes else "Nothing changed.", "success" if changes else "info")
        return redirect(url_for("main.automation_settings"))
    with db.tx() as cur:
        R = rules(cur)
        cur.execute("SELECT value FROM settings WHERE key='automation_last_run'")
        r = cur.fetchone()
    last = json.loads(r["value"]) if r and r["value"] else None
    return render_template("admin/automation.html", R=R, RULES=RULES, last=last,
                           cron_ready=bool(os.environ.get("CRON_SECRET")))


@bp.route("/api/cron/daily")
def cron_daily():
    """Called by Vercel Cron every morning (vercel.json). Vercel sends: Authorization: Bearer <CRON_SECRET>."""
    import secrets as _s
    from flask import jsonify
    from ..automation import daily_job
    secret = os.environ.get("CRON_SECRET", "")
    sent = (request.headers.get("Authorization") or "").replace("Bearer ", "", 1)
    if not secret or not _s.compare_digest(sent, secret):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify({"ok": True, **daily_job()})


# ------------------------------------------------------------------ sales / transactions
SALES_SQL = """
SELECT * FROM (
  SELECT r.business_date AS d, r.updated_at AS at, n.name AS nozzle, n.fuel_code AS fuel,
         (r.closing - r.opening - r.testing) AS litres, ROUND((r.closing - r.opening - r.testing) * r.rate, 2) AS amount,
         'Counter sale' AS mode, 'meter' AS src
    FROM nozzle_readings r JOIN nozzles n ON n.id = r.nozzle_id
  UNION ALL
  SELECT t.business_date, t.created_at, COALESCE(NULLIF(t.vehicle_no,''), '—'), COALESCE(t.fuel_code, 'Cash'),
         CASE WHEN t.kind='fuel' THEN t.qty END, t.debit, 'Credit – ' || p.name, 'credit'
    FROM party_txns t JOIN parties p ON p.id = t.party_id
   WHERE NOT t.voided AND t.kind IN ('fuel','cash_advance')
  UNION ALL
  SELECT m.business_date, m.created_at, lp.name, 'Lube', -m.qty, m.amount, COALESCE(NULLIF(m.pay_mode,''),'Cash'), 'lube'
    FROM lube_moves m JOIN lube_products lp ON lp.id = m.product_id WHERE NOT m.voided AND m.kind = 'sale'
  UNION ALL
  SELECT c.business_date, c.updated_at, '—', '—', NULL, c.amount, cm.name, 'digital'
    FROM day_collections c JOIN collection_modes cm ON cm.id = c.mode_id WHERE c.amount <> 0
  UNION ALL
  SELECT t.business_date, t.created_at, '—', '—', NULL, t.credit, 'Payment in – ' || p.name || ' (' || COALESCE(t.pay_mode,'') || ')', 'payment'
    FROM party_txns t JOIN parties p ON p.id = t.party_id WHERE NOT t.voided AND t.kind = 'payment'
) x WHERE d BETWEEN %(a)s AND %(b)s
"""


@bp.route("/sales")
@login_required("closing_enter|reports")
def sales():
    a = parse_date(request.args.get("from") or today().isoformat())
    b = parse_date(request.args.get("to") or a.isoformat())
    if b < a:
        a, b = b, a
    fuel = request.args.get("fuel") or ""
    mode = request.args.get("mode") or ""
    where, prm = "", {"a": a, "b": b}
    if fuel:
        where += " AND fuel = %(fuel)s"
        prm["fuel"] = fuel
    if mode == "Credit":
        where += " AND src = 'credit'"
    elif mode == "Counter":
        where += " AND src = 'meter'"
    elif mode == "Payments":
        where += " AND src = 'payment'"
    elif mode:
        where += " AND mode = %(mode)s"
        prm["mode"] = mode
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    with db.tx() as cur:
        cur.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(litres) FILTER (WHERE src='meter'),0) AS l, "
                    f"COALESCE(SUM(amount) FILTER (WHERE src IN ('meter','lube')),0) AS s FROM ({SALES_SQL}) y WHERE TRUE {where}", prm)
        agg = cur.fetchone()
        sql = f"SELECT * FROM ({SALES_SQL}) y WHERE TRUE {where} ORDER BY d DESC, at DESC NULLS LAST"
        if request.args.get("format") == "xlsx":
            cur.execute(sql, prm)
            rows = cur.fetchall()
            from ..exports import workbook_bytes
            from ..storage import deliver
            data = [[r["d"], r["at"].astimezone(IST).strftime("%I:%M %p") if r["at"] else "", r["nozzle"], r["fuel"],
                     float(r["litres"]) if r["litres"] is not None else None, float(r["amount"] or 0), r["mode"]] for r in rows]
            return deliver(workbook_bytes([("Sales", ["Date", "Time", "Nozzle / item", "Fuel type", "Litres", "Amount", "Payment mode"],
                                            data, (5,))]), f"Sales {a:%d%m%y}-{b:%d%m%y}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        cur.execute(sql + " LIMIT 50 OFFSET %(off)s", dict(prm, off=(page - 1) * 50))
        rows = cur.fetchall()
        cur.execute("SELECT name FROM collection_modes WHERE active ORDER BY sort, id")
        modes = [r["name"] for r in cur.fetchall()]
    return render_template("sales.html", rows=rows, a=a, b=b, fuel=fuel, mode=mode, modes=modes, page=page,
                           pages=max(1, -(-agg["n"] // 50)), count=agg["n"], litres=agg["l"], total=agg["s"], IST=IST)


# ------------------------------------------------------------------ search (top bar)
@bp.route("/search")
@login_required()
def search():
    from ..security import can
    q = clean(request.args.get("q"), 60)
    res = []   # (group, title, detail, href)
    if len(q) >= 2:
        like = f"%{q}%"
        pages = [("Dashboard", "main.dashboard", ""), ("Daily Closing", "day.day_today", "closing_enter"),
                 ("Nozzle Management", "main.nozzles", ""), ("Tank Level", "tanks.index", "tanks|reports"),
                 ("Credit Transport", "transport.index", "transport_enter|bills"), ("Lube Stock", "lube.index", ""),
                 ("Attendance", "staff.index", "attendance_photo|staff_manage"), ("Reports", "reports.index", "reports"),
                 ("Digital Collection", "reports.digital", "reports"), ("Sales / Transactions", "main.sales", "closing_enter|reports"),
                 ("Party Accounts", "parties.index", "accounts_view"), ("Salary", "staff.salary", "salary"),
                 ("Photo Proofs", "proofs.index", ""), ("Alerts", "main.alerts", ""), ("Settings", "admin.settings", "setup"),
                 ("Audit Log", "admin.audit_log", "audit")]
        for title, ep, perm in pages:
            if q.lower() in title.lower() and (not perm or can(perm)):
                res.append(("Pages", title, "Open page", url_for(ep)))
        with db.tx() as cur:
            cur.execute("SELECT id, name, vehicles FROM parties WHERE name ILIKE %s OR vehicles ILIKE %s ORDER BY name LIMIT 8", (like, like))
            for r in cur.fetchall():
                href = url_for("parties.detail", pid=r["id"]) if can("accounts_view") else url_for("transport.index", party=r["id"])
                res.append(("Transporters / parties", r["name"], (r["vehicles"] or "")[:80], href))
            if can("transport_enter") or can("bills"):
                cur.execute("""SELECT t.business_date, t.party_id, p.name, t.vehicle_no, t.slip_no, t.challan_no, t.debit, t.sl_no
                               FROM party_txns t JOIN parties p ON p.id=t.party_id
                               WHERE NOT t.voided AND t.kind IN ('fuel','cash_advance')
                                 AND (t.vehicle_no ILIKE %s OR t.slip_no ILIKE %s OR t.challan_no ILIKE %s)
                               ORDER BY t.business_date DESC, t.id DESC LIMIT 15""", (like, like, like))
                for r in cur.fetchall():
                    res.append(("Credit slips", f"{r['name']} · SL {r['sl_no'] or '-'} · {inr(r['debit'], decimals=0)}",
                                f"{r['business_date']:%d %b %Y} · vehicle {r['vehicle_no'] or '-'} · slip {r['slip_no'] or '-'} · challan {r['challan_no'] or '-'}",
                                url_for("transport.index", d=r["business_date"].isoformat(), party=r["party_id"])))
            cur.execute("SELECT name, fuel_code FROM nozzles WHERE name ILIKE %s OR fuel_code ILIKE %s LIMIT 8", (like, like))
            for r in cur.fetchall():
                res.append(("Nozzles", r["name"], r["fuel_code"], url_for("main.nozzles")))
            cur.execute("SELECT name, fuel_code FROM tanks WHERE name ILIKE %s OR fuel_code ILIKE %s LIMIT 8", (like, like))
            for r in cur.fetchall():
                res.append(("Tanks", r["name"], r["fuel_code"], url_for("tanks.index")))
            if can("attendance_photo") or can("staff_manage"):
                cur.execute("SELECT name, designation FROM staff WHERE name ILIKE %s AND active LIMIT 8", (like,))
                for r in cur.fetchall():
                    res.append(("Staff", r["name"], r["designation"] or "Staff", url_for("staff.index")))
            cur.execute("SELECT name, sale_rate FROM lube_products WHERE name ILIKE %s AND active LIMIT 8", (like,))
            for r in cur.fetchall():
                res.append(("Lube products", r["name"], f"₹{r['sale_rate']}", url_for("lube.index")))
    groups = {}
    for g_, t, d_, h in res:
        groups.setdefault(g_, []).append((t, d_, h))
    return render_template("search.html", q=q, groups=groups)
