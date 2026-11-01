"""One-time import from the old app's tables (schema public) into pump.*.
Reads only; never changes or deletes old tables. Runs in ONE transaction."""
import json
import os
import threading
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import g
import psycopg2
import psycopg2.errors
from psycopg2.extras import execute_values

from . import db
from .security import audit
from .util import ZERO, UserError, money, today

LEGACY_TABLES = ["settings", "nozzle_master", "nozzle_entries", "daily_closing", "credit_transporters",
                 "transport_entries", "transporter_ledger", "lube_stock", "lube_transactions", "staff_master",
                 "attendance", "salary_payments", "tank_level", "fuel_receipts", "proof_register"]


def _cols(cur, table):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=%s""", (table,))
    return {r["column_name"] for r in cur.fetchall()}


def _rows(cur, table, order="id"):
    cols = _cols(cur, table)
    if not cols:
        return [], cols
    cur.execute(f'SELECT * FROM public."{table}" ORDER BY {order if order in cols else "1"}')
    return cur.fetchall(), cols


def _d(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _n(v):
    if v is None or v == "":
        return ZERO
    try:
        x = Decimal(str(v))
        return money(x) if x.is_finite() else ZERO
    except (InvalidOperation, ValueError):
        return ZERO


def _s(v, lim=200):
    return ("" if v is None else str(v)).strip()[:lim]


class Batch:
    """Collects rows and sends them in pages of 500 — one network trip per page
    instead of one per row (the old way took 40,000 trips for a year of data)."""
    def __init__(self, cur, sql, template=None, page=500):
        self.cur, self.sql, self.template, self.page, self.rows = cur, sql, template, page, []

    def add(self, row):
        self.rows.append(row)
        if len(self.rows) >= self.page:
            self.flush()

    def flush(self):
        if self.rows:
            execute_values(self.cur, self.sql, self.rows, template=self.template, page_size=self.page)
            self.rows = []


def inspect_legacy():
    info = {"tables": {}, "done": None}
    with db.tx() as cur:
        cur.execute("SELECT value FROM settings WHERE key='legacy_imported_at'")
        r = cur.fetchone()
        info["done"] = r["value"] if r else None
        for t in LEGACY_TABLES:
            if _cols(cur, t):
                cur.execute(f'SELECT COUNT(*) AS n FROM public."{t}"')
                info["tables"][t] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM party_txns WHERE NOT is_legacy")
        info["new_entries"] = cur.fetchone()["n"]
    return info


def run_import(username, progress=lambda step, pct, **kw: None):
    rep = {"counts": {}, "issues": [], "balances": []}
    T = today()

    def count(k, n=1):
        rep["counts"][k] = rep["counts"].get(k, 0) + n

    with db.tx() as cur:
        # never wait forever behind another session (an abandoned earlier attempt, a stuck connection)
        cur.execute("SET LOCAL lock_timeout = '20s'; SET LOCAL statement_timeout = '300s'")
        cur.execute("SELECT pg_backend_pid() AS pid")
        progress("Reading old tables", 3, pid=cur.fetchone()["pid"])
        try:
            cur.execute("SELECT pg_advisory_xact_lock(990011)")
        except psycopg2.errors.LockNotAvailable:
            raise UserError("Another database session is still holding the import (probably an earlier attempt "
                            "that never finished). Use 'End stuck sessions' on this page, then start again.")
        progress("Reading old tables", 5)
        cur.execute("SELECT value FROM settings WHERE key='legacy_imported_at'")
        if cur.fetchone():
            raise UserError("Old data was already imported once. It cannot be imported twice.")
        g.user = {"username": username, "role": "owner"}
        legacy_dates = set()
        legacy_totals = {}

        # ---------------- settings + rates
        rows, cols = _rows(cur, "settings")
        old_rates = {}
        if rows:
            s = rows[0]
            for old, new in (("station_name", "station_name"), ("station_address", "station_address"),
                             ("phone_number", "station_phone"), ("gstin", "station_gstin")):
                if old in cols and _s(s.get(old)):
                    cur.execute("UPDATE settings SET value=%s WHERE key=%s", (_s(s[old]), new))
            for code, col in (("MS", "ms_rate"), ("HSD", "hsd_rate"), ("CNG", "cng_rate")):
                if col in cols and _n(s.get(col)) > 0:
                    old_rates[code] = _n(s[col])

        # earliest date in old data, for rate history and openings
        earliest = T
        for tbl, col in (("daily_closing", "date"), ("nozzle_entries", "entry_date"),
                         ("transport_entries", "entry_date"), ("transporter_ledger", "date")):
            if col in _cols(cur, tbl):
                cur.execute(f'SELECT MIN({col}) AS m FROM public."{tbl}"')
                m = _d(cur.fetchone()["m"])
                if m and m < earliest:
                    earliest = m
        for code, rate in old_rates.items():
            cur.execute("SELECT 1 FROM fuel_rates WHERE fuel_code=%s LIMIT 1", (code,))
            if not cur.fetchone():
                cur.execute("INSERT INTO fuel_rates (fuel_code, effective_from, rate, created_by) VALUES (%s,%s,%s,%s)",
                            (code, earliest, rate, "import"))
                count("fuel rates")

        # ---------------- tanks
        tank_by_fuel = {}
        cap = {}
        tl, tlc = _rows(cur, "tank_level")
        for r in tl:
            ft = _s(r.get("fuel_type")).upper()
            if "capacity" in tlc and _n(r.get("capacity")) > 0:
                cap[ft] = _n(r["capacity"])
        for code in ("MS", "HSD"):
            cur.execute("SELECT id FROM tanks WHERE fuel_code=%s ORDER BY id LIMIT 1", (code,))
            t = cur.fetchone()
            if not t:
                cur.execute("INSERT INTO tanks (name, fuel_code, capacity_l) VALUES (%s,%s,%s) RETURNING id",
                            (f"{code} tank 1", code, cap.get(code, ZERO)))
                t = cur.fetchone()
                count("tanks")
            tank_by_fuel[code] = t["id"]

        # ---------------- nozzles
        nozzle_map = {}
        rows, cols = _rows(cur, "nozzle_master")
        for r in rows:
            fuel = _s(r.get("fuel_type")).upper() or "MS"
            cur.execute("SELECT 1 FROM fuels WHERE code=%s", (fuel,))
            if not cur.fetchone():
                cur.execute("INSERT INTO fuels (code, name, sort) VALUES (%s,%s,9)", (fuel[:8], fuel))
            active = _s(r.get("status")).lower() not in ("inactive", "disabled", "closed")
            cur.execute("""INSERT INTO nozzles (name, machine_no, fuel_code, tank_id, active, legacy_id)
                           VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (_s(r.get("nozzle_name"), 40) or f"Nozzle {r['id']}", _s(r.get("machine_no"), 20), fuel[:8],
                         tank_by_fuel.get(fuel), active, r["id"]))
            nozzle_map[r["id"]] = (cur.fetchone()["id"], fuel)
            count("nozzles")

        # ---------------- nozzle readings (latest row per day+nozzle)
        rows, cols = _rows(cur, "nozzle_entries")
        latest = {}
        for r in rows:
            d = _d(r.get("entry_date"))
            if d and r.get("nozzle_id") in nozzle_map:
                latest[(d, r["nozzle_id"])] = r
        progress("Meter readings", 15)
        readings = Batch(cur, """INSERT INTO nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate,
                                 opening_override_reason, updated_by) VALUES %s""",
                         "(%s,%s,%s,%s,%s,%s,'Imported from old system','import')")
        for (d, old_nid), r in sorted(latest.items(), key=lambda x: x[0][0]):
            nid, fuel = nozzle_map[old_nid]
            op, cl, te = _n(r.get("opening_reading")), _n(r.get("closing_reading")), _n(r.get("testing_qty"))
            if cl < op:
                rep["issues"].append(f"Skipped reading {d} nozzle {old_nid}: closing {cl} < opening {op}")
                continue
            if te > cl - op:
                rep["issues"].append(f"Reading {d} nozzle {old_nid}: testing {te} > sale; testing set to 0")
                te = ZERO
            readings.add((d, nid, op, cl, te, old_rates.get(fuel, ZERO)))
            legacy_dates.add(d)
            count("meter readings")
        readings.flush()

        # ---------------- daily closing totals
        modes = {}
        cur.execute("SELECT id, name FROM collection_modes")
        for m in cur.fetchall():
            modes[m["name"]] = m["id"]
        mode_cols = {"phonepe": "PhonePe", "card_swipe": "Card swipe", "hp_pay": "HP Pay",
                     "hpcl_otp": "HPCL OTP / Fleet card", "upi_other": "Other UPI"}
        progress("Daily closings", 25)
        collections = {}
        rows, cols = _rows(cur, "daily_closing")
        for r in rows:
            d = _d(r.get("date"))
            if not d:
                continue
            legacy_totals[d] = {k: str(_n(r.get(k))) for k in (
                "ms_litres", "hsd_litres", "cng_sale", "total_fuel_sale", "lube_sale", "digital_collection",
                "phonepe", "card_swipe", "hp_pay", "hpcl_otp", "upi_other", "credit_given", "transport_received",
                "net_credit_due", "total_expense", "cash_in_hand") if k in cols}
            legacy_dates.add(d)
            for col, name in mode_cols.items():
                if col in cols and _n(r.get(col)) > 0 and name in modes:
                    collections[(d, modes[name])] = _n(r[col])
            count("daily closings")
        b = Batch(cur, """INSERT INTO day_collections (business_date, mode_id, amount, updated_by) VALUES %s
                          ON CONFLICT (business_date, mode_id) DO UPDATE SET amount=EXCLUDED.amount""",
                  "(%s,%s,%s,'import')")
        for (d, mid), amt in collections.items():
            b.add((d, mid, amt))
        b.flush()

        # ---------------- parties
        party_map, party_by_name, old_balance = {}, {}, {}
        rows, cols = _rows(cur, "credit_transporters")
        cur.execute("SELECT LOWER(TRIM(name)) AS n FROM parties")
        taken = {r["n"] for r in cur.fetchall()}
        prows, meta = [], {}
        for r in rows:
            name = _s(r.get("party_name"), 100) or f"Party {r['id']}"
            base, k = name, 2
            while name.strip().lower() in taken:
                name = f"{base} ({k})"
                k += 1
            taken.add(name.strip().lower())
            if name != base:
                rep["issues"].append(f"Duplicate party name '{base}' imported as '{name}'")
            mobile = "".join(c for c in _s(r.get("mobile")) if c.isdigit())[-10:]
            prows.append((name, mobile, _s(r.get("vehicle_no"), 400).upper(),
                          _s(r.get("status")).lower() != "inactive", r["id"], max(_n(r.get("discount")), ZERO)))
            meta[r["id"]] = (name, base, _n(r.get("balance_due")),
                             _n(r.get("opening_balance")) if "opening_balance" in cols else ZERO)
        inserted = execute_values(cur, """INSERT INTO parties (name, mobile, vehicles, active, legacy_id, discount_per_litre)
                                          VALUES %s RETURNING id, legacy_id""", prows, page_size=500, fetch=True) if prows else []
        openings = Batch(cur, """INSERT INTO party_txns (party_id, business_date, kind, debit, credit, remarks,
                                 created_by, is_legacy) VALUES %s""",
                         "(%s,%s,'opening',%s,%s,'Opening balance (old system)','import',TRUE)")
        for row in inserted:
            pid, lid = row["id"], row["legacy_id"]
            name, base, bal, ob = meta[lid]
            party_map[lid] = pid
            party_by_name.setdefault(base.lower(), pid)
            old_balance[pid] = (name, bal)
            if ob:
                openings.add((pid, earliest, max(ob, ZERO), max(-ob, ZERO)))
            count("parties")
        openings.flush()

        def pid_for(r):
            if r.get("transporter_id") in party_map:
                return party_map[r["transporter_id"]]
            return party_by_name.get(_s(r.get("transporter_name")).lower())

        txns = Batch(cur, """INSERT INTO party_txns (party_id, business_date, kind, fuel_code, qty, rate, debit, credit,
                             pay_mode, vehicle_no, challan_no, slip_no, remarks, created_by, is_legacy,
                             cash_taken, sl_no, pay_for) VALUES %s""",
                     "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'import',TRUE,%s,%s,%s)")

        def add_txn(pid, d, kind, debit=ZERO, credit=ZERO, **kw):
            legacy_dates.add(d)
            txns.add((pid, d, kind, kw.get("fuel_code"), kw.get("qty", ZERO), kw.get("rate", ZERO), debit, credit,
                         kw.get("pay_mode"), _s(kw.get("vehicle_no"), 20).upper(), _s(kw.get("challan_no"), 30),
                         _s(kw.get("slip_no"), 30), _s(kw.get("remarks"), 200), kw.get("cash_taken", ZERO),
                         kw.get("sl_no"), kw.get("pay_for")))

        progress("Transport entries", 35)
        rows, cols = _rows(cur, "transport_entries")
        for r in rows:
            pid, d = pid_for(r), _d(r.get("entry_date"))
            if not pid or not d:
                rep["issues"].append(f"Skipped transport entry #{r['id']}: party or date missing")
                continue
            q = _n(r.get("hsd_qty")) or _n(r.get("qty"))
            rate = _n(r.get("rate"))
            cash = _n(r.get("cash_taken"))
            fuel_amt = _n(r.get("hsd_amount")) or _n(r.get("fuel_amount")) or (_n(r.get("total_amount")) - cash)
            sl = int(r["sl_no"]) if str(r.get("sl_no") or "").isdigit() else None
            if fuel_amt > 0:
                add_txn(pid, d, "fuel", debit=fuel_amt + cash, cash_taken=cash, fuel_code="HSD", qty=q, rate=rate,
                        vehicle_no=r.get("vehicle_no"), challan_no=r.get("challan_no"), slip_no=r.get("slip_no"),
                        sl_no=sl, remarks=f"Old entry #{r['id']}")
                count("transport register entries")
            elif cash > 0:
                add_txn(pid, d, "cash_advance", debit=cash, vehicle_no=r.get("vehicle_no"), sl_no=sl,
                        challan_no=r.get("challan_no"), slip_no=r.get("slip_no"), remarks=f"Cash taken, old entry #{r['id']}")
                count("transport register entries")

        progress("Transporter ledger", 50)
        rows, cols = _rows(cur, "transporter_ledger")
        for r in rows:
            et = _s(r.get("entry_type"))
            pid, d = pid_for(r), _d(r.get("date"))
            if not pid or not d:
                continue
            fc, lc, rec = _n(r.get("fuel_credit")), _n(r.get("lube_credit")), _n(r.get("received_amount"))
            if et == "Fuel Credit" and fc > 0:
                add_txn(pid, d, "fuel", debit=fc, remarks=_s(r.get("remarks")) or "Fuel credit (daily closing)")
                count("credit entries")
            elif et == "Lube Credit" and lc > 0:
                add_txn(pid, d, "lube", debit=lc, remarks=_s(r.get("remarks")) or "Lube credit")
                count("credit entries")
            elif rec > 0 and "Received" in et:
                add_txn(pid, d, "payment", credit=rec, pay_mode="Cash",
                        pay_for="Lube" if "Lube" in et else ("Fuel" if "Fuel" in et else None),
                        remarks=f"{et} (old system; mode not recorded)")
                count("payments")
            elif rec < 0 or fc < 0 or lc < 0:
                if et != "Entry Deleted":
                    rep["issues"].append(f"Skipped ledger row #{r['id']} ({et}) with negative amount")

        txns.flush()
        progress("Checking balances", 60)
        cur.execute("SELECT party_id, COALESCE(SUM(debit-credit),0) AS b FROM party_txns WHERE NOT voided GROUP BY party_id")
        sums = {r["party_id"]: Decimal(r["b"]) for r in cur.fetchall()}
        for pid, (name, oldbal) in old_balance.items():
            computed = sums.get(pid, ZERO)
            diff = money(oldbal - computed)
            rep["balances"].append({"name": name, "old": oldbal, "computed": computed, "diff": diff})
            if diff != 0:
                add_txn(pid, T, "adjustment", debit=max(diff, ZERO), credit=max(-diff, ZERO),
                        remarks="Difference between old-system balance and its own entries, at import")
        txns.flush()

        # ---------------- lube (same rules: duplicate names combine their stock, negative stock → 0)
        rows, cols = _rows(cur, "lube_stock")
        cur.execute("SELECT id, LOWER(TRIM(name)) AS n FROM lube_products")
        existing = {r["n"]: r["id"] for r in cur.fetchall()}
        new_products, stock_by_name, first_name = [], {}, {}
        for r in rows:
            name = _s(r.get("product_name"), 80)
            if not name:
                continue
            key = name.strip().lower()
            if key in existing or key in first_name:
                rep["issues"].append(f"Lube '{name}' appeared twice; stock combined")
            else:
                first_name[key] = name
                new_products.append((name, _n(r.get("selling_rate")), r["id"]))
                count("lube products")
            stock = _n(r.get("closing_stock"))
            if stock < 0:
                rep["issues"].append(f"Lube '{name}' had negative stock {stock} in old system; imported as 0")
                stock = ZERO
            stock_by_name[key] = stock_by_name.get(key, ZERO) + stock
        if new_products:
            for row in execute_values(cur, "INSERT INTO lube_products (name, sale_rate, legacy_id) VALUES %s "
                                           "RETURNING id, LOWER(TRIM(name)) AS n", new_products, page_size=500, fetch=True):
                existing[row["n"]] = row["id"]
        moves = Batch(cur, "INSERT INTO lube_moves (product_id, business_date, kind, qty, remarks, created_by) VALUES %s",
                      "(%s,%s,'opening',%s,'Stock carried from old system','import')")
        for key, stock in stock_by_name.items():
            if stock and key in existing:
                moves.add((existing[key], T, stock))
        moves.flush()

        # ---------------- staff
        staff_by_name, staff_by_code = {}, {}
        rows, cols = _rows(cur, "staff_master")
        n = 0
        for r in rows:
            name = _s(r.get("staff_name"), 80)
            if not name or name.lower() in staff_by_name:
                continue
            code = _s(r.get("emp_id"), 20).upper()
            cur.execute("SELECT 1 FROM staff WHERE emp_code=%s", (code,))
            if not code or cur.fetchone():
                n += 1
                code = f"OLD{n:03d}"
            cur.execute("""INSERT INTO staff (emp_code, name, designation, joined_on, bank_account, active, department, shift)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (code, name, _s(r.get("role"), 40), _d(r.get("joined_date")), _s(r.get("bank_account"), 40),
                         _s(r.get("status")).lower() != "inactive", _s(r.get("department"), 40), _s(r.get("shift"), 20)))
            sid = cur.fetchone()["id"]
            staff_by_name[name.lower()] = sid
            if _s(r.get("emp_id")):
                staff_by_code[_s(r.get("emp_id")).upper()] = sid
            count("staff")
        status_map = {"present": "P", "absent": "A", "leave": "L", "half day": "H", "half": "H", "off": "O",
                      "weekly off": "O"}
        rows, cols = _rows(cur, "attendance")
        att = {}
        for r in rows:
            sid = staff_by_name.get(_s(r.get("staff_name")).lower())
            d = _d(r.get("date"))
            st = status_map.get(_s(r.get("attendance_status")).lower())
            if sid and d and st:
                att[(sid, d)] = st
        progress("Attendance and salary", 70)
        b = Batch(cur, "INSERT INTO attendance (staff_id, work_date, status, updated_by) VALUES %s", "(%s,%s,%s,'import')")
        for (sid, d), st in att.items():
            b.add((sid, d, st))
        b.flush()
        count("attendance marks", len(att))
        salaries = Batch(cur, """INSERT INTO staff_payments (staff_id, business_date, for_month, kind, amount, pay_mode,
                                 remarks, created_by) VALUES %s""", "(%s,%s,%s,'salary',%s,'Cash by owner',%s,'import')")
        rows, cols = _rows(cur, "salary_payments")
        for r in rows:
            sid = staff_by_code.get(_s(r.get("emp_id")).upper()) or staff_by_name.get(_s(r.get("employee_name")).lower())
            d = _d(r.get("payment_date"))
            amt = _n(r.get("amount"))
            if not sid or not d or amt <= 0:
                rep["issues"].append(f"Skipped salary payment #{r['id']}")
                continue
            ym = d.strftime("%Y-%m")
            mn = _s(r.get("month_name"))
            for fmt in ("%B %Y", "%b %Y", "%Y-%m", "%m-%Y", "%B-%Y"):
                try:
                    ym = datetime.strptime(mn, fmt).strftime("%Y-%m")
                    break
                except ValueError:
                    pass
            legacy_dates.add(d)
            salaries.add((sid, d, ym, amt, f"Old system: {_s(r.get('payment_mode'))} {_s(r.get('remarks'))}".strip()))
            count("salary payments")
        salaries.flush()

        # ---------------- tank dips + receipts
        dips = {}
        for r in tl:
            d = _d(r.get("date"))
            tid = tank_by_fuel.get(_s(r.get("fuel_type")).upper())
            if d and tid:
                dips[(d, tid)] = r
        progress("Tank dips and tanker receipts", 80)
        b = Batch(cur, """INSERT INTO tank_dips (business_date, tank_id, dip_mm, volume_l, water_mm, temperature_c,
                          density, source, updated_by) VALUES %s""", "(%s,%s,%s,%s,%s,%s,%s,'legacy','import')")
        for (d, tid), r in dips.items():
            vol = _n(r.get("current_stock"))
            if vol < 0:
                continue
            b.add((d, tid, _n(r.get("dip_mm")), vol, _n(r.get("water_dip_mm")),
                   _n(r.get("temperature_c")) or None, _n(r.get("density_kg_m3")) or None))
            legacy_dates.add(d)
            count("tank dips")
        b.flush()
        receipts = Batch(cur, """INSERT INTO fuel_receipts (business_date, tank_id, invoice_no, vehicle_no,
                             received_qty_l, dip_before_mm, dip_after_mm, challan_density, observed_density,
                             temperature_c, remarks, created_by, order_no, carrier_no, carrier_name, po_no, po_date,
                             water_checked, compartments, receipt_group, dip_variance_mm) VALUES %s""",
                         "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Imported from old system','import',%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        rows, cols = _rows(cur, "fuel_receipts")
        for r in rows:
            d = _d(r.get("receipt_date"))
            if not d:
                continue
            comps = []
            for i in range(1, 6):
                if _n(r.get(f"comp{i}_vol")) > 0:
                    comps.append({"no": i, "fuel": _s(r.get(f"comp{i}_fuel")).upper() or "HSD",
                                  "volume_l": str(_n(r.get(f"comp{i}_vol"))),
                                  "challan_dip_mm": str(_n(r.get(f"comp{i}_challan_dip")) or ""),
                                  "observed_dip_mm": str(_n(r.get(f"comp{i}_dip")) or "")})
            group = f"old{r['id']}"
            for code, col in (("MS", "total_ms_vol"), ("HSD", "total_hsd_vol")):
                vol = _n(r.get(col))
                if vol > 0:
                    receipts.add((d, tank_by_fuel[code], _s(r.get("invoice_no"), 40), _s(r.get("vehicle_no"), 20),
                                 vol, _n(r.get("dip_before")) or None, _n(r.get("dip_after")) or None,
                                 _n(r.get("challan_density")) or None, _n(r.get("density")) or None,
                                 _n(r.get("temperature_c")) or None, _s(r.get("order_no"), 40), _s(r.get("carrier_no"), 40),
                                 _s(r.get("carrier_name"), 80), _s(r.get("po_no"), 40), _d(r.get("po_date")),
                                 _s(r.get("water_checked"), 20),
                                 json.dumps([c for c in comps if c["fuel"] == code]) if comps else None, group,
                                 _n(r.get("total_dip_variance")) if r.get("total_dip_variance") not in (None, "") else None))
                    legacy_dates.add(d)
                    count("tanker receipts")
        receipts.flush()

        # ---------------- proofs  (the old activity log is NOT imported, by owner's choice)
        proofs = Batch(cur, """INSERT INTO proofs (business_date, category, note, file_url, latitude, longitude, created_by,
                               video_url, fuel_code, stock_status, proof_time) VALUES %s""",
                       "(%s,%s,%s,%s,%s,%s,'import',%s,%s,%s,%s)")
        rows, cols = _rows(cur, "proof_register")
        for r in rows:
            d = _d(r.get("proof_date"))
            url = _s(r.get("photo_url"), 500)
            vurl = _s(r.get("video_url"), 500)
            if d and (url or vurl):
                proofs.add((d, _s(r.get("proof_category"), 40) or "General",
                             " ".join(x for x in [_s(r.get("item_name")), _s(r.get("fuel_type")), _s(r.get("stock_status")),
                                                  _s(r.get("remarks"))] if x)[:300],
                             url, _s(r.get("latitude"), 20) or None, _s(r.get("longitude"), 20) or None,
                             vurl, _s(r.get("fuel_type"), 8).upper() or None, _s(r.get("stock_status"), 20),
                             _s(r.get("proof_time"), 20) or _s(r.get("client_time"), 20)))
                count("proof photos")
        proofs.flush()


        # ---------------- days: everything before today becomes read-only history
        progress("Locking old days as history", 96)
        days_b = Batch(cur, """INSERT INTO days (business_date, status, is_legacy, legacy_totals, locked_by, locked_at)
                               VALUES %s ON CONFLICT (business_date) DO UPDATE SET status=EXCLUDED.status,
                               is_legacy=EXCLUDED.is_legacy, legacy_totals=EXCLUDED.legacy_totals""")
        for d in sorted(legacy_dates):
            locked = d != T
            days_b.add((d, "locked" if locked else "open", locked,
                         json.dumps(legacy_totals.get(d)) if d in legacy_totals else None,
                         "import" if locked else None, datetime.now() if locked else None))
        days_b.flush()
        count("days of history", len(legacy_dates))

        cur.execute("INSERT INTO settings (key, value) VALUES ('legacy_imported_at', %s)",
                    (datetime.now().strftime("%d %b %Y %I:%M %p"),))
        progress("Saving", 99)
        audit(cur, "import", "legacy", None,
              "Imported old data: " + ", ".join(f"{v} {k}" for k, v in rep["counts"].items()))
    return rep


# ====================================================================== background run with live progress
STATE_KEY = "legacy_import_state"
_start_lock = threading.Lock()


def _state_conn():
    import psycopg2
    dsn = os.environ["DATABASE_URL"]
    kw = {} if ("sslmode" in dsn or "/tmp" in dsn or "localhost" in dsn) else {"sslmode": "require"}
    c = psycopg2.connect(dsn, connect_timeout=15, **kw)
    c.autocommit = True
    return c


def _json_default(v):
    if isinstance(v, (Decimal, date, datetime)):
        return str(v)
    return str(v)


_state_db = {"conn": None}


def _state_cursor():
    c = _state_db["conn"]
    if c is None or c.closed:
        c = _state_db["conn"] = _state_conn()
    return c.cursor()


def write_state(**kw):
    """Progress lives in its own committed row (the import itself is one big transaction
    that nobody can see until it finishes)."""
    for attempt in (1, 2):
        try:
            cur = _state_cursor()
            cur.execute("SELECT value FROM pump.settings WHERE key=%s", (STATE_KEY,))
            r = cur.fetchone()
            st = json.loads(r[0]) if r and r[0] else {}
            st.update(kw)
            st["heartbeat"] = time.time()
            cur.execute("""INSERT INTO pump.settings (key, value) VALUES (%s, %s)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                        (STATE_KEY, json.dumps(st, default=_json_default)))
            return
        except Exception:
            try:
                _state_db["conn"].close()
            except Exception:
                pass
            _state_db["conn"] = None
            if attempt == 2:
                raise


