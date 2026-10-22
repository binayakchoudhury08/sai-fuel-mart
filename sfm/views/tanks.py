import json
import os
import secrets
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, g, jsonify, redirect, render_template, request, flash, url_for

from .. import db
from ..security import assert_writable, audit, claim_form_token, current_user, login_required
from ..services import avg_daily_sale, tank_reconciliation
from ..util import UserError, clean, now_ist, parse_date, to_dec, today

bp = Blueprint("tanks", __name__)
api = Blueprint("api", __name__)


@bp.route("/tanks")
@login_required()
def index():
    d = parse_date(request.args.get("d") or today().isoformat())
    with db.tx() as cur:
        from ..services import tank_series, avg_daily_sales
        month = tank_series(cur, d.replace(day=1), d)           # whole month once; today is the last day
        rec = [r for r in month if r["date"] == d]
        cur.execute("""SELECT r.*, t.name AS tank_name, t.fuel_code FROM fuel_receipts r
                       JOIN tanks t ON t.id=r.tank_id
                       WHERE r.business_date BETWEEN %s AND %s ORDER BY r.business_date DESC, r.id DESC""",
                    (d - timedelta(days=30), d))
        receipts = cur.fetchall()
        cur.execute("""SELECT dp.*, t.name AS tank_name FROM tank_dips dp JOIN tanks t ON t.id=dp.tank_id
                       WHERE dp.business_date BETWEEN %s AND %s ORDER BY dp.business_date DESC, t.name""",
                    (d - timedelta(days=13), d))
        history = cur.fetchall()
        stats = month_receipt_stats(cur, d)
        mt = {"gain": sum((r["variance"] for r in month if r["variance"] and r["variance"] > 0), Decimal(0)),
              "short": sum((-r["variance"] for r in month if r["variance"] and r["variance"] < 0), Decimal(0)),
              "gain_value": sum((r["value"] for r in month if r["value"] and r["value"] > 0), Decimal(0)),
              "short_value": sum((-r["value"] for r in month if r["value"] and r["value"] < 0), Decimal(0))}
        avgs = avg_daily_sales(cur, d)
        days_left = {}
        for r in rec:
            avg = avgs.get(r["tank"]["fuel_code"], Decimal(0))
            if r["actual"] is not None and avg > 0:
                days_left[r["tank"]["id"]] = (r["actual"] / avg).quantize(Decimal("0.1"))
    return render_template("tanks/index.html", d=d, rec=rec, receipts=receipts, history=history,
                           days_left=days_left, stats=stats, mt=mt, now=now_ist(),
                           prev_day=d - timedelta(days=1),
                           next_day=d + timedelta(days=1) if d < today() else None)


@bp.route("/tanks/dip", methods=["POST"])
@login_required("tanks")
def save_dip():
    d = parse_date(request.form.get("business_date"))
    saved = 0
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        cur.execute("SELECT * FROM tanks WHERE active")
        for t in cur.fetchall():
            raw = request.form.get(f"volume_{t['id']}", "").strip()
            if not raw:
                continue
            vol = to_dec(raw, f"{t['name']} stock")
            if t["capacity_l"] and vol > Decimal(t["capacity_l"]) * Decimal("1.05"):
                raise UserError(f"{t['name']}: {vol} L is more than the tank capacity ({t['capacity_l']} L).")
            f = {"dip_mm": to_dec(request.form.get(f"dip_{t['id']}"), "Dip"),
                 "water_mm": to_dec(request.form.get(f"water_{t['id']}"), "Water"),
                 "temperature_c": to_dec(request.form.get(f"temp_{t['id']}"), "Temperature", allow_negative=True) if request.form.get(f"temp_{t['id']}") else None,
                 "density": to_dec(request.form.get(f"density_{t['id']}"), "Density") if request.form.get(f"density_{t['id']}") else None}
            cur.execute("SELECT * FROM tank_dips WHERE business_date=%s AND tank_id=%s", (d, t["id"]))
            old = cur.fetchone()
            cur.execute("""
                INSERT INTO tank_dips (business_date, tank_id, dip_mm, volume_l, water_mm, temperature_c, density,
                                       source, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'manual',%s)
                ON CONFLICT (business_date, tank_id) DO UPDATE SET dip_mm=EXCLUDED.dip_mm, volume_l=EXCLUDED.volume_l,
                    water_mm=EXCLUDED.water_mm, temperature_c=EXCLUDED.temperature_c, density=EXCLUDED.density,
                    source='manual', updated_by=EXCLUDED.updated_by, updated_at=NOW()
                RETURNING *""", (d, t["id"], f["dip_mm"], vol, f["water_mm"], f["temperature_c"], f["density"],
                                 current_user()["username"]))
            new = cur.fetchone()
            if not old or Decimal(old["volume_l"]) != vol:
                audit(cur, "update" if old else "create", "tank_dip", new["id"],
                      f"{t['name']} stock {old['volume_l'] if old else '–'} → {vol} L", before=old, after=new,
                      business_date=d)
            saved += 1
    flash(f"Dip readings saved for {saved} tank(s)." if saved else "Enter at least one tank stock.",
          "success" if saved else "error")
    return redirect(url_for("tanks.index", d=d.isoformat()))


