"""Settings → Tally Excel (owner): the workbook kept in the website, days added from Daily Closing, editing,
daily check, SFM converter (XML), Tally names, versions."""
import io
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from .. import db
from ..security import audit, claim_form_token, current_user, login_required
from ..tally_book import LUBE, METER, PAYMENT, RECEIPT, SALES
from ..util import UserError, parse_date, today

bp = Blueprint("tally", __name__)
SHEETS = list(SALES.values()) + [LUBE, PAYMENT, RECEIPT]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _book(cur):
    from ..tally_site import current
    data, meta = current(cur)
    if not data:
        raise UserError("Upload your SFM_Daily_Readings.xlsx first.")
    return data, meta


@bp.route("/tally")
@login_required("owner")
def index():
    from ..tally_site import KINDS, current, daily_check, day_view, name_map
    d = parse_date(request.args.get("d")) if request.args.get("d") else today() - timedelta(days=1)
    with db.tx() as cur:
        data, meta = current(cur)
        cur.execute("SELECT id, created_at, created_by, note, length(data) AS size FROM tally_books ORDER BY id DESC LIMIT 12")
        versions = cur.fetchall()
        names = name_map(cur)
    view = check = None
    missing, choices = [], {}
    if data:
        from ..tally_book import Adder
        from ..tally_site import day_rows, masters, suggest
        try:
            ad = Adder(data)
            view = day_view(ad, d)
            check = daily_check(view)
            with db.tx() as cur:
                _rows, miss = day_rows(cur, d, ad)
            led, items, meters = masters(ad)
            choices = {"party": led, "mode": led, "expense": led, "item": items, "meter": meters}
            missing = [(k, s_, suggest(s_, choices[k])) for k, s_ in miss]
        except Exception as e:                       # a damaged upload must not break the page
            flash(f"Could not read the workbook: {e}", "error")
    return render_template("tally/index.html", d=d, meta=meta, versions=versions, view=view, check=check,
                           names=names, kinds=KINDS, sheets=SHEETS, meter=METER, missing=missing, choices=choices)


@bp.route("/tally/upload", methods=["POST"])
@login_required("owner")
def upload():
    from ..tally_book import Adder
    from ..tally_site import save_version
    f = request.files.get("book")
    if not f or not f.filename.lower().endswith(".xlsx"):
        raise UserError("Choose your .xlsx workbook.")
    data = f.read()
    try:
        v = Adder(data).v
        missing = [s for s in SHEETS + [METER, "LEDGER MASTER", "ITEM MASTER", "METER MASTER"] if s not in v.sheetnames]
    except Exception:
        raise UserError("That file is not a readable Excel workbook.")
    if missing:
        raise UserError("This is not the SFM workbook — missing sheets: " + ", ".join(missing))
    with db.tx() as cur:
        claim_form_token(cur)
        vid = save_version(cur, data, f"Uploaded {f.filename}", current_user()["username"])
        audit(cur, "create", "tally_book", vid, f"Tally workbook uploaded: {f.filename} ({len(data) // 1024} KB)")
    flash("Workbook saved in the website.", "success")
    return redirect(url_for("tally.index"))


@bp.route("/tally/download")
@login_required("owner")
def download():
    with db.tx() as cur:
        data, meta = _book(cur)
    return send_file(io.BytesIO(data), mimetype=XLSX, as_attachment=True, download_name="SFM_Daily_Readings.xlsx")


@bp.route("/tally/add", methods=["POST"])
@login_required("owner")
def add():
    from ..tally_site import add_day, save_version
    d = parse_date(request.form.get("d"), "Date")
    with db.tx() as cur:
        claim_form_token(cur)
        data, _ = _book(cur)
        new, added = add_day(cur, data, d)
        save_version(cur, new, f"Added {d:%d/%m/%Y} from Daily Closing ({len(added)} rows)", current_user()["username"])
        audit(cur, "update", "tally_book", None, f"Tally workbook: {d:%d/%m/%Y} added from Daily Closing — {len(added)} rows", business_date=d)
    flash(f"{d:%d/%m/%Y} added: {len(added)} rows (meters, credit, lube, expenses, receipts). Now do your adjustment.", "success")
    return redirect(url_for("tally.index", d=d.isoformat()))


