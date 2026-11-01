from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import db
from ..security import assert_writable, audit, claim_form_token, current_user, login_required
from ..services import lube_stock, party_balances, product_stock
from ..util import UserError, clean, money, parse_date, to_dec, today

bp = Blueprint("lube", __name__)


@bp.route("/lube")
@login_required()
def index():
    """Stock notebook for a day: opening, + added, − sold, = left, physical count."""
    d = parse_date(request.args.get("d") or today().isoformat())
    with db.tx() as cur:
        cur.execute("""
            SELECT p.*,
              COALESCE(SUM(m.qty) FILTER (WHERE NOT m.voided AND m.business_date < %(d)s), 0) AS opening,
              COALESCE(SUM(m.qty) FILTER (WHERE NOT m.voided AND m.business_date = %(d)s AND m.qty > 0), 0) AS added,
              COALESCE(SUM(-m.qty) FILTER (WHERE NOT m.voided AND m.business_date = %(d)s AND m.kind = 'sale'), 0) AS sold,
              COALESCE(SUM(-m.qty) FILTER (WHERE NOT m.voided AND m.business_date = %(d)s AND m.qty < 0 AND m.kind <> 'sale'), 0) AS removed,
              COALESCE(SUM(m.qty) FILTER (WHERE NOT m.voided AND m.business_date <= %(d)s), 0) AS left_qty,
              COALESCE(SUM(m.qty) FILTER (WHERE NOT m.voided), 0) AS stock
            FROM lube_products p LEFT JOIN lube_moves m ON m.product_id = p.id
            GROUP BY p.id ORDER BY p.active DESC, p.name""", {"d": d})
        products = cur.fetchall()
        cur.execute("""SELECT m.*, p.name AS product_name, p.unit, pa.name AS party_name
                       FROM lube_moves m JOIN lube_products p ON p.id=m.product_id
                       LEFT JOIN party_txns pt ON pt.id=m.party_txn_id LEFT JOIN parties pa ON pa.id=pt.party_id
                       WHERE m.business_date = %s ORDER BY m.id DESC""", (d,))
        moves = cur.fetchall()
    value = sum((Decimal(p["stock"]) * Decimal(p["sale_rate"]) for p in products if p["active"]), Decimal(0))
    low = [p for p in products if p["active"] and Decimal(p["stock"]) <= Decimal(p["reorder_level"])]
    return render_template("lube/index.html", products=products, moves=moves, value=value, low=low, d=d,
                           prev_day=d - timedelta(days=1), next_day=d + timedelta(days=1) if d < today() else None)


@bp.route("/lube/notebook", methods=["POST"])
@login_required("lube_stock")
def notebook():
    """One Save for the whole notebook: stock that arrived (+) and the shelf count (difference adjusted)."""
    d = parse_date(request.form.get("business_date") or today().isoformat())
    bill = clean(request.form.get("bill"), 80)
    added, counted = [], []
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        cur.execute("SELECT * FROM lube_products WHERE active ORDER BY id FOR UPDATE")
        for p in cur.fetchall():
            add = to_dec(request.form.get(f"add_{p['id']}"), f"{p['name']} added")
            if add > 0:
                cur.execute("""INSERT INTO lube_moves (product_id, business_date, kind, qty, remarks, created_by)
                               VALUES (%s,%s,'purchase',%s,%s,%s)""",
                            (p["id"], d, add, ("Stock arrived" + (f" – {bill}" if bill else "")), current_user()["username"]))
                added.append(f"{p['name']} +{add}")
            raw = (request.form.get(f"count_{p['id']}") or "").strip()
            if raw != "":
                cnt = to_dec(raw, f"{p['name']} counted")
                cur.execute("""SELECT COALESCE(SUM(qty),0) AS s FROM lube_moves WHERE product_id=%s AND NOT voided
                               AND business_date <= %s""", (p["id"], d))
                book = Decimal(cur.fetchone()["s"])
                diff = cnt - book
                if diff != 0:
                    cur.execute("""INSERT INTO lube_moves (product_id, business_date, kind, qty, remarks, created_by)
                                   VALUES (%s,%s,'adjust',%s,%s,%s)""",
                                (p["id"], d, diff, f"Stock count: counted {cnt}, book {book}", current_user()["username"]))
                counted.append(f"{p['name']} counted {cnt} (book {book}, {'+' if diff > 0 else ''}{diff})")
        if added or counted:
            audit(cur, "update", "lube_stock", d.isoformat(), "Stock notebook — " + "; ".join(added + counted), business_date=d)
    msg = []
    if added:
        msg.append(f"{len(added)} product(s) added to stock")
    if counted:
        msg.append(f"{len(counted)} count(s) saved")
    flash((", ".join(msg) + ".") if msg else "Nothing to save — write what arrived or what you counted.",
          "success" if msg else "info")
    return redirect(url_for("lube.index", d=d.isoformat()))