def read_state():
    try:
        cur = _state_cursor()
        cur.execute("SELECT value FROM pump.settings WHERE key=%s", (STATE_KEY,))
        r = cur.fetchone()
        return json.loads(r[0]) if r and r[0] else None
    except Exception:
        _state_db["conn"] = None
        cur = _state_cursor()
        cur.execute("SELECT value FROM pump.settings WHERE key=%s", (STATE_KEY,))
        r = cur.fetchone()
        return json.loads(r[0]) if r and r[0] else None


STUCK_SQL = """
    SELECT a.pid, a.state, a.application_name, a.client_addr::text AS client,
           EXTRACT(EPOCH FROM (now() - a.xact_start))::int AS tx_seconds,
           EXTRACT(EPOCH FROM (now() - a.state_change))::int AS state_seconds,
           LEFT(regexp_replace(COALESCE(a.query, ''), '\\s+', ' ', 'g'), 140) AS query,
           EXISTS (SELECT 1 FROM pg_locks l WHERE l.pid = a.pid AND l.locktype = 'advisory'
                   AND l.objid IN (990011, 842211) AND l.granted) AS holds_import_lock
    FROM pg_stat_activity a
    WHERE a.datname = current_database() AND a.pid <> pg_backend_pid() AND a.usename = current_user
      AND a.xact_start IS NOT NULL
      AND (now() - a.xact_start > interval '60 seconds'
           OR EXISTS (SELECT 1 FROM pg_locks l WHERE l.pid = a.pid AND l.locktype = 'advisory'
                      AND l.objid IN (990011, 842211) AND l.granted))
    ORDER BY a.xact_start
"""


