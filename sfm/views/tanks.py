import json
import os
import secrets
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, g, jsonify, redirect, render_template, request, flash, url_for

from .. import db
from ..security import assert_writable, audit, claim_form_token, current_user, login_required
from ..services import avg_daily_sale, tank_reconciliation
from ..util import UserError, clean, parse_date, to_dec, today

bp = Blueprint("tanks", __name__)
api = Blueprint("api", __name__)


@bp.route("/tanks")
@login_required()
def index():
    d = parse_date(request.args.get("d") or today().isoformat())
    with db.tx() as cur:
        rec = tank_reconciliation(cur, d)
        cur.execute("""SELECT r.*, t.name AS tank_name, t.fuel_code FROM fuel_receipts r
                       JOIN tanks t ON t.id=r.tank_id
                       WHERE r.business_date BETWEEN %s AND %s ORDER BY r.business_date DESC, r.id DESC""",
                    (d - timedelta(days=30), d))
        receipts = cur.fetchall()
        cur.execute("""SELECT dp.*, t.name AS tank_name FROM tank_dips dp JOIN tanks t ON t.id=dp.tank_id
                       WHERE dp.business_date BETWEEN %s AND %s ORDER BY dp.business_date DESC, t.name""",
                    (d - timedelta(days=13), d))
        history = cur.fetchall()
        days_left = {}
        for r in rec:
            avg = avg_daily_sale(cur, r["tank"]["fuel_code"], d)
            if r["actual"] is not None and avg > 0:
                days_left[r["tank"]["id"]] = (r["actual"] / avg).quantize(Decimal("0.1"))
    return render_template("tanks/index.html", d=d, rec=rec, receipts=receipts, history=history,
                           days_left=days_left, prev_day=d - timedelta(days=1),
                           next_day=d + timedelta(days=1) if d < today() else None)


@bp.route("/tanks/dip", methods=["POST"])
@login_required()
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
@login_required()
def add_receipt():
    d = parse_date(request.form.get("business_date"))
    def opt(name, label, neg=False):
        v = request.form.get(name, "").strip()
        return to_dec(v, label, allow_negative=neg) if v else None
    f = {"business_date": d, "tank_id": int(request.form.get("tank_id") or 0),
         "invoice_no": clean(request.form.get("invoice_no"), 40),
         "vehicle_no": clean(request.form.get("vehicle_no"), 20).upper().replace(" ", ""),
         "invoice_qty_l": to_dec(request.form.get("invoice_qty_l"), "Invoice quantity"),
         "received_qty_l": to_dec(request.form.get("received_qty_l"), "Received quantity", allow_blank=False),
         "dip_before_mm": opt("dip_before_mm", "Dip before"), "dip_after_mm": opt("dip_after_mm", "Dip after"),
         "challan_density": opt("challan_density", "Challan density"),
         "observed_density": opt("observed_density", "Observed density"),
         "temperature_c": opt("temperature_c", "Temperature", True),
         "invoice_amount": to_dec(request.form.get("invoice_amount"), "Invoice amount"),
         "remarks": clean(request.form.get("remarks"), 300), "created_by": current_user()["username"]}
    if not f["invoice_no"]:
        raise UserError("Enter the invoice / challan number.")
    if f["received_qty_l"] <= 0:
        raise UserError("Received quantity must be more than zero.")
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        cur.execute("SELECT * FROM tanks WHERE id=%s AND active", (f["tank_id"],))
        t = cur.fetchone()
        if not t:
            raise UserError("Choose the tank.")
        cur.execute("SELECT id FROM fuel_receipts WHERE invoice_no=%s AND tank_id=%s AND NOT voided",
                    (f["invoice_no"], f["tank_id"]))
        if cur.fetchone():
            raise UserError(f"Invoice {f['invoice_no']} is already recorded for {t['name']}.")
        cur.execute("""INSERT INTO fuel_receipts (business_date, tank_id, invoice_no, vehicle_no, invoice_qty_l,
                         received_qty_l, dip_before_mm, dip_after_mm, challan_density, observed_density,
                         temperature_c, invoice_amount, remarks, created_by)
                       VALUES (%(business_date)s,%(tank_id)s,%(invoice_no)s,%(vehicle_no)s,%(invoice_qty_l)s,
                         %(received_qty_l)s,%(dip_before_mm)s,%(dip_after_mm)s,%(challan_density)s,
                         %(observed_density)s,%(temperature_c)s,%(invoice_amount)s,%(remarks)s,%(created_by)s)
                       RETURNING *""", f)
        r = cur.fetchone()
        short = (f["invoice_qty_l"] - f["received_qty_l"]) if f["invoice_qty_l"] else None
        audit(cur, "create", "fuel_receipt", r["id"],
              f"Tanker {f['invoice_no']} into {t['name']}: received {f['received_qty_l']} L"
              + (f" (invoice {f['invoice_qty_l']} L, short {short} L)" if short else ""), after=r, business_date=d)
    flash("Tanker receipt saved.", "success")
    return redirect(url_for("tanks.index", d=d.isoformat()))


@bp.route("/tanks/receipt/<int:rid>/void", methods=["POST"])
@login_required("manager")
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


@api.route("/api/tank-sync", methods=["POST", "OPTIONS"])
def api_tank_sync():
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
                return _cors(jsonify({"status": "error", "message": f"{d} is locked"}), 409)
            for t in payload.get("tanks", []) or []:
                code = str(t.get("fuel_type") or "").upper().strip()
                cur.execute("SELECT * FROM tanks WHERE fuel_code=%s AND active ORDER BY id LIMIT 1", (code,))
                tank = cur.fetchone()
                if not tank:
                    skipped.append(code)
                    continue
                def num(k):
                    try:
                        return Decimal(str(t.get(k) or 0)).quantize(Decimal("0.01"))
                    except Exception:
                        return Decimal(0)
                vol = num("net_volume")
                extra = {k: t.get(k) for k in ("capacity", "water_ltr", "gross_volume", "ullage_ltr",
                                               "today_receipt_ltr", "today_sale_ltr", "decantation_status",
                                               "density_status", "density_at_15", "den_float_height",
                                               "water_dip_mm")}
                cur.execute("""
                    INSERT INTO tank_dips (business_date, tank_id, dip_mm, volume_l, water_mm, temperature_c,
                                           density, source, atg, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'atg',%s,'ATG sync')
                    ON CONFLICT (business_date, tank_id) DO UPDATE SET dip_mm=EXCLUDED.dip_mm,
                        volume_l=EXCLUDED.volume_l, water_mm=EXCLUDED.water_mm, temperature_c=EXCLUDED.temperature_c,
                        density=EXCLUDED.density, source='atg', atg=EXCLUDED.atg, updated_by='ATG sync',
                        updated_at=NOW()
                """, (d, tank["id"], num("dip_mm"), vol, num("water_dip_mm"), num("temperature_c"),
                      num("density_kg_m3") or None, json.dumps(extra, default=str)))
                if tank["capacity_l"] in (None, 0) and num("capacity"):
                    cur.execute("UPDATE tanks SET capacity_l=%s WHERE id=%s", (num("capacity"), tank["id"]))
                audit(cur, "sync", "tank_dip", tank["id"], f"ATG sync {code}: {vol} L", business_date=d)
                synced.append(code)
    except Exception as e:
        return _cors(jsonify({"status": "error", "message": str(e)[:200]}), 500)
    return _cors(jsonify({"status": "success", "synced": synced, "skipped": skipped, "date": d.isoformat()}))
