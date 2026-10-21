from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import db
from ..security import audit, can, claim_form_token, current_user, login_required
from ..services import rates_for, settings_map
from ..util import UserError, clean, parse_date, to_dec, today

bp = Blueprint("admin", __name__)

SETTING_KEYS = {
    "station_name": ("Pump name", 80), "station_address": ("Address", 200), "station_phone": ("Phone", 30),
    "station_gstin": ("GSTIN", 15), "backdate_days": ("Back-date window (days)", 3),
    "cash_tolerance": ("Cash difference allowed (₹)", 8),
}


@bp.route("/settings")
@login_required("manager")
def settings():
    with db.tx() as cur:
        st = settings_map(cur)
        cur.execute("SELECT * FROM fuels ORDER BY sort, code")
        fuels = cur.fetchall()
        current = rates_for(cur, today())
        cur.execute("SELECT * FROM fuel_rates ORDER BY effective_from DESC, fuel_code LIMIT 40")
        rate_hist = cur.fetchall()
        cur.execute("""SELECT t.*, f.name AS fuel_name FROM tanks t JOIN fuels f ON f.code=t.fuel_code
                       ORDER BY t.active DESC, f.sort, t.name""")
        tanks = cur.fetchall()
        cur.execute("""SELECT n.*, t.name AS tank_name FROM nozzles n LEFT JOIN tanks t ON t.id=n.tank_id
                       ORDER BY n.active DESC, n.sort, n.machine_no, n.name""")
        nozzles = cur.fetchall()
        cur.execute("SELECT * FROM collection_modes ORDER BY active DESC, sort, name")
        modes = cur.fetchall()
        cur.execute("""SELECT EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema='public' AND table_name='daily_closing') AS legacy""")
        legacy_available = cur.fetchone()["legacy"]
    return render_template("admin/settings.html", st=st, fuels=fuels, current=current, rate_hist=rate_hist,
                           tanks=tanks, nozzles=nozzles, modes=modes, keys=SETTING_KEYS,
                           legacy_available=legacy_available, legacy_done=st.get("legacy_imported_at"))


@bp.route("/settings/general", methods=["POST"])
@login_required("owner")
def save_general():
    with db.tx() as cur:
        st = settings_map(cur)
        changes = []
        for k, (label, lim) in SETTING_KEYS.items():
            if k not in request.form:
                continue
            v = clean(request.form.get(k), lim)
            if k == "backdate_days":
                if not v.isdigit() or not 0 <= int(v) <= 31:
                    raise UserError("Back-date window must be between 0 and 31 days.")
            if k == "cash_tolerance":
                v = str(to_dec(v, label))
            if st.get(k, "") != v:
                cur.execute("INSERT INTO settings (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=%s",
                            (k, v, v))
                changes.append(f"{label}: '{st.get(k, '')}' → '{v}'")
        if changes:
            audit(cur, "update", "settings", None, "; ".join(changes))
    flash("Settings saved." if changes else "Nothing changed.", "success" if changes else "info")
    return redirect(url_for("admin.settings"))


@bp.route("/settings/rates", methods=["POST"])
@login_required("owner")
def save_rates():
    d = parse_date(request.form.get("effective_from"), "Effective date")
    if d > today() + timedelta(days=7):
        raise UserError("Rates can be set at most 7 days ahead.")
    n = 0
    with db.tx() as cur:
        claim_form_token(cur)
        cur.execute("SELECT status FROM days WHERE business_date>=%s AND status='locked' LIMIT 1", (d,))
        if cur.fetchone():
            raise UserError("A locked day falls on or after that date. Rates for locked days cannot change.")
        cur.execute("SELECT code FROM fuels WHERE active")
        active_fuels = cur.fetchall()
        before = rates_for(cur, d)
        for f in active_fuels:
            raw = request.form.get(f"rate_{f['code']}", "").strip()
            if not raw:
                continue
            rate = to_dec(raw, f"{f['code']} rate")
            if rate <= 0 or rate > 1000:
                raise UserError(f"{f['code']} rate looks wrong.")
            if before.get(f["code"]) == rate:
                continue
            cur.execute("""INSERT INTO fuel_rates (fuel_code, effective_from, rate, created_by) VALUES (%s,%s,%s,%s)
                           ON CONFLICT (fuel_code, effective_from) DO UPDATE SET rate=EXCLUDED.rate,
                           created_by=EXCLUDED.created_by, created_at=NOW()""",
                        (f["code"], d, rate, current_user()["username"]))
            audit(cur, "update", "fuel_rate", f["code"],
                  f"{f['code']} rate from {d}: ₹{before.get(f['code'], '–')} → ₹{rate}", business_date=d)
            n += 1
        # readings already saved on/after d keep the rate they were saved with unless re-saved;
        # refresh them so totals match the new rate (only open days, enforced above)
        if n:
            cur.execute("""
                UPDATE nozzle_readings r SET rate = x.new_rate
                FROM (SELECT r2.id, (SELECT fr.rate FROM fuel_rates fr
                                     WHERE fr.fuel_code = nz.fuel_code AND fr.effective_from <= r2.business_date
                                     ORDER BY fr.effective_from DESC LIMIT 1) AS new_rate
                      FROM nozzle_readings r2 JOIN nozzles nz ON nz.id = r2.nozzle_id
                      WHERE r2.business_date >= %s) x
                WHERE r.id = x.id AND x.new_rate IS NOT NULL AND r.rate <> x.new_rate""", (d,))
            if cur.rowcount:
                audit(cur, "update", "nozzle_reading", None,
                      f"Re-priced {cur.rowcount} saved reading(s) from {d} after rate change", business_date=d)
    flash(f"{n} rate(s) updated from {d.strftime('%d %b %Y')}." if n else "No rate changed.",
          "success" if n else "info")
    return redirect(url_for("admin.settings") + "#rates")