@bp.route("/lube/product", methods=["POST"])
@login_required("lube_stock")
def save_product():
    pid = request.form.get("id")
    name = clean(request.form.get("name"), 80)
    if not name:
        raise UserError("Product name is required.")
    f = {"name": name, "unit": clean(request.form.get("unit"), 10) or "pcs",
         "sale_rate": to_dec(request.form.get("sale_rate"), "Selling rate"),
         "reorder_level": to_dec(request.form.get("reorder_level"), "Reorder level"),
         "active": request.form.get("active", "1") == "1"}
    with db.tx() as cur:
        claim_form_token(cur)
        cur.execute("SELECT id FROM lube_products WHERE LOWER(TRIM(name))=LOWER(TRIM(%s)) AND id<>%s",
                    (name, int(pid or 0)))
        if cur.fetchone():
            raise UserError(f"'{name}' already exists.")
        if pid:
            cur.execute("SELECT * FROM lube_products WHERE id=%s FOR UPDATE", (pid,))
            old = cur.fetchone()
            if not old:
                abort(404)
            cur.execute("""UPDATE lube_products SET name=%(name)s, unit=%(unit)s, sale_rate=%(sale_rate)s,
                           reorder_level=%(reorder_level)s, active=%(active)s WHERE id=%(id)s RETURNING *""",
                        {**f, "id": pid})
            audit(cur, "update", "lube_product", pid, f"Edited product '{name}'", before=old, after=cur.fetchone())
            flash("Product saved.", "success")
        else:
            cur.execute("""INSERT INTO lube_products (name, unit, sale_rate, reorder_level)
                           VALUES (%(name)s,%(unit)s,%(sale_rate)s,%(reorder_level)s) RETURNING *""", f)
            p = cur.fetchone()
            opening = to_dec(request.form.get("opening_stock"), "Opening stock")
            if opening > 0:
                d = today()
                assert_writable(cur, d)
                cur.execute("""INSERT INTO lube_moves (product_id, business_date, kind, qty, remarks, created_by)
                               VALUES (%s,%s,'opening',%s,'Opening stock',%s)""",
                            (p["id"], d, opening, current_user()["username"]))
            audit(cur, "create", "lube_product", p["id"], f"Added product '{name}' with opening stock {opening}",
                  after=p)
            flash(f"'{name}' added.", "success")
    return redirect(url_for("lube.index"))


@bp.route("/lube/move", methods=["POST"])
@login_required("lube_stock")
def add_move():
    kind = request.form.get("kind")
    if kind not in ("purchase", "adjust", "return"):
        raise UserError("Unknown stock entry.")
    d = parse_date(request.form.get("business_date") or today().isoformat())
    try:
        pid = int(request.form.get("product_id") or 0)
    except ValueError:
        pid = 0
    q = to_dec(request.form.get("qty"), "Quantity", allow_blank=False)
    if q <= 0:
        raise UserError("Quantity must be more than zero.")
    remarks = clean(request.form.get("remarks"), 200)
    rate = to_dec(request.form.get("rate"), "Rate")
    if kind == "adjust":
        if not remarks:
            raise UserError("Write the reason for the stock adjustment (damage, count difference…).")
        if request.form.get("direction") == "decrease":
            q = -q
    with db.tx() as cur:
        claim_form_token(cur)
        assert_writable(cur, d)
        cur.execute("SELECT * FROM lube_products WHERE id=%s FOR UPDATE", (pid,))
        p = cur.fetchone()
        if not p:
            raise UserError("Choose a product.")
        if q < 0 and product_stock(cur, pid) + q < 0:
            raise UserError(f"Stock of {p['name']} cannot go below zero.")
        cur.execute("""INSERT INTO lube_moves (product_id, business_date, kind, qty, rate, amount, remarks, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (pid, d, kind, q, rate, money(abs(q) * rate), remarks
                     or ("Purchase" if kind == "purchase" else "Customer return"), current_user()["username"]))
        m = cur.fetchone()
        audit(cur, "create", "lube_move", m["id"], f"{kind.title()} {q} {p['unit']} of {p['name']}"
              + (f": {remarks}" if remarks else ""), after=m, business_date=d)
    flash(f"Stock entry saved: {p['name']} {'+' if q > 0 else ''}{q} {p['unit']}.", "success")
    back = request.form.get("_back")
    if back and back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect(url_for("lube.index"))
