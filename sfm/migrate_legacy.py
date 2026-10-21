"""One-time import from the old app's tables (schema public) into pump.*.
Reads only; never changes or deletes old tables. Runs in ONE transaction."""
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import g

from . import db
from .security import audit
from .util import ZERO, UserError, money, today

LEGACY_TABLES = ["settings", "nozzle_master", "nozzle_entries", "daily_closing", "credit_transporters",
                 "transport_entries", "transporter_ledger", "lube_stock", "lube_transactions", "staff_master",
                 "attendance", "salary_payments", "tank_level", "fuel_receipts", "proof_register", "activity_log"]


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


def run_import(username):
    rep = {"counts": {}, "issues": [], "balances": []}
    T = today()

    def count(k, n=1):
        rep["counts"][k] = rep["counts"].get(k, 0) + n

    with db.tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(990011)")
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
        for (d, old_nid), r in sorted(latest.items(), key=lambda x: x[0][0]):
            nid, fuel = nozzle_map[old_nid]
            op, cl, te = _n(r.get("opening_reading")), _n(r.get("closing_reading")), _n(r.get("testing_qty"))
            if cl < op:
                rep["issues"].append(f"Skipped reading {d} nozzle {old_nid}: closing {cl} < opening {op}")
                continue
            if te > cl - op:
                rep["issues"].append(f"Reading {d} nozzle {old_nid}: testing {te} > sale; testing set to 0")
                te = ZERO
            cur.execute("""INSERT INTO nozzle_readings (business_date, nozzle_id, opening, closing, testing, rate,
                                                        opening_override_reason, updated_by)
                           VALUES (%s,%s,%s,%s,%s,%s,'Imported from old system','import')""",
                        (d, nid, op, cl, te, old_rates.get(fuel, ZERO)))
            legacy_dates.add(d)
            count("meter readings")

        # ---------------- daily closing totals
        modes = {}
        cur.execute("SELECT id, name FROM collection_modes")
        for m in cur.fetchall():
            modes[m["name"]] = m["id"]
        mode_cols = {"phonepe": "PhonePe", "card_swipe": "Card swipe", "hp_pay": "HP Pay",
                     "hpcl_otp": "HPCL OTP / Fleet card", "upi_other": "Other UPI"}
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
                    cur.execute("""INSERT INTO day_collections (business_date, mode_id, amount, updated_by)
                                   VALUES (%s,%s,%s,'import') ON CONFLICT (business_date, mode_id)
                                   DO UPDATE SET amount=EXCLUDED.amount""", (d, modes[name], _n(r[col])))
            count("daily closings")

        # ---------------- parties
        party_map, party_by_name, old_balance = {}, {}, {}
        rows, cols = _rows(cur, "credit_transporters")
        for r in rows:
            name = _s(r.get("party_name"), 100) or f"Party {r['id']}"
            base, k = name, 2
            while True:
                cur.execute("SELECT 1 FROM parties WHERE LOWER(TRIM(name))=LOWER(TRIM(%s))", (name,))
                if not cur.fetchone():
                    break
                name = f"{base} ({k})"
                k += 1
            if name != base:
                rep["issues"].append(f"Duplicate party name '{base}' imported as '{name}'")
            mobile = "".join(c for c in _s(r.get("mobile")) if c.isdigit())[-10:]
            cur.execute("""INSERT INTO parties (name, mobile, vehicles, active, legacy_id, discount_per_litre)
                           VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (name, mobile, _s(r.get("vehicle_no"), 400).upper(),
                         _s(r.get("status")).lower() != "inactive", r["id"], max(_n(r.get("discount")), ZERO)))
            pid = cur.fetchone()["id"]
            party_map[r["id"]] = pid
            party_by_name[base.lower()] = pid
            old_balance[pid] = (name, _n(r.get("balance_due")))
            ob = _n(r.get("opening_balance")) if "opening_balance" in cols else ZERO
            if ob:
                cur.execute("""INSERT INTO party_txns (party_id, business_date, kind, debit, credit, remarks,
                                                       created_by, is_legacy)
                               VALUES (%s,%s,'opening',%s,%s,'Opening balance (old system)','import',TRUE)""",
                            (pid, earliest, max(ob, ZERO), max(-ob, ZERO)))
            count("parties")

        def pid_for(r):
            if r.get("transporter_id") in party_map:
                return party_map[r["transporter_id"]]
            return party_by_name.get(_s(r.get("transporter_name")).lower())

        def add_txn(pid, d, kind, debit=ZERO, credit=ZERO, **kw):
            legacy_dates.add(d)
            cur.execute("""INSERT INTO party_txns (party_id, business_date, kind, fuel_code, qty, rate, debit, credit,
                             pay_mode, vehicle_no, challan_no, slip_no, remarks, created_by, is_legacy,
                             cash_taken, sl_no, pay_for)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'import',TRUE,%s,%s,%s)""",
                        (pid, d, kind, kw.get("fuel_code"), kw.get("qty", ZERO), kw.get("rate", ZERO), debit, credit,
                         kw.get("pay_mode"), _s(kw.get("vehicle_no"), 20).upper(), _s(kw.get("challan_no"), 30),
                         _s(kw.get("slip_no"), 30), _s(kw.get("remarks"), 200), kw.get("cash_taken", ZERO),
                         kw.get("sl_no"), kw.get("pay_for")))

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

        for pid, (name, oldbal) in old_balance.items():
            cur.execute("SELECT COALESCE(SUM(debit-credit),0) AS b FROM party_txns WHERE party_id=%s AND NOT voided",
                        (pid,))
            computed = Decimal(cur.fetchone()["b"])
            diff = money(oldbal - computed)
            rep["balances"].append({"name": name, "old": oldbal, "computed": computed, "diff": diff})
            if diff != 0:
                add_txn(pid, T, "adjustment", debit=max(diff, ZERO), credit=max(-diff, ZERO),
                        remarks="Difference between old-system balance and its own entries, at import")

        # ---------------- lube
        rows, cols = _rows(cur, "lube_stock")
        for r in rows:
            name = _s(r.get("product_name"), 80)
            if not name:
                continue
            cur.execute("SELECT id FROM lube_products WHERE LOWER(TRIM(name))=LOWER(TRIM(%s))", (name,))
            ex = cur.fetchone()
            if ex:
                pid = ex["id"]
                rep["issues"].append(f"Lube '{name}' appeared twice; stock combined")
            else:
                cur.execute("""INSERT INTO lube_products (name, sale_rate, legacy_id) VALUES (%s,%s,%s) RETURNING id""",
                            (name, _n(r.get("selling_rate")), r["id"]))
                pid = cur.fetchone()["id"]
                count("lube products")
            stock = _n(r.get("closing_stock"))
            if stock < 0:
                rep["issues"].append(f"Lube '{name}' had negative stock {stock} in old system; imported as 0")
                stock = ZERO
            if stock:
                cur.execute("""INSERT INTO lube_moves (product_id, business_date, kind, qty, remarks, created_by)
                               VALUES (%s,%s,'opening',%s,'Stock carried from old system','import')""", (pid, T, stock))

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
        for (sid, d), st in att.items():
            cur.execute("INSERT INTO attendance (staff_id, work_date, status, updated_by) VALUES (%s,%s,%s,'import')",
                        (sid, d, st))
        count("attendance marks", len(att))
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
            cur.execute("""INSERT INTO staff_payments (staff_id, business_date, for_month, kind, amount, pay_mode,
                                                       remarks, created_by)
                           VALUES (%s,%s,%s,'salary',%s,'Cash by owner',%s,'import')""",
                        (sid, d, ym, amt, f"Old system: {_s(r.get('payment_mode'))} {_s(r.get('remarks'))}".strip()))
            count("salary payments")

        # ---------------- tank dips + receipts
        dips = {}
        for r in tl:
            d = _d(r.get("date"))
            tid = tank_by_fuel.get(_s(r.get("fuel_type")).upper())
            if d and tid:
                dips[(d, tid)] = r
        for (d, tid), r in dips.items():
            vol = _n(r.get("current_stock"))
            if vol < 0:
                continue
            cur.execute("""INSERT INTO tank_dips (business_date, tank_id, dip_mm, volume_l, water_mm, temperature_c,
                                                  density, source, updated_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,'legacy','import')""",
                        (d, tid, _n(r.get("dip_mm")), vol, _n(r.get("water_dip_mm")),
                         _n(r.get("temperature_c")) or None, _n(r.get("density_kg_m3")) or None))
            legacy_dates.add(d)
            count("tank dips")
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
                    cur.execute("""INSERT INTO fuel_receipts (business_date, tank_id, invoice_no, vehicle_no,
                                     received_qty_l, dip_before_mm, dip_after_mm, challan_density, observed_density,
                                     temperature_c, remarks, created_by, order_no, carrier_no, carrier_name, po_no, po_date,
                                     water_checked, compartments, receipt_group, dip_variance_mm)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Imported from old system','import',
                                           %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                (d, tank_by_fuel[code], _s(r.get("invoice_no"), 40), _s(r.get("vehicle_no"), 20),
                                 vol, _n(r.get("dip_before")) or None, _n(r.get("dip_after")) or None,
                                 _n(r.get("challan_density")) or None, _n(r.get("density")) or None,
                                 _n(r.get("temperature_c")) or None, _s(r.get("order_no"), 40), _s(r.get("carrier_no"), 40),
                                 _s(r.get("carrier_name"), 80), _s(r.get("po_no"), 40), _d(r.get("po_date")),
                                 _s(r.get("water_checked"), 20),
                                 json.dumps([c for c in comps if c["fuel"] == code]) if comps else None, group,
                                 _n(r.get("total_dip_variance")) if r.get("total_dip_variance") not in (None, "") else None))
                    legacy_dates.add(d)
                    count("tanker receipts")

        # ---------------- proofs + old activity log
        rows, cols = _rows(cur, "proof_register")
        for r in rows:
            d = _d(r.get("proof_date"))
            url = _s(r.get("photo_url"), 500)
            vurl = _s(r.get("video_url"), 500)
            if d and (url or vurl):
                cur.execute("""INSERT INTO proofs (business_date, category, note, file_url, latitude, longitude, created_by,
                                                   video_url, fuel_code, stock_status, proof_time)
                               VALUES (%s,%s,%s,%s,%s,%s,'import',%s,%s,%s,%s)""",
                            (d, _s(r.get("proof_category"), 40) or "General",
                             " ".join(x for x in [_s(r.get("item_name")), _s(r.get("fuel_type")), _s(r.get("stock_status")),
                                                  _s(r.get("remarks"))] if x)[:300],
                             url, _s(r.get("latitude"), 20) or None, _s(r.get("longitude"), 20) or None,
                             vurl, _s(r.get("fuel_type"), 8).upper() or None, _s(r.get("stock_status"), 20),
                             _s(r.get("proof_time"), 20) or _s(r.get("client_time"), 20)))
                count("proof photos")
        rows, cols = _rows(cur, "activity_log")
        for r in rows:
            cur.execute("""INSERT INTO audit_log (at, username, role, action, entity, summary)
                           VALUES (%s,%s,%s,'legacy',%s,%s)""",
                        (r.get("logged_at") or datetime.now(), _s(r.get("username"), 40), _s(r.get("role"), 20),
                         _s(r.get("module"), 40) or "old system",
                         f"[old system] {_s(r.get('action'))}: {_s(r.get('description'), 450)}"))
        count("old activity log rows", len(rows))

        # ---------------- days: everything before today becomes read-only history
        for d in sorted(legacy_dates):
            locked = d != T
            cur.execute("""INSERT INTO days (business_date, status, is_legacy, legacy_totals, locked_by, locked_at)
                           VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (business_date) DO UPDATE SET status=EXCLUDED.status,
                             is_legacy=EXCLUDED.is_legacy, legacy_totals=EXCLUDED.legacy_totals""",
                        (d, "locked" if locked else "open", locked,
                         json.dumps(legacy_totals.get(d)) if d in legacy_totals else None,
                         "import" if locked else None, datetime.now() if locked else None))
        count("days of history", len(legacy_dates))

        cur.execute("INSERT INTO settings (key, value) VALUES ('legacy_imported_at', %s)",
                    (datetime.now().strftime("%d %b %Y %I:%M %p"),))
        audit(cur, "import", "legacy", None,
              "Imported old data: " + ", ".join(f"{v} {k}" for k, v in rep["counts"].items()))
    return rep