def stuck_sessions(exclude_pid=None):
    """Database sessions of this app with a transaction open for over a minute or holding the
    import lock — the usual reason an import 'hangs'."""
    try:
        cur = _state_cursor()
        cur.execute(STUCK_SQL)
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return [r for r in rows if r["pid"] != exclude_pid]
    except Exception as e:
        return [{"pid": None, "error": str(e)[:200]}]


def end_stuck_sessions(exclude_pid=None):
    ended = []
    for r in stuck_sessions(exclude_pid):
        if not r.get("pid"):
            continue
        cur = _state_cursor()
        cur.execute("SELECT pg_terminate_backend(%s)", (r["pid"],))
        if cur.fetchone()[0]:
            ended.append(r)
    return ended


def _wait_info(pid):
    """What the import's database session is doing right now (for the progress screen)."""
    try:
        cur = _state_cursor()
        cur.execute("""SELECT state, wait_event_type, wait_event, pg_blocking_pids(pid) AS blockers,
                              EXTRACT(EPOCH FROM (now() - state_change))::int AS secs
                       FROM pg_stat_activity WHERE pid=%s""", (pid,))
        r = cur.fetchone()
        if not r:
            return None
        state, wtype, wevent, blockers, secs = r
        if blockers:
            return f"waiting {secs}s for another database session (pid {', '.join(map(str, blockers))})"
        if wtype == "Lock":
            return f"waiting {secs}s for a database lock"
        return None
    except Exception:
        return None


