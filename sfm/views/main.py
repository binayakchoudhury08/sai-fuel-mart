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
from ..util import ZERO, UserError, clean, now_ist, parse_date, today

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
    return render_template("dashboard.html", td=td, counts=counts, chart=chart, peak=peak, dues=dues[:6],
                           total_due=total_due, due_count=len(dues), lube=lube, tanks=tanks,
                           days_left=days_left, pending=pending, week_diff=sum(diffs, ZERO),
                           week_diff_days=len(diffs),
                           needs_setup=(setup_nozzles == 0 or setup_rates == 0))
