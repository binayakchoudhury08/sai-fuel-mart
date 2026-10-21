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
    with db.tx() as cur:
        products = lube_stock(cur)
        cur.execute("""SELECT m.*, p.name AS product_name, p.unit, pa.name AS party_name
                       FROM lube_moves m JOIN lube_products p ON p.id=m.product_id
                       LEFT JOIN party_txns pt ON pt.id=m.party_txn_id LEFT JOIN parties pa ON pa.id=pt.party_id
                       ORDER BY m.business_date DESC, m.id DESC LIMIT 150""")
        moves = cur.fetchall()
        parties = party_balances(cur, active_only=True)
    value = sum((Decimal(p["stock"]) * Decimal(p["sale_rate"]) for p in products), Decimal(0))
    low = [p for p in products if p["active"] and Decimal(p["stock"]) <= Decimal(p["reorder_level"])]
    return render_template("lube/index.html", products=products, moves=moves, value=value, low=low,
                           parties=parties)


@bp.route("/lube/product", methods=["POST"])
@login_required("manager")
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
@login_required("manager")
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