@bp.route("/tally/set", methods=["POST"])
@login_required("owner")
def set_row():
    from ..tally_site import save_version, set_amount
    d = parse_date(request.form.get("d"), "Date")
    sheet = request.form.get("sheet")
    if sheet not in SHEETS:
        raise UserError("Unknown sheet.")
    name = (request.form.get("name") or "").strip()
    amount = Decimal(request.form.get("amount") or "0")
    rate = Decimal(request.form["rate"]) if (request.form.get("rate") or "").strip() else None
    from_l = (request.form.get("from_ledger") or "").strip() or None
    with db.tx() as cur:
        claim_form_token(cur)
        data, _ = _book(cur)
        new, what = set_amount(data, sheet, d, name, amount, rate, from_l)
        save_version(cur, new, f"{sheet} {d:%d/%m/%Y}: {name} = {amount} ({what})", current_user()["username"])
        audit(cur, "update", "tally_book", None, f"Tally workbook {sheet} {d:%d/%m/%Y}: {name} = {amount} ({what})", business_date=d)
    flash(f"{sheet}: {name} = {amount} — {what}.", "success")
    return redirect(url_for("tally.index", d=d.isoformat()) + "#" + sheet.replace(" ", "-"))


@bp.route("/tally/edit", methods=["POST"])
@login_required("owner")
def edit():
    """Change any input of one row: party / item / ledger, amount, qty, rate — or a meter's closing reading."""
    from ..tally_site import edit_row, save_version
    d = parse_date(request.form.get("d"), "Date")
    sheet = request.form.get("sheet")
    if sheet not in SHEETS + [METER]:
        raise UserError("Unknown sheet.")
    f = {k: (request.form.get(k) or "").strip() for k in ("party", "item", "to", "amount", "qty", "orig_qty", "rate", "meter", "closing")}
    f["d"] = d
    with db.tx() as cur:
        claim_form_token(cur)
        data, _ = _book(cur)
        new = edit_row(data, sheet, request.form.get("row") or 0, f)
        what = f"{sheet} {d:%d/%m/%Y} row {request.form.get('row') or f['meter']}: " + ", ".join(f"{k} {v}" for k, v in f.items() if v and k != "d")
        save_version(cur, new, what, current_user()["username"])
        audit(cur, "update", "tally_book", None, "Tally workbook — " + what, business_date=d)
    flash("Saved — " + what, "success")
    return redirect(url_for("tally.index", d=d.isoformat()) + "#" + sheet.replace(" ", "-"))


@bp.route("/tally/convert", methods=["POST"])
@login_required("owner")
def convert():
    from ..tally_convert import convert as run
    a, b = parse_date(request.form.get("from"), "From"), parse_date(request.form.get("to"), "To")
    with db.tx() as cur:
        data, _ = _book(cur)
    res = run(data, a, b)
    if not res["ok"]:
        flash("XML blocked — " + " · ".join(res["errors"][:6]) if res["errors"] else "No vouchers in those dates.", "error")
        return redirect(url_for("tally.index", d=a.isoformat()))
    with db.tx() as cur:
        audit(cur, "create", "tally_xml", None, f"Tally XML {a:%d/%m/%Y}–{b:%d/%m/%Y}: {res['vouchers']} vouchers")
    return send_file(io.BytesIO(res["xml"].encode("utf8")), mimetype="application/xml", as_attachment=True, download_name=res["name"])


@bp.route("/tally/names", methods=["POST"])
@login_required("owner")
def names():
    kind, site, tally = request.form.get("kind"), (request.form.get("site") or "").strip(), (request.form.get("tally") or "").strip()
    with db.tx() as cur:
        claim_form_token(cur)
        if tally:
            cur.execute("""INSERT INTO tally_names (kind, site_name, tally_name) VALUES (%s,%s,%s)
                           ON CONFLICT (kind, site_name) DO UPDATE SET tally_name=EXCLUDED.tally_name""", (kind, site, tally))
        else:
            cur.execute("DELETE FROM tally_names WHERE kind=%s AND site_name=%s", (kind, site))
        audit(cur, "update", "tally_names", None, f"Tally name: {kind} ‘{site}’ → ‘{tally or '(removed)'}’")
    return redirect(request.form.get("next") or url_for("tally.index"))


@bp.route("/tally/restore/<int:vid>", methods=["POST"])
@login_required("owner")
def restore(vid):
    from ..tally_site import save_version
    with db.tx() as cur:
        claim_form_token(cur)
        cur.execute("SELECT data, note FROM tally_books WHERE id=%s", (vid,))
        r = cur.fetchone()
        if not r:
            raise UserError("Version not found.")
        save_version(cur, bytes(r["data"]), f"Restored version #{vid} ({r['note'][:80]})", current_user()["username"])
        audit(cur, "update", "tally_book", vid, f"Tally workbook restored to version #{vid}")
    flash(f"Version #{vid} is the current workbook again.", "success")
    return redirect(url_for("tally.index"))