def is_running(st=None):
    st = st if st is not None else read_state()
    # a worker restart kills the thread; after 5 minutes without a heartbeat we know it died
    return bool(st and st.get("state") == "running" and time.time() - float(st.get("heartbeat") or 0) < 300)


def start_background(app, username):
    with _start_lock:
        st = read_state()
        if is_running(st):
            raise UserError("The import is already running. Please wait for it to finish.")
        with db.tx() as cur:
            cur.execute("SELECT value FROM settings WHERE key='legacy_imported_at'")
            if cur.fetchone():
                raise UserError("Old data was already imported once. It cannot be imported twice.")
        write_state(state="running", step="Starting", pct=1, started_at=time.time(), error=None, report=None,
                    by=username)
        threading.Thread(target=_worker, args=(app, username), daemon=True, name="legacy-import").start()


def _worker(app, username):
    import logging
    log = logging.getLogger("sfm.import")
    t0 = time.time()
    info = {"pid": None, "step": "Starting", "pct": 1, "alive": True}

    def beat():
        # keeps the 'still alive' signal going even while one database step is slow,
        # and reports what the import is waiting on
        while info["alive"]:
            time.sleep(5)
            if not info["alive"]:
                break
            try:
                waiting = _wait_info(info["pid"]) if info["pid"] else None
                write_state(state="running", step=info["step"], pct=info["pct"], waiting=waiting, pid=info["pid"])
            except Exception:
                pass

    threading.Thread(target=beat, daemon=True, name="legacy-import-heartbeat").start()
    with app.app_context():
        try:
            def progress(step, pct, pid=None):
                info.update(step=step, pct=pct)
                if pid:
                    info["pid"] = pid
                log.info("IMPORT %s%% %s", pct, step)
                write_state(state="running", step=step, pct=pct, waiting=None, pid=info["pid"],
                            step_at=time.strftime("%H:%M:%S"))
            rep = run_import(username, progress=progress)
            rep["seconds"] = round(time.time() - t0, 1)
            info["alive"] = False
            write_state(state="done", step="Finished", pct=100, report=rep, waiting=None)
            log.info("IMPORT finished in %.1fs", rep["seconds"])
        except UserError as e:
            info["alive"] = False
            log.warning("IMPORT stopped: %s", e)
            write_state(state="failed", step=f"Stopped at: {info['step']}", error=str(e), waiting=None)
        except BaseException as e:  # nothing was saved: the whole import is one transaction
            info["alive"] = False
            log.exception("Legacy import failed")
            msg = str(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
            if isinstance(e, psycopg2.errors.LockNotAvailable):
                msg = ("Waited 20 seconds for another database session that is holding the same records. "
                       "Use 'End stuck sessions', then start again.")
            elif isinstance(e, psycopg2.errors.QueryCanceled):
                msg = "One database step took longer than 5 minutes and was cancelled. Try 'Import while I wait'."
            write_state(state="failed", step=f"Stopped at: {info['step']}", error=f"{type(e).__name__}: {msg}",
                        waiting=None)
        finally:
            info["alive"] = False


def run_inline(username):
    """'Import while I wait': same import, in the web request itself (no background job)."""
    t0 = time.time()
    rep = run_import(username)
    rep["seconds"] = round(time.time() - t0, 1)
    write_state(state="done", step="Finished", pct=100, report=rep, waiting=None)
    return rep