@bp.route("/settings/fuel", methods=["POST"])
@login_required("owner")
def save_fuel():
    code = clean(request.form.get("code"), 8).upper()
    name = clean(request.form.get("name"), 40)
    if not code.isalnum() or not name:
        raise UserError("Give a short code (letters/numbers) and a name.")
    with db.tx() as cur:
        cur.execute("""INSERT INTO fuels (code, name, unit, active, sort) VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, unit=EXCLUDED.unit,
                       active=EXCLUDED.active""",
                    (code, name, clean(request.form.get("unit"), 4) or "L", request.form.get("active", "1") == "1", 9))
        audit(cur, "update", "fuel", code, f"Fuel {code} – {name}")
    flash("Fuel saved.", "success")
    return redirect(url_for("admin.settings") + "#fuels")


@bp.route("/settings/tank", methods=["POST"])
@login_required("owner")
def save_tank():
    tid = request.form.get("id")
    f = {"name": clean(request.form.get("name"), 40), "fuel_code": request.form.get("fuel_code"),
         "capacity_l": to_dec(request.form.get("capacity_l"), "Capacity"),
         "active": request.form.get("active", "1") == "1"}
    if not f["name"]:
        raise UserError("Tank name is required.")
    with db.tx() as cur:
        cur.execute("SELECT 1 FROM fuels WHERE code=%s", (f["fuel_code"],))
        if not cur.fetchone():
            raise UserError("Choose a fuel.")
        if tid:
            cur.execute("SELECT * FROM tanks WHERE id=%s", (tid,))
            old = cur.fetchone()
            if not old:
                abort(404)
            cur.execute("""UPDATE tanks SET name=%(name)s, fuel_code=%(fuel_code)s, capacity_l=%(capacity_l)s,
                           active=%(active)s WHERE id=%(id)s RETURNING *""", {**f, "id": tid})
            audit(cur, "update", "tank", tid, f"Edited tank {f['name']}", before=old, after=cur.fetchone())
        else:
            cur.execute("""INSERT INTO tanks (name, fuel_code, capacity_l) VALUES (%(name)s,%(fuel_code)s,%(capacity_l)s)
                           RETURNING id""", f)
            audit(cur, "create", "tank", cur.fetchone()["id"], f"Added tank {f['name']}")
    flash("Tank saved.", "success")
    return redirect(url_for("admin.settings") + "#tanks")


@bp.route("/settings/nozzle", methods=["POST"])
@login_required("owner")
def save_nozzle():
    nid = request.form.get("id")
    tank = request.form.get("tank_id") or None
    f = {"name": clean(request.form.get("name"), 40), "machine_no": clean(request.form.get("machine_no"), 20),
         "fuel_code": request.form.get("fuel_code"), "tank_id": int(tank) if tank else None,
         "sort": int(request.form.get("sort") or 0) if (request.form.get("sort") or "0").lstrip("-").isdigit() else 0,
         "active": request.form.get("active", "1") == "1"}
    if not f["name"]:
        raise UserError("Nozzle name is required.")
    with db.tx() as cur:
        cur.execute("SELECT 1 FROM fuels WHERE code=%s", (f["fuel_code"],))
        if not cur.fetchone():
            raise UserError("Choose a fuel.")
        if f["tank_id"]:
            cur.execute("SELECT fuel_code FROM tanks WHERE id=%s", (f["tank_id"],))
            t = cur.fetchone()
            if not t or t["fuel_code"] != f["fuel_code"]:
                raise UserError("The tank must hold the same fuel as the nozzle.")
        if nid:
            cur.execute("SELECT * FROM nozzles WHERE id=%s", (nid,))
            old = cur.fetchone()
            if not old:
                abort(404)
            if old["fuel_code"] != f["fuel_code"]:
                cur.execute("SELECT 1 FROM nozzle_readings WHERE nozzle_id=%s LIMIT 1", (nid,))
                if cur.fetchone():
                    raise UserError("This nozzle already has readings; its fuel cannot change. "
                                    "Deactivate it and add a new nozzle instead.")
            cur.execute("""UPDATE nozzles SET name=%(name)s, machine_no=%(machine_no)s, fuel_code=%(fuel_code)s,
                           tank_id=%(tank_id)s, sort=%(sort)s, active=%(active)s WHERE id=%(id)s RETURNING *""",
                        {**f, "id": nid})
            audit(cur, "update", "nozzle", nid, f"Edited nozzle {f['name']}", before=old, after=cur.fetchone())
        else:
            cur.execute("""INSERT INTO nozzles (name, machine_no, fuel_code, tank_id, sort)
                           VALUES (%(name)s,%(machine_no)s,%(fuel_code)s,%(tank_id)s,%(sort)s) RETURNING id""", f)
            audit(cur, "create", "nozzle", cur.fetchone()["id"], f"Added nozzle {f['name']} ({f['fuel_code']})")
    flash("Nozzle saved.", "success")
    return redirect(url_for("admin.settings") + "#nozzles")