@bp.route("/tanks/receipt", methods=["POST"])
@login_required("tanks")
def add_receipt():
    """Tanker receipt like the old Fuel Receipt page: one tanker, up to 5 compartments.
    Saved as one row per fuel/tank (so stock maths stays per tank), linked by receipt_group."""
    import uuid
    d = parse_date(request.form.get("business_date"))
    def opt(name, label, neg=False):
        v = request.form.get(name, "").strip()
        return to_dec(v, label, allow_negative=neg) if v else None
    head = {"invoice_no": clean(request.form.get("invoice_no"), 40),
            "vehicle_no": clean(request.form.get("vehicle_no"), 20).upper().replace(" ", ""),
            "order_no": clean(request.form.get("order_no"), 40), "carrier_no": clean(request.form.get("carrier_no"), 40),
            "carrier_name": clean(request.form.get("carrier_name"), 80), "po_no": clean(request.form.get("po_no"), 40),
            "po_date": parse_date(request.form["po_date"], "PO date") if request.form.get("po_date") else None,
            "water_checked": clean(request.form.get("water_checked"), 20),
            "challan_density": opt("challan_density", "Challan density"),
            "observed_density": opt("observed_density", "Density at receipt"),
            "temperature_c": opt("temperature_c", "Temperature", True),
            "dip_before_mm": opt("dip_before_mm", "Dip before"), "dip_after_mm": opt("dip_after_mm", "Dip after"),
            "remarks": clean(request.form.get("remarks"), 300)}
    if not head["invoice_no"]:
        raise UserError("Enter the invoice / challan number.")
    comps = []
    for i in range(1, 6):
        vol = request.form.get(f"c{i}_vol", "").strip()
        if not vol:
            continue
        fuel = request.form.get(f"c{i}_fuel")
        comps.append({"no": i, "fuel": fuel, "volume_l": str(to_dec(vol, f"Compartment {i} volume")),
                      "challan_dip_mm": str(opt(f"c{i}_cdip", f"Compartment {i} challan dip") or ""),
                      "observed_dip_mm": str(opt(f"c{i}_odip", f"Compartment {i} observed dip") or "")})
    if not comps and request.form.get("received_qty_l"):
        cur_tank = int(request.form.get("tank_id") or 0)
        comps = [{"no": 1, "tank_id": cur_tank, "volume_l": str(to_dec(request.form.get("received_qty_l"), "Received")),
                  "challan_dip_mm": "", "observed_dip_mm": ""}]
    if not comps:
        raise UserError("Enter at least one compartment volume.")
    group = uuid.uuid4().hex[:12]
    saved = []
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        by_tank = {}
        for c in comps:
            if c.get("tank_id"):
                cur.execute("SELECT * FROM tanks WHERE id=%s AND active", (c["tank_id"],))
            else:
                cur.execute("SELECT * FROM tanks WHERE fuel_code=%s AND active ORDER BY id LIMIT 1", (c["fuel"],))
            t = cur.fetchone()
            if not t:
                raise UserError(f"No active tank for {c.get('fuel') or 'that fuel'}. Add it in Settings → Tanks.")
            by_tank.setdefault(t["id"], {"tank": t, "comps": []})["comps"].append(c)
        for tid, g in by_tank.items():
            t = g["tank"]
            cur.execute("SELECT id FROM fuel_receipts WHERE invoice_no=%s AND tank_id=%s AND NOT voided",
                        (head["invoice_no"], tid))
            if cur.fetchone():
                raise UserError(f"Invoice {head['invoice_no']} is already recorded for {t['name']}.")
            vol = sum((Decimal(c["volume_l"]) for c in g["comps"]), Decimal(0))
            dv = [Decimal(c["observed_dip_mm"]) - Decimal(c["challan_dip_mm"]) for c in g["comps"]
                  if c["observed_dip_mm"] and c["challan_dip_mm"]]
            inv = to_dec(request.form.get(f"invoice_{t['fuel_code']}") or request.form.get("invoice_qty_l"),
                         "Invoice quantity")
            cur.execute("""INSERT INTO fuel_receipts (business_date, tank_id, invoice_no, vehicle_no, invoice_qty_l,
                             received_qty_l, dip_before_mm, dip_after_mm, challan_density, observed_density, temperature_c,
                             invoice_amount, remarks, created_by, order_no, carrier_no, carrier_name, po_no, po_date,
                             water_checked, compartments, receipt_group, dip_variance_mm)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (d, tid, head["invoice_no"], head["vehicle_no"], inv, vol, head["dip_before_mm"],
                         head["dip_after_mm"], head["challan_density"], head["observed_density"], head["temperature_c"],
                         to_dec(request.form.get("invoice_amount"), "Invoice amount") if len(by_tank) == 1 else 0,
                         head["remarks"], current_user()["username"], head["order_no"], head["carrier_no"],
                         head["carrier_name"], head["po_no"], head["po_date"], head["water_checked"],
                         json.dumps(g["comps"]), group, sum(dv, Decimal(0)) if dv else None))
            r = cur.fetchone()
            saved.append(f"{t['name']} {vol} L")
            alert = ""
            if head["challan_density"] and head["observed_density"] and \
                    abs(head["challan_density"] - head["observed_density"]) > 3:
                alert = " | DENSITY ALERT"
            audit(cur, "create", "fuel_receipt", r["id"],
                  f"Tanker {head['invoice_no']} → {t['name']}: {vol} L" + (f" (invoice {inv} L)" if inv else "") + alert,
                  after=r, business_date=d)
    flash("Tanker receipt saved: " + ", ".join(saved) + ".", "success")
    return redirect(url_for("tanks.index", d=d.isoformat()))


def month_receipt_stats(cur, d):
    first = d.replace(day=1)
    cur.execute("""SELECT r.*, t.fuel_code FROM fuel_receipts r JOIN tanks t ON t.id=r.tank_id
                   WHERE NOT r.voided AND r.business_date BETWEEN %s AND %s""", (first, d))
    rows = cur.fetchall()
    st = {"receipts": len({r["receipt_group"] or r["id"] for r in rows}), "ms": Decimal(0), "hsd": Decimal(0),
          "gain_mm": Decimal(0), "short_mm": Decimal(0), "density_alerts": 0, "short_challans": 0}
    for r in rows:
        if r["fuel_code"] == "MS":
            st["ms"] += Decimal(r["received_qty_l"])
        elif r["fuel_code"] == "HSD":
            st["hsd"] += Decimal(r["received_qty_l"])
        dv = r["dip_variance_mm"]
        if dv is not None:
            if dv > 0:
                st["gain_mm"] += Decimal(dv)
            elif dv < 0:
                st["short_mm"] += -Decimal(dv)
        if r["challan_density"] and r["observed_density"] and abs(Decimal(r["challan_density"]) - Decimal(r["observed_density"])) > 3:
            st["density_alerts"] += 1
        if (r["invoice_qty_l"] and Decimal(r["invoice_qty_l"]) > Decimal(r["received_qty_l"])) or (dv is not None and dv < 0):
            st["short_challans"] += 1
    cur.execute("SELECT MAX(business_date) AS m FROM fuel_receipts WHERE NOT voided")
    st["last"] = cur.fetchone()["m"]
    return st


@bp.route("/tanks/receipt/<int:rid>/void", methods=["POST"])
@login_required("cancel_entries")
def void_receipt(rid):
    reason = clean(request.form.get("reason"), 200)
    if not reason:
        raise UserError("Give a reason for cancelling.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM fuel_receipts WHERE id=%s FOR UPDATE", (rid,))
        r = cur.fetchone()
        if not r or r["voided"]:
            raise UserError("Receipt not found or already cancelled.")
        assert_writable(cur, r["business_date"])
        cur.execute("UPDATE fuel_receipts SET voided=TRUE, void_reason=%s, voided_by=%s, voided_at=NOW() WHERE id=%s",
                    (reason, current_user()["username"], rid))
        audit(cur, "void", "fuel_receipt", rid, f"Cancelled tanker receipt {r['invoice_no']}: {reason}",
              before=r, business_date=r["business_date"])
    flash("Tanker receipt cancelled.", "success")
    return redirect(url_for("tanks.index", d=r["business_date"].isoformat()))


# ------------------------------------------------------------------ ATG bookmarklet (same payload as v1)
def _cors(resp, code=200):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp, code


FUEL_ALIASES = {"MS": "MS", "PETROL": "MS", "HSD": "HSD", "DIESEL": "HSD", "CNG": "CNG"}
ATG_EXTRA = ("capacity", "water_ltr", "gross_volume", "ullage_ltr", "today_receipt_ltr", "today_sale_ltr",
             "decantation_status", "density_status", "density_at_15", "den_float_height", "water_dip_mm",
             "net_volume", "temperature_c", "density_kg_m3", "dip_mm", "product", "tank_no")


def _num(v):
    """ATG pages sometimes send '12,345.60' or ' 812.4 L'. Never turn junk into a silent 0."""
    if v is None or v == "":
        return None
    try:
        s = "".join(ch for ch in str(v) if ch.isdigit() or ch in ".-")
        return Decimal(s).quantize(Decimal("0.01")) if s not in ("", ".", "-") else None
    except Exception:
        return None


@api.route("/api/tank-sync", methods=["POST", "OPTIONS"])
def api_tank_sync():
    """Receives live readings from the HP Smart Connect ATG bookmarklet. Same URL, secret and
    JSON as the old app: {"secret": "...", "tanks": [{"fuel_type": "MS", "net_volume": ..., ...}]}."""
    if request.method == "OPTIONS":
        return _cors(jsonify({"status": "ok"}))
    payload = request.get_json(force=True, silent=True) or {}
    expected = os.environ.get("TANK_SYNC_SECRET", "")
    if not expected or not secrets.compare_digest(str(payload.get("secret", "")), expected):
        return _cors(jsonify({"status": "error", "message": "Invalid or missing secret"}), 403)
    d = today()
    synced, skipped = [], []
    g.user = {"username": "ATG sync", "role": "system"}
    try:
        with db.tx() as cur:
            cur.execute("SELECT status FROM days WHERE business_date=%s", (d,))
            st = cur.fetchone()
            if st and st["status"] == "locked":
                return _cors(jsonify({"status": "error", "message": f"{d} is locked; reading not saved"}), 409)
            for t in payload.get("tanks", []) or []:
                code = FUEL_ALIASES.get(str(t.get("fuel_type") or t.get("product") or "").upper().strip())
                vol = _num(t.get("net_volume"))
                if not code or vol is None or vol < 0:
                    skipped.append(str(t.get("fuel_type") or "?"))
                    continue
                cur.execute("SELECT * FROM tanks WHERE fuel_code=%s AND active ORDER BY id LIMIT 1", (code,))
                tank = cur.fetchone()
                if not tank:
                    skipped.append(code)
                    continue
                extra = {k: (str(_num(t.get(k))) if _num(t.get(k)) is not None else t.get(k))
                         if k not in ("decantation_status", "density_status", "product") else str(t.get(k) or "")[:50]
                         for k in ATG_EXTRA if t.get(k) not in (None, "")}
                extra["synced_at"] = now_ist().isoformat(timespec="seconds")
                cur.execute("""
                    INSERT INTO tank_dips (business_date, tank_id, dip_mm, volume_l, water_mm, temperature_c,
                                           density, source, atg, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'atg',%s,'ATG sync')
                    ON CONFLICT (business_date, tank_id) DO UPDATE SET dip_mm=EXCLUDED.dip_mm,
                        volume_l=EXCLUDED.volume_l, water_mm=EXCLUDED.water_mm, temperature_c=EXCLUDED.temperature_c,
                        density=EXCLUDED.density, source='atg', atg=EXCLUDED.atg, updated_by='ATG sync',
                        updated_at=NOW()
                """, (d, tank["id"], _num(t.get("dip_mm")) or 0, vol, _num(t.get("water_dip_mm")) or 0,
                      _num(t.get("temperature_c")), _num(t.get("density_kg_m3")), json.dumps(extra)))
                cap = _num(t.get("capacity"))
                if cap and (not tank["capacity_l"] or Decimal(tank["capacity_l"]) == 0):
                    cur.execute("UPDATE tanks SET capacity_l=%s WHERE id=%s", (cap, tank["id"]))
                synced.append(f"{code} {vol} L")
            if synced:
                audit(cur, "sync", "tank_dip", None, "ATG sync: " + ", ".join(synced), business_date=d)
    except Exception as e:
        return _cors(jsonify({"status": "error", "message": str(e)[:200]}), 500)
    return _cors(jsonify({"status": "success", "synced": [s.split()[0] for s in synced], "skipped": skipped,
                          "date": d.isoformat(), "server_time": now_ist().strftime("%Y-%m-%d %H:%M:%S")}))


@bp.route("/api/tank-status")
def api_tank_status():
    """Live feed for the tank cards (old app polled this every 30 s). Needs a signed-in user."""
    from flask import g as _g
    if not _g.get("user"):
        return jsonify({"status": "error", "message": "Not logged in"}), 401
    t0 = today()
    with db.tx() as cur:
        rows = tank_reconciliation(cur, t0)
        cur.execute("""SELECT DISTINCT ON (tank_id) tank_id, business_date, volume_l, source, updated_at, atg,
                              temperature_c, density, dip_mm
                       FROM tank_dips ORDER BY tank_id, business_date DESC, updated_at DESC""")
        latest = {r["tank_id"]: r for r in cur.fetchall()}
        from ..services import avg_daily_sales
        avgs = avg_daily_sales(cur, t0)
        out = []
        for r in rows:
            t = r["tank"]
            lt = latest.get(t["id"])
            avg = avgs.get(t["fuel_code"], Decimal(0))
            stock = Decimal(lt["volume_l"]) if lt else None
            atg = (lt["atg"] or {}) if lt else {}
            out.append({
                "tank_id": t["id"], "name": t["name"], "fuel": t["fuel_code"],
                "capacity": float(t["capacity_l"] or 0), "stock": float(stock) if stock is not None else None,
                "fill_pct": float(stock / Decimal(t["capacity_l"]) * 100) if (stock is not None and t["capacity_l"]) else None,
                "days_left": float((stock / avg).quantize(Decimal("0.1"))) if (stock is not None and avg > 0) else None,
                "variance": float(r["variance"]) if r["variance"] is not None else None,
                "variance_value": float(r["value"]) if r["value"] is not None else None,
                "temperature_c": float(lt["temperature_c"]) if lt and lt["temperature_c"] is not None else None,
                "water_ltr": float(_num(atg.get("water_ltr")) or 0),
                "source": lt["source"] if lt else None, "reading_date": lt["business_date"].isoformat() if lt else None,
                "updated_at": lt["updated_at"].isoformat() if lt else None,
                "receipt_mismatch": r["receipt_mismatch"],
            })
    return jsonify({"status": "success", "tanks": out, "server_time": now_ist().isoformat(timespec="seconds")})
