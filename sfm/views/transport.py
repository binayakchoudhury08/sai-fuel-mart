"""Credit Transport: the daily register of diesel/cash given to transporters,
bills in the original format (with per-litre discount), and daily bill ZIPs."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from .. import db
from ..exports import (bill_filename, transport_bill_pdf, transport_bill_xlsx, transport_zip, workbook_bytes,
                       report_pdf)
from ..security import (assert_transport_writable, assert_writable, audit, can, claim_form_token, current_user, login_required, role)
from ..services import (PAY_MODES, fuels, next_sl_no, party_balance, party_balances, rates_for,
                        register_totals, settings_map, transport_register)
from ..util import ZERO, UserError, clean, inr, money, parse_date, rupee, to_dec, today


def _deliver_compat(buf, mimetype=None, as_attachment=True, download_name=None, **kw):
    from ..storage import deliver
    return deliver(buf, download_name, mimetype)


bp = Blueprint("transport", __name__)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _filters(default_days=0):
    t = today()
    s, e = request.args.get("from"), request.args.get("to")
    start = parse_date(s, "From date") if s else t - timedelta(days=default_days)
    end = parse_date(e, "To date") if e else t
    if end < start:
        start, end = end, start
    if (end - start).days > 400:
        raise UserError("Choose 400 days or less.")
    pid = request.args.get("party") or None
    try:
        pid = int(pid) if pid else None
    except ValueError:
        pid = None
    return start, end, pid, clean(request.args.get("q"), 60)


def _month(ym):
    from ..util import month_bounds
    try:
        return month_bounds(ym)
    except Exception:
        return month_bounds(today().strftime("%Y-%m"))


@bp.route("/transport")
@login_required()
def index():
    d = parse_date(request.args.get("d") or today().isoformat())
    ym = request.args.get("month") or d.strftime("%Y-%m")
    m1, m2 = _month(ym)
    pid = request.args.get("party") or None
    pid = int(pid) if pid and str(pid).isdigit() else None
    q = clean(request.args.get("q"), 60)
    with db.tx() as cur:
        cur.execute("SELECT id, name, vehicles, active FROM parties ORDER BY active DESC, name")
        parties = cur.fetchall()
        rows = transport_register(cur, d, d, pid, q)
        cur.execute("""SELECT p.id, p.name, COUNT(t.id) AS entries,
                              COALESCE(SUM(t.qty) FILTER (WHERE t.kind='fuel'),0) AS qty,
                              COALESCE(SUM(t.debit - t.cash_taken) FILTER (WHERE t.kind='fuel'),0) AS fuel_amount,
                              COALESCE(SUM(t.cash_taken) FILTER (WHERE t.kind='fuel'),0)
                                + COALESCE(SUM(t.debit) FILTER (WHERE t.kind='cash_advance'),0) AS cash,
                              COALESCE(SUM(t.debit),0) AS total, COUNT(DISTINCT t.business_date) AS days
                       FROM parties p JOIN party_txns t ON t.party_id=p.id
                       WHERE NOT t.voided AND t.kind IN ('fuel','cash_advance') AND t.business_date BETWEEN %s AND %s
                       GROUP BY p.id, p.name ORDER BY p.name""", (m1, m2))
        month = cur.fetchall()
        rate = rates_for(cur, d).get("HSD")
    tot = register_totals(rows)
    mtot = {k: sum((Decimal(r[k]) for r in month), ZERO) for k in ("qty", "fuel_amount", "cash", "total")}
    from ..security import TRANSPORT_DAYS
    show_deleted = request.args.get("deleted") == "1"
    deleted_count = sum(1 for r in rows if r["voided"])
    return render_template("transport/index.html", parties=parties, rows=rows, tot=tot, d=d, ym=m1.strftime("%Y-%m"),
                           show_deleted=show_deleted, deleted_count=deleted_count,
                           min_date=today() - timedelta(days=3650),
                           month=month, mtot=mtot, m1=m1, pid=pid, q=q, rate=rate, PAY_MODES=PAY_MODES,
                           prev_day=d - timedelta(days=1), next_day=d + timedelta(days=1) if d < today() else None)


@bp.route("/transport/download")
@login_required("bills")
def download():
    """One place for every bill: transporter (or all), day or month, PDF / Excel / ZIP."""
    period = request.args.get("period", "day")
    fmt = request.args.get("fmt", "pdf")
    if fmt not in ("pdf", "xlsx", "zip") or period not in ("day", "month"):
        abort(404)
    if period == "day":
        start = end = parse_date(request.args.get("d") or today().isoformat())
    else:
        start, end = _month(request.args.get("month") or today().strftime("%Y-%m"))
    pid = request.args.get("party") or ""
    with db.tx() as cur:
        st = settings_map(cur)
        if pid and pid.isdigit():
            cur.execute("SELECT * FROM parties WHERE id=%s", (int(pid),))
            plist = cur.fetchall()
        else:
            cur.execute("""SELECT DISTINCT p.* FROM parties p JOIN party_txns t ON t.party_id=p.id
                           WHERE NOT t.voided AND t.kind IN ('fuel','cash_advance') AND t.business_date BETWEEN %s AND %s
                           ORDER BY p.name""", (start, end))
            plist = cur.fetchall()
        pairs = [(p, transport_register(cur, start, end, p["id"], include_voided=False)) for p in plist]
    pairs = [x for x in pairs if x[1]]
    label = start.strftime("%d %b %Y") if period == "day" else start.strftime("%B %Y")
    if not pairs:
        flash(f"No transport entries for {label}" + (" for that transporter" if pid else "") + ".", "info")
        return redirect(url_for("transport.index", d=start.isoformat(), month=start.strftime("%Y-%m")))
    if fmt in ("pdf", "xlsx") and len(pairs) == 1:
        p, rows = pairs[0]
        maker = transport_bill_pdf if fmt == "pdf" else transport_bill_xlsx
        return _deliver_compat(maker(p, rows, start, end, st), as_attachment=True,
                         mimetype="application/pdf" if fmt == "pdf" else XLSX,
                         download_name=bill_filename(p["name"], start, fmt, end))
    # ZIP (or several transporters): each transporter in its own folder
    if fmt == "zip":
        buf, n = transport_zip(pairs, st, start, end)
    else:
        import zipfile
        from io import BytesIO
        buf = BytesIO()
        maker = transport_bill_pdf if fmt == "pdf" else transport_bill_xlsx
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p, rows in pairs:
                zf.writestr(bill_filename(p["name"], start, fmt, end), maker(p, rows, start, end, st).getvalue())
        buf.seek(0)
    who = pairs[0][0]["name"] if (pid and len(pairs) == 1) else "All transporters"
    kind = {"pdf": "PDF", "xlsx": "Excel", "zip": "PDF+Excel"}[fmt]
    return _deliver_compat(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{''.join(c if c.isalnum() else ' ' for c in who).strip()} Bills {label} {kind}.zip")


def create_entry(cur, form, d, sl_no=None, corrected_from=None):
    """Insert one register row (diesel + cash taken). Returns the new row."""
    try:
        pid = int(form.get("party") or 0)
    except ValueError:
        pid = 0
    cur.execute("SELECT * FROM parties WHERE id=%s FOR UPDATE", (pid,))
    p = cur.fetchone()
    if not p:
        raise UserError("Choose the transporter.")
    if not p["active"]:
        raise UserError(f"{p['name']} is inactive. Activate the party before giving credit.")
    code = form.get("fuel_code") or "HSD"
    if form.get("kind") == "cash_advance":
        # "cash given to driver" only: the amount is cash, never diesel
        q, amt_in = ZERO, ZERO
        cash = rupee(to_dec(form.get("cash_taken") or form.get("amount"), "Cash amount"))
    else:
        q = to_dec(form.get("qty"), "Quantity")
        cash = rupee(to_dec(form.get("cash_taken"), "Cash taken"))
        amt_in = to_dec(form.get("amount"), "Amount")
    rate = ZERO
    if q > 0 or amt_in > 0:
        rates = rates_for(cur, d)
        typed = form.get("rate") and can("rate_override")
        if not rates.get(code) and not typed:
            raise UserError(f"No {code} rate is set for {d.strftime('%d %b %Y')}. Set it in Settings → Fuel rates.")
        rate = rates.get(code) or ZERO
        if typed:
            rate = to_dec(form.get("rate"), "Rate")
            if rate <= 0:
                raise UserError("Rate must be more than zero.")
        if q <= 0:
            q = (amt_in / rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if q <= 0 and cash <= 0:
        raise UserError("Enter litres (or amount) and/or cash taken.")
    if q > 20000:
        raise UserError("That quantity looks like a typing mistake.")
    # every slip is kept in whole rupees (nearest rupee), so bills, balances and the closing check agree
    fuel_amt = rupee(amt_in) if (amt_in > 0 and to_dec(form.get("qty"), "Quantity") <= 0) else rupee(q * rate)
    total = fuel_amt + cash
    vehicle = clean(form.get("vehicle_no"), 20).upper().replace(" ", "")
    challan = clean(form.get("challan_no"), 30)
    slip = clean(form.get("slip_no"), 30)
    if Decimal(p["credit_limit"]) > 0 and not can("credit_limit_override"):
        new_bal = party_balance(cur, pid) + total
        if new_bal > Decimal(p["credit_limit"]):
            raise UserError(f"Credit limit crossed: {p['name']} would owe {inr(new_bal)} "
                            f"(limit {inr(p['credit_limit'])}). Only the owner can allow this.")
    kind = "fuel" if q > 0 else "cash_advance"
    cur.execute("""INSERT INTO party_txns (party_id, business_date, kind, fuel_code, qty, rate, debit, cash_taken,
                       vehicle_no, challan_no, slip_no, remarks, sl_no, corrected_from, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (pid, d, kind, code if kind == "fuel" else None, q if kind == "fuel" else ZERO, rate, total,
                 cash if kind == "fuel" else ZERO, vehicle, challan, slip, clean(form.get("remarks"), 200),
                 sl_no or next_sl_no(cur, d), corrected_from, current_user()["username"]))
    return cur.fetchone(), p