@bp.route("/settings/mode", methods=["POST"])
@login_required("owner")
def save_mode():
    mid = request.form.get("id")
    name = clean(request.form.get("name"), 40)
    if not name:
        raise UserError("Name is required.")
    active = request.form.get("active", "1") == "1"
    with db.tx() as cur:
        cur.execute("SELECT id FROM collection_modes WHERE LOWER(name)=LOWER(%s) AND id<>%s", (name, int(mid or 0)))
        if cur.fetchone():
            raise UserError("That collection type already exists.")
        if mid:
            cur.execute("UPDATE collection_modes SET name=%s, active=%s WHERE id=%s", (name, active, mid))
        else:
            cur.execute("INSERT INTO collection_modes (name, sort) VALUES (%s, 50)", (name,))
        audit(cur, "update", "collection_mode", mid, f"Collection type '{name}' {'active' if active else 'hidden'}")
    flash("Collection type saved.", "success")
    return redirect(url_for("admin.settings") + "#modes")


@bp.route("/audit")
@login_required("owner")
def audit_log():
    q = clean(request.args.get("q"), 60)
    who = clean(request.args.get("user"), 40)
    page = max(int(request.args.get("page") or 1), 1)
    sql = "SELECT * FROM audit_log WHERE TRUE"
    params = []
    if q:
        sql += " AND (summary ILIKE %s OR entity ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    if who:
        sql += " AND username=%s"
        params.append(who)
    if request.args.get("action"):
        sql += " AND action=%s"
        params.append(request.args["action"])
    sql += " ORDER BY id DESC LIMIT 101 OFFSET %s"
    params.append((page - 1) * 100)
    rows = db.query(sql, params)
    users = db.query("SELECT DISTINCT username FROM audit_log WHERE username IS NOT NULL ORDER BY 1")
    return render_template("admin/audit.html", rows=rows[:100], has_more=len(rows) > 100, page=page, q=q,
                           who=who, users=users, action=request.args.get("action", ""))


@bp.route("/admin/import-legacy", methods=["GET", "POST"])
@login_required("owner")
def import_legacy():
    from flask import current_app
    from ..migrate_legacy import inspect_legacy, is_running, read_state, start_background
    if request.method == "POST":
        if request.form.get("confirm") != "IMPORT":
            raise UserError("Type IMPORT in capitals to confirm.")
        start_background(current_app._get_current_object(), current_user()["username"])
        return redirect(url_for("admin.import_legacy"))
    st = read_state() or {}
    if is_running(st):
        return render_template("admin/import_progress.html", st=st)
    if st.get("state") == "done" and st.get("report"):
        return render_template("admin/import_done.html", report=_decimalize(st["report"]))
    info = inspect_legacy()
    return render_template("admin/import.html", info=info, failed=st if st.get("state") in ("failed", "running") else None)


@bp.route("/admin/import-legacy/status")
@login_required("owner")
def import_status():
    from flask import jsonify
    from ..migrate_legacy import is_running, read_state
    st = read_state() or {}
    return jsonify({"state": st.get("state") if (st.get("state") != "running" or is_running(st)) else "failed",
                    "step": st.get("step"), "pct": st.get("pct"), "error": st.get("error") or
                    ("The import stopped (server restarted). Nothing was saved — start it again." if st.get("state") == "running" and not is_running(st) else None),
                    "elapsed": round(__import__("time").time() - float(st.get("started_at") or 0)) if st.get("started_at") else 0})


def _decimalize(rep):
    from decimal import Decimal as D
    for b in rep.get("balances", []):
        for k in ("old", "computed", "diff"):
            try:
                b[k] = D(str(b[k]))
            except Exception:
                b[k] = D(0)
    return rep