@bp.route("/transport/entry", methods=["POST"])
@login_required("transport_enter")
def add_entry(party_id=None):
    d = parse_date(request.form.get("business_date") or today().isoformat())
    form = request.form.to_dict()
    if party_id and not form.get("party"):
        form["party"] = str(party_id)
    with db.tx() as cur:
        claim_form_token(cur)
        assert_transport_writable(cur, d)
        cur.execute("INSERT INTO days (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
        r, p = create_entry(cur, form, d)
        audit(cur, "create", "transport_entry", r["id"],
              f"SL {r['sl_no']} {p['name']}: {r['qty']} {r['fuel_code'] or ''} @ ₹{r['rate']} "
              f"+ cash ₹{r['cash_taken'] if r['kind'] == 'fuel' else r['debit']} = ₹{r['debit']}",
              after=r, business_date=d)
    flash(f"Entry SL {r['sl_no']} saved for {p['name']}: {inr(r['debit'])}.", "success")
    back = request.form.get("_back")
    if back and back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect(url_for("transport.index", **{"from": d.isoformat(), "to": d.isoformat()}))


@bp.route("/transport/entry/<int:tid>/correct", methods=["POST"])
@login_required("cancel_entries")
def correct_entry(tid):
    """Fix an amount/qty mistake: the old row is cancelled (kept, struck through) and a
    corrected row takes the same SL No — one step, fully audited."""
    reason = clean(request.form.get("reason"), 200)
    if not reason:
        raise UserError("Write what was wrong with the entry.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM party_txns WHERE id=%s FOR UPDATE", (tid,))
        old = cur.fetchone()
        if not old or old["voided"] or old["kind"] not in ("fuel", "cash_advance"):
            raise UserError("Entry not found or already cancelled.")
        d = old["business_date"]
        assert_transport_writable(cur, d)
        cur.execute("""UPDATE party_txns SET voided=TRUE, void_reason=%s, voided_by=%s, voided_at=NOW() WHERE id=%s""",
                    (f"Corrected: {reason}", current_user()["username"], tid))
        form = dict(request.form)
        form["party"] = str(old["party_id"])
        new, p = create_entry(cur, form, d, sl_no=old["sl_no"], corrected_from=tid)
        audit(cur, "correct", "transport_entry", new["id"],
              f"SL {old['sl_no']} {p['name']} corrected ₹{old['debit']} → ₹{new['debit']}: {reason}",
              before=old, after=new, business_date=d)
    flash(f"SL {old['sl_no']} corrected: {inr(old['debit'])} → {inr(new['debit'])}. The old entry stays visible.",
          "success")
    return redirect(request.form.get("_back") or url_for("transport.index"))


# old links keep working
@bp.route("/transport/bill")
@bp.route("/transport/bill.<fmt>")
@bp.route("/transport/bills.zip")
@login_required("bills")
def bill(fmt=None):
    args = dict(request.args)
    p = args.get("party") or ""
    if fmt is None and request.path.endswith(".zip"):
        fmt = "zip"
    if not fmt:
        return redirect(url_for("transport.index", party=p or None) + "#download")
    start = parse_date(args.get("from") or today().isoformat())
    end = parse_date(args.get("to") or start.isoformat())
    if start == end:
        return redirect(url_for("transport.download", party=p, period="day", d=start.isoformat(), fmt=fmt))
    return redirect(url_for("transport.download", party=p, period="month", month=start.strftime("%Y-%m"), fmt=fmt))


@bp.route("/transport/register.<fmt>")
@login_required("bills")
def register_file(fmt):
    start, end, pid, q = _filters(default_days=0)
    with db.tx() as cur:
        rows = transport_register(cur, start, end, pid, q)
        st = settings_map(cur)
    tot = register_totals(rows)
    data = [[r["business_date"], r["sl_no"], r["party_name"], r["challan_no"], r["vehicle_no"], r["slip_no"],
             r["fuel_code"] or "Cash", Decimal(r["qty"]) if r["kind"] == "fuel" else None,
             Decimal(r["rate"]) if r["kind"] == "fuel" else None, Decimal(r["fuel_amount"]), r["cash"],
             Decimal(r["debit"]), ("CANCELLED: " + (r["void_reason"] or "")) if r["voided"] else "", r["created_by"]]
            for r in rows]
    heads = ["Date", "SL", "Transporter", "Challan", "Vehicle", "Slip", "Fuel", "Qty", "Rate", "Fuel amt",
             "Cash taken", "Total", "Status", "By"]
    name = f"Transport register {start:%d%m%y}-{end:%d%m%y}"
    if fmt == "xlsx":
        data.append([None, None, "TOTAL (not counting cancelled)", None, None, None, None, tot["qty"], None,
                     tot["fuel_amount"], tot["cash"], tot["total"], None, None])
        return _deliver_compat(workbook_bytes([("Transport register", heads, data, (8, 9, 10, 11, 12))]), mimetype=XLSX,
                         as_attachment=True, download_name=name + ".xlsx")
    if fmt == "pdf":
        pdf_rows = [r[:12] + [r[12][:40]] for r in data]
        return _deliver_compat(report_pdf(st, "Credit transport register",
                                    f"{start:%d-%m-%Y} to {end:%d-%m-%Y}", heads[:13], pdf_rows,
                                    right_cols=range(7, 12),
                                    totals=["", "", "TOTAL", "", "", "", "", tot["qty"], "", tot["fuel_amount"],
                                            tot["cash"], tot["total"], ""],
                                    widths=[52, 24, 110, 55, 65, 45, 30, 45, 40, 60, 55, 62, 70]),
                         mimetype="application/pdf", as_attachment=True, download_name=name + ".pdf")
    abort(404)


# ------------------------------------------------------------------ scan handwritten bill pages (many pages, one transporter)
def _scan_rows(cur, raw, party_id, challan):
    from ..scan import build_entries, check_against_books
    entries, checks, page_notes = build_entries(raw)
    already = check_against_books(cur, entries, party_id, challan)
    if already:
        page_notes.append(f"Challan {challan} already has {already} entr{'y' if already == 1 else 'ies'} for this transporter")
    return entries, checks, page_notes


@bp.route("/transport/scan")
@login_required("transport_enter")
def scan():
    from ..scan import gemini_ready
    with db.tx() as cur:
        cur.execute("SELECT id, name FROM parties WHERE active ORDER BY name")
        parties = cur.fetchall()
        cur.execute("""SELECT b.*, p.name AS party_name,
                              (SELECT COUNT(*) FROM scans s WHERE s.batch_id=b.id) AS pages,
                              (SELECT string_agg(NULLIF(s.bill_no,''), ', ' ORDER BY s.id) FROM scans s WHERE s.batch_id=b.id) AS bills
                       FROM scan_batches b LEFT JOIN parties p ON p.id=b.party_id ORDER BY b.id DESC LIMIT 15""")
        recent = cur.fetchall()
    return render_template("transport/scan.html", ready=gemini_ready(), recent=recent, parties=parties)


@bp.route("/transport/scan/batch", methods=["POST"])
@login_required("transport_enter")
def scan_batch_new():
    from flask import jsonify
    pid = int(request.form.get("party") or 0) or None
    with db.tx() as cur:
        cur.execute("INSERT INTO scan_batches (created_by, party_id) VALUES (%s,%s) RETURNING id", (current_user()["username"], pid))
        bid = cur.fetchone()["id"]
    return jsonify({"ok": True, "id": bid, "review": url_for("transport.scan_batch", bid=bid)})


@bp.route("/transport/scan/batch/<int:bid>/page", methods=["POST"])
@login_required("transport_enter")
def scan_batch_page(bid):
    """One page per request (each read takes 10–30 s; Vercel allows 60 s per request)."""
    import json
    import uuid as _u
    from flask import jsonify
    from .. import storage
    from ..scan import match_party, read_page
    try:
        b = db.one("SELECT * FROM scan_batches WHERE id=%s", (bid,))
        if not b or b["status"] != "review":
            raise UserError("This scan batch is already saved or discarded.")
        f = request.files.get("photo")
        if not f or not f.filename:
            raise UserError("No photo received.")
        data = f.read(4_000_001)
        if len(data) > 4_000_000:
            raise UserError("Photo is too big.")
        mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        raw = read_page(data, mime)
        path = ""
        if storage.ready():
            try:
                path = f"scans/{today():%Y-%m}/{_u.uuid4().hex}.{'png' if mime == 'image/png' else 'jpg'}"
                storage.put(path, data, mime)
            except Exception:
                path = ""
        with db.tx() as cur:
            p, _ = match_party(cur, raw.get("party"))
            if not b["party_id"] and p:
                cur.execute("UPDATE scan_batches SET party_id=%s WHERE id=%s AND party_id IS NULL", (p["id"], bid))
            cur.execute("""INSERT INTO scans (created_by, party_id, written_party, bill_no, photo_path, raw, batch_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (current_user()["username"], p["id"] if p else None, clean(raw.get("party"), 60),
                         clean(raw.get("bill_no"), 30), path, json.dumps(raw), bid))
            sid = cur.fetchone()["id"]
            audit(cur, "create", "scan", sid, f"Scanned page {raw.get('bill_no') or '?'} ({raw.get('party') or '?'}): "
                  f"{len(raw.get('rows') or [])} lines read (batch {bid})")
        return jsonify({"ok": True, "bill_no": raw.get("bill_no") or "?", "lines": len(raw.get("rows") or [])})
    except UserError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/transport/scan/batch/<int:bid>")
@login_required("transport_enter")
def scan_batch(bid):
    from ..scan import match_party
    with db.tx() as cur:
        cur.execute("SELECT * FROM scan_batches WHERE id=%s", (bid,))
        b = cur.fetchone()
        if not b:
            abort(404)
        cur.execute("SELECT * FROM scans WHERE batch_id=%s ORDER BY id", (bid,))
        scans = cur.fetchall()
        party_id = int(request.args.get("party") or b["party_id"] or 0) or None
        pages, seen, grand = [], {}, {"entries": 0, "qty": Decimal(0), "total": Decimal(0)}
        for s in scans:
            raw = s["raw"] or {}
            entries, checks, notes = _scan_rows(cur, raw, party_id, s["bill_no"])
            if s["bill_no"] and s["bill_no"] in seen:
                notes.insert(0, f"Same challan {s['bill_no']} as page {seen[s['bill_no']]} — was this page photographed twice?")
            seen.setdefault(s["bill_no"], len(pages) + 1)
            tot = sum((e["fuel_amount"] + e["cash"] for e in entries), Decimal(0))
            qty = sum((e["qty"] or 0 for e in entries), Decimal(0))
            grand["entries"] += len(entries); grand["qty"] += qty; grand["total"] += tot
            pages.append({"scan": s, "entries": entries, "checks": checks, "notes": notes, "total": tot, "qty": qty,
                          "flags": sum(1 for e in entries if e["notes"]),
                          "ok": checks["qty_ok"] and checks["amount_ok"] and not notes})
        _, parties = match_party(cur, (scans[0]["raw"] or {}).get("party") if scans else "")
        rate_hsd = None
        for pg in pages:
            if pg["entries"] and pg["entries"][0]["date"]:
                rate_hsd = rates_for(cur, pg["entries"][0]["date"]).get("HSD")
                break
    return render_template("transport/scan_review.html", b=b, pages=pages, grand=grand, parties=parties,
                           party_id=party_id, rate_hsd=rate_hsd)


@bp.route("/transport/scan/page/<int:sid>/photo")
@login_required("transport_enter|bills")
def scan_photo(sid):
    from .. import storage
    s = db.one("SELECT photo_path FROM scans WHERE id=%s", (sid,))
    if not s or not s["photo_path"]:
        abort(404)
    link = storage.signed_download(s["photo_path"], 1800)
    if not link:
        abort(404)
    return redirect(link)


@bp.route("/transport/scan/batch/<int:bid>/save", methods=["POST"])
@login_required("transport_enter")
def scan_batch_save(bid):
    import re as _re
    with db.tx() as cur:
        claim_form_token(cur)
        cur.execute("SELECT * FROM scan_batches WHERE id=%s FOR UPDATE", (bid,))
        b = cur.fetchone()
        if not b:
            abort(404)
        if b["status"] != "review":
            raise UserError("These pages were already saved or discarded.")
        pid = int(request.form.get("party") or 0)
        cur.execute("SELECT * FROM parties WHERE id=%s", (pid,))
        party = cur.fetchone()
        if not party:
            raise UserError("Choose the transporter for these pages.")
        cur.execute("SELECT * FROM scans WHERE batch_id=%s ORDER BY id", (bid,))
        scans = {s["id"]: s for s in cur.fetchall()}
        saved, total, per_page = [], Decimal(0), []
        for sid, s in scans.items():
            challan = clean(request.form.get(f"challan_{sid}"), 30)
            n = int(request.form.get(f"rows_{sid}") or 0)
            count = 0
            for i in range(n):
                g = lambda k: (request.form.get(f"{k}_{sid}_{i}") or "").strip()
                if g("use") != "1":
                    continue
                d = parse_date(g("date"), "Date")
                assert_transport_writable(cur, d)
                cur.execute("INSERT INTO days (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
                form = {"party": str(pid), "fuel_code": "HSD", "vehicle_no": g("vehicle"), "slip_no": g("slip"),
                        "challan_no": challan, "remarks": f"Scanned challan {challan}".strip(), "cash_taken": g("cash") or "0"}
                if g("qty") and to_dec(g("qty"), "Quantity") > 0:
                    form.update(qty=g("qty"), rate=g("rate"))
                else:
                    form.update(kind="cash_advance")
                try:
                    r, _ = create_entry(cur, form, d)
                except UserError as e:
                    raise UserError(f"Challan {challan or '?'}, row {i + 1} ({g('vehicle') or 'cash'}): {e}")
                cur.execute("UPDATE party_txns SET scan_id=%s WHERE id=%s", (sid, r["id"]))
                saved.append(r); total += Decimal(r["debit"]); count += 1
            cur.execute("UPDATE scans SET status='saved', party_id=%s, bill_no=%s, saved_count=%s WHERE id=%s",
                        (pid, challan, count, sid))
            per_page.append(f"{challan or '?'}: {count}")
            key = _re.sub(r"[^A-Z0-9]", "", (s["written_party"] or "").upper())
            if key:   # remember how this transporter is written, so the next pages match by themselves
                cur.execute("""INSERT INTO settings (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""",
                            (f"party_alias:{key}", str(pid)))
        if not saved:
            raise UserError("Tick at least one row to save.")
        cur.execute("UPDATE scan_batches SET status='saved', party_id=%s, saved_count=%s, saved_total=%s WHERE id=%s",
                    (pid, len(saved), total, bid))
        audit(cur, "create", "transport_entry", f"scan-batch:{bid}",
              f"Scanned {len(scans)} page(s) for {party['name']} — {len(saved)} entries, ₹{total} ({'; '.join(per_page)})",
              business_date=saved[0]["business_date"])
    flash(f"{len(saved)} entries saved for {party['name']} from {len(scans)} page(s): {inr(total, decimals=0)}.", "success")
    return redirect(url_for("transport.index", d=saved[0]["business_date"].isoformat(), party=pid))


@bp.route("/transport/scan/batch/<int:bid>/discard", methods=["POST"])
@login_required("transport_enter")
def scan_batch_discard(bid):
    with db.tx() as cur:
        cur.execute("UPDATE scan_batches SET status='discarded' WHERE id=%s AND status='review'", (bid,))
        cur.execute("UPDATE scans SET status='discarded' WHERE batch_id=%s AND status='review'", (bid,))
        audit(cur, "delete", "scan", f"batch:{bid}", "Scanned pages discarded")
    flash("Scan discarded — nothing was saved.", "info")
    return redirect(url_for("transport.scan"))
