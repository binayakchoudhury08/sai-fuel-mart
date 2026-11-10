"""WhatsApp (owner): the Tally workbook — table photos, add a day, change rows sheet by sheet, daily check, cash,
SFM converter → XML, undo. Every change asks YES and is kept as a new version (audit "(WhatsApp)")."""
import io
import os
import re
from datetime import date
from decimal import Decimal

from .tally_book import LUBE, METER, PAYMENT, RECEIPT, SALES
from .util import UserError, today

SHEET_WORDS = {"hsd": SALES["HSD"], "diesel": SALES["HSD"], "ms": SALES["MS"], "petrol": SALES["MS"], "cng": SALES["CNG"],
               "lube": LUBE, "payment": PAYMENT, "expense": PAYMENT, "receipt": RECEIPT, "received": RECEIPT}
FONT = os.path.join(os.path.dirname(__file__), "fonts")


def _when(text):
    from .wa import _when as w
    x = w(text)
    return x[1] if x and x[0] == "day" else None


def _book(cur):
    from .tally_site import current
    data, meta = current(cur)
    if not data:
        raise UserError("Upload the workbook first: website → Settings → Tally Excel.")
    return data


def inr(v):
    try:
        v = Decimal(str(v or 0))
    except Exception:
        return str(v)
    from .scan import indian
    s = indian(abs(v.quantize(Decimal("1"))))
    return ("-" if v < 0 else "") + s


# ------------------------------------------------------------------ the table photo
def table_png(d, view, check, counted=None):
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(os.path.join(FONT, "DejaVuSans.ttf"), 22)
    fb = ImageFont.truetype(os.path.join(FONT, "DejaVuSans-Bold.ttf"), 24)
    lines = [("title", f"Tally workbook — {d:%d/%m/%Y}")]
    for sh in list(SALES.values()) + [LUBE, PAYMENT, RECEIPT]:
        rows = view[sh]
        lines.append(("head", f"{sh}  ({len(rows)} rows)"))
        for r in rows:
            if sh == LUBE:
                lines.append(("row", f"  {r['party']} · {r['item']}", f"{r['qty']} × {r['rate']}", inr(r["amount"])))
            elif sh in (PAYMENT, RECEIPT):
                lines.append(("row", f"  {r['from']} → {r['to']}", "", inr(r["amount"])))
            else:
                lines.append(("row", f"  {r['party']}", f"{r['qty']} L", inr(r["amount"])))
        if not rows:
            lines.append(("dim", "  — none —", "", ""))
    lines.append(("head", f"METER READING  ({len(view[METER])} meters)"))
    for r in view[METER]:
        lines.append(("row", f"  {r['meter']}", f"{r['opening']} → {r['closing']}", f"net {r['net']}"))
    lines.append(("head", "METER vs SALES"))
    for label, m, s_, diff, ok in check.get("match", []):
        lines.append(("ok" if ok else "bad", f"  {label}  {'✓' if ok else '✗'}", f"meter {inr(m) if 'amount' in label else m}", (f"sheet {inr(s_)}" if 'amount' in label else f"sheet {s_}") + ("" if ok else f" ({diff:+})")))
    lines.append(("head", "DAILY CHECK"))
    for k in ("HSD amount", "MS amount", "CNG amount", "Lube amount", "Cash sales", "Cash receipt", "Cash payment"):
        lines.append(("row", f"  {k}", "", inr(check[k])))
    for k, v in check["digital"].items():
        lines.append(("row", f"  {k}", "", inr(v)))
    lines.append(("total", "  Final cash balance", "", inr(check["Final cash balance"])))
    if counted is not None:
        diff = Decimal(str(counted)) - check["Final cash balance"]
        lines.append(("total", "  Cash counted (Daily Closing)", "", inr(counted)))
        lines.append(("ok" if abs(diff) <= 10 else "bad", "  " + ("MATCHES ✓" if abs(diff) <= 10 else f"DIFFERENCE {inr(diff)}"), "", ""))
    W, H = 1180, 46 + 34 * len(lines)
    img = Image.new("RGB", (W, H), "white"); dr = ImageDraw.Draw(img)
    y = 18
    for ln in lines:
        kind = ln[0]
        if kind == "title":
            dr.rectangle([0, 0, W, 60], fill="#0A1630"); dr.text((24, 16), ln[1], font=fb, fill="white"); y = 76; continue
        if kind == "head":
            dr.rectangle([12, y - 2, W - 12, y + 30], fill="#EAF0FF"); dr.text((22, y + 2), ln[1], font=fb, fill="#163CA8"); y += 36; continue
        col = {"dim": "#8F9BB6", "ok": "#14804A", "bad": "#C0262D", "total": "#0E1A33"}.get(kind, "#0E1A33")
        font = fb if kind in ("total", "ok", "bad") else f
        dr.text((22, y), ln[1][:40], font=font, fill=col)
        if len(ln) > 2 and ln[2]:
            dr.text((520, y), str(ln[2])[:30], font=f, fill="#5B6B86")
        if len(ln) > 3 and ln[3]:
            w = dr.textlength(str(ln[3]), font=font); dr.text((W - 28 - w, y), str(ln[3]), font=font, fill=col)
        dr.line([16, y + 31, W - 16, y + 31], fill="#EDF1F6"); y += 34
    out = io.BytesIO(); img.save(out, "PNG", optimize=True)
    return out.getvalue()


def counted_cash(cur, d):
    try:
        cur.execute("SELECT counted_cash FROM days WHERE business_date=%s", (d,))
        r = cur.fetchone()
        return Decimal(r["counted_cash"]) if r and r["counted_cash"] is not None else None
    except Exception:
        return None


# ------------------------------------------------------------------ plain sentences → one workbook command
CMD_START = r"^(excel|tally excel|xml|daily check|cash|tally import|tally ledger|tally check|tally day\s*book|tally status)\b"


def remember_day(cur, phone, d):
    cur.execute("INSERT INTO settings (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (f"tally_day:{phone}", d.isoformat()))


def last_day(cur, phone):
    cur.execute("SELECT value FROM settings WHERE key=%s", (f"tally_day:{phone}",))
    r = cur.fetchone()
    try:
        return date.fromisoformat(str(r["value"]).strip('"')) if r else None
    except ValueError:
        return None


SHEET_RX = r"(hsd|diesel|ms|petrol|cng|lube|payment|expense|receipt|received)"


def normalize(cur, low, phone=None):
    m = re.match(r"^(?:please\s+)?(?:close|autopilot|finish|day close)(?:\s+(?:the\s+)?day)?\s+(.+)$", low.strip())
    if m and _when(m.group(1)):
        d = _when(m.group(1))
        return f"excel autopilot {d:%d/%m}"
    return _normalize(cur, low, phone)


def _normalize(cur, low, phone=None):
    """'See what's in the Excel for 2 Nov\nexcel 2/11' → 'excel 2/11'; 'add daily closing for tally excel 2/11' →
    'excel add 2/11'. None when it is not about the workbook (e.g. 'svt excel yesterday' = a transporter's bill)."""
    lines = [l.strip() for l in low.splitlines() if l.strip()]
    if phone and re.match(r"^(please\s+)?(upload|import|send)\b.*\btally\b", low) and not _when(low):
        d = last_day(cur, phone)
        return f"tally import {d:%d/%m}" if d else "tally import"
    if re.match(rf"^(please\s+)?(change|edit|replace|make)\b.*\b{SHEET_RX}\b.*\bto\b", low):
        return "excel change " + re.sub(r"^(please\s+)?(change|edit|replace|make)\s+", "", low)
    for l in reversed(lines):                                   # a command written on its own line wins
        if re.match(CMD_START, l):
            return l
    if not re.search(r"\b(excel|tally|xml)\b", low):
        return None
    from .wa import _which_party
    if _which_party(cur, low)[0] and not re.search(r"\b(add|adjust|daily closing|cash|check|xml|convert|import)\b", low):
        return None                                             # "svt excel yesterday" → the bill file
    d = _when(low)
    ds = f"{d:%d/%m}" if d else ""
    if re.search(r"\b(import|upload)\b", low) and "tally" in low:
        return f"tally import {ds}".strip()
    if re.search(r"\b(xml|convert)\b", low):
        return f"xml {ds}".strip()
    if re.search(r"\b(adjust|adjustment|change|edit)\b", low):
        return f"excel adjust {ds}".strip()
    if re.search(r"\b(add|fill|enter|transfer|daily closing|closing)\b", low):
        return f"excel add {ds}".strip()
    if re.search(r"\b(cash|balance)\b", low):
        return f"cash {ds}".strip()
    if re.search(r"\bcheck\b", low):
        return f"daily check {ds}".strip()
    return f"excel {ds}".strip()


# ------------------------------------------------------------------ guided adjustment with tap-menus
ADJ_SHEETS = [SALES["HSD"], SALES["MS"], SALES["CNG"], LUBE, PAYMENT, RECEIPT]


def _menu(body, button, rows, numbered_head):
    """A tap-menu (≤10 rows) + the same options as a numbered text, in case the menu does not arrive."""
    rows = rows[:10]
    fallback = numbered_head + "\n" + "\n".join(f"{i + 1}. {t}" for i, (_id, t, _d) in enumerate(rows)) + "\n\nReply with the number."
    return {"list": (body, button, [("Choose", [(i, t[:24], (dsc or t)[:72]) for i, t, dsc in rows])]), "fallback": fallback, "force": True}


def _sheet_now(cur, sheet, d):
    from .tally_site import day_view
    v = day_view(_book(cur), d)[sheet]
    if not v:
        return "(no rows yet)"
    if sheet == LUBE:
        return "\n".join(f"• {r['item']}: {r['qty']} × {r['rate']} = {inr(r['amount'])}" for r in v)
    if sheet in (PAYMENT, RECEIPT):
        return "\n".join(f"• {r['from']} → {r['to']}: {inr(r['amount'])}" for r in v)
    return "\n".join(f"• {r['party']}: {inr(r['amount'])}" for r in v)


def _names_menu(cur, st, phone):
    from .wa import save_state
    from .tally_site import sheet_names
    sheet = st["sheet"]
    col = 3 if (sheet == RECEIPT and st.get("step") == "name_in") else None
    if sheet == RECEIPT and st.get("step") == "name":
        col = 4
    opts, _a = sheet_names(_book(cur), sheet, col)
    st["opts"] = opts
    save_state(cur, phone, st, touch=True)
    what = {LUBE: "item", PAYMENT: "paid to", RECEIPT: ("received IN" if st.get("step") == "name_in" else "received FROM")}.get(sheet, "party")
    rows = [(f"adj:p:{i}", n, n) for i, n in enumerate(opts)] + [("adj:find", "🔎 Search a name", "Type a few letters"), ("adj:back", "↩ Back", "Choose another sheet")]
    return _menu(f"*{sheet}* {st['d'][8:]}/{st['d'][5:7]} — choose {what} (most used in your Excel):", "Choose name", rows,
                 f"*{sheet}* — choose {what}:")


def _rows_menu(cur, st, phone):
    from .wa import save_state
    from .tally_site import day_view
    sheet, d = st["sheet"], date.fromisoformat(st["d"])
    rows = day_view(_book(cur), d)[sheet]
    def label(r):
        if sheet == LUBE:
            return f"{r['item']} {r['qty']}×{r['rate']}", f"row {r['row']} · {r['party']} · ₹{inr(r['amount'])}"
        if sheet in (PAYMENT, RECEIPT):
            return f"{r['to']} {inr(r['amount'])}", f"row {r['row']} · {r['from']} → {r['to']}"
        return f"{r['party']} {inr(r['amount'])}", f"row {r['row']} · {r['qty']} L"
    st["rows"] = [r["row"] for r in rows][:9]
    st["step"] = "row"
    save_state(cur, phone, st, touch=True)
    items = [(f"adj:r:{i}", *label(r)) for i, r in enumerate(rows[:9])] + [("adj:new", "➕ Add a new row", "")]
    return _menu(f"*{sheet}* {d:%d/%m} — tap the row to change:", "Choose row", items, f"*{sheet}* {d:%d/%m} — choose the row:")


FIELDS = {"sales": [("name", "Change party", "e.g. Cash → DTPLUS HPCL"), ("amount", "Change amount", "₹"), ("qty", "Change litres", "amount = litres × rate")],
          LUBE: [("item", "Change item", ""), ("qty", "Change qty", ""), ("rate", "Change rate", "")],
          PAYMENT: [("from", "Change paid from", "e.g. Cash"), ("to", "Change paid to", ""), ("amount", "Change amount", "")],
          RECEIPT: [("from", "Change received in", "e.g. Cash → DTPLUS HPCL"), ("to", "Change received from", ""), ("amount", "Change amount", "")]}


def _fields(sheet):
    return FIELDS.get(sheet, FIELDS["sales"])


def adjust(cur, phone, st, low, text):
    """Guided change: sheet → row → what to change → new value → YES (or ➕ add a new row)."""
    from .wa import save_state
    from .tally_site import search_names, sheet_names
    step = st.get("step")
    m = re.fullmatch(r"adj:\w:(\d+)", low) or (re.fullmatch(r"(\d{1,2})", low) if step in ("sheet", "row", "field", "name", "name_in", "newname") else None)
    pick = (int(m.group(1)) - (0 if low.startswith("adj:") else 1)) if m else None
    if low in ("adj:back", "back"):
        st = {"mode": "adj", "d": st["d"], "step": "sheet"}
        save_state(cur, phone, st, touch=True)
        return _menu(f"Adjust {st['d'][8:]}/{st['d'][5:7]} — choose the sheet:", "Choose sheet", [(f"adj:s:{i}", s_, "") for i, s_ in enumerate(ADJ_SHEETS)], "Choose the sheet:")
    if step == "sheet":
        if pick is None or not 0 <= pick < len(ADJ_SHEETS):
            return "Tap a sheet, or reply 1–6."
        st["sheet"] = ADJ_SHEETS[pick]
        return _rows_menu(cur, st, phone)
    if step == "row":
        if low == "adj:new" or (pick is not None and pick == len(st.get("rows", []))):
            st.update(step="name_in" if st["sheet"] == RECEIPT else "newname")
            return _names_menu(cur, dict(st, step="name_in" if st["sheet"] == RECEIPT else "name"), phone) if st["sheet"] == RECEIPT else _pick_name(cur, st, phone, "newname")
        if pick is None or not 0 <= pick < len(st.get("rows", [])):
            return "Tap a row, or reply with its number."
        st.update(row=st["rows"][pick], step="field")
        save_state(cur, phone, st, touch=True)
        f = _fields(st["sheet"])
        return _menu(f"Row {st['row']} — what to change?", "Choose", [(f"adj:f:{i}", t, dsc) for i, (_k, t, dsc) in enumerate(f)] + [("adj:back", "↩ Back", "")], "What to change?")
    if step == "field":
        f = _fields(st["sheet"])
        if pick is None or not 0 <= pick < len(f):
            return "Tap what to change."
        key = f[pick][0]
        st["field"] = key
        if key in ("name", "item", "from", "to"):
            return _pick_name(cur, st, phone, "newvalue")
        st["step"] = "number"; save_state(cur, phone, st, touch=True)
        return {"amount": "Type the new *amount* (e.g. *62084*).", "qty": "Type the new *litres / qty* (e.g. *608.79*).", "rate": "Type the new *rate*."}[key]
    if step in ("newvalue", "newname", "name", "name_in"):
        if low == "adj:find":
            return "🔎 Type the first letters of the name (e.g. *dtplus*, *svt*, *racer*)."
        if pick is not None and 0 <= pick < len(st.get("opts") or []):
            chosen = st["opts"][pick]
        else:
            a = __import__("sfm.tally_book", fromlist=["Adder"]).Adder(_book(cur))
            hits = search_names(a, st["sheet"], text)
            if not hits:
                return f"No name matching “{text}”. Try other letters, or tap ↩ Back."
            if len(hits) > 1:
                st["opts"] = hits; save_state(cur, phone, st, touch=True)
                return _menu(f"Names matching “{text}”:", "Choose name", [(f"adj:p:{i}", n, n) for i, n in enumerate(hits)] + [("adj:back", "↩ Back", "")], f"Names matching “{text}”:")
            chosen = hits[0]
        if step == "newvalue":                                  # change the chosen row
            key = {"name": "party", "from": "party", "item": "item", "to": "to"}[st["field"]]
            conf = {"mode": "confirm", "action": "tally_edit", "sheet": st["sheet"], "d": st["d"], "row": st["row"], key: chosen}
            save_state(cur, phone, conf, touch=True)
            return f"✏️ *{st['sheet']}* row {st['row']}: {st['field']} → *{chosen}*\nReply *YES*."
        if step == "name_in":                                   # new receipt: received in, then from
            st.update(from_ledger=chosen, step="newname")
            return _pick_name(cur, st, phone, "newname", col=4)
        st.update(name=chosen, step="amount"); st.pop("opts", None)
        save_state(cur, phone, st, touch=True)
        return f"New row in *{st['sheet']}*: *{chosen}* — type the {'qty (e.g. *12 @ 14*)' if st['sheet'] == LUBE else 'amount, or litres with L (e.g. *608.79 L*)'}."
    if step == "number":
        v = re.fullmatch(r"\d+(?:\.\d+)?", text.strip().replace(",", ""))
        if not v:
            return "Type a number."
        conf = {"mode": "confirm", "action": "tally_edit", "sheet": st["sheet"], "d": st["d"], "row": st["row"], st["field"]: v.group(0)}
        save_state(cur, phone, conf, touch=True)
        return f"✏️ *{st['sheet']}* row {st['row']}: {st['field']} → *{v.group(0)}*\nReply *YES*."
    if step == "amount":
        mm = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*(?:@|x|×|at)\s*(\d+(?:\.\d+)?))?\s*(l|ltr|ltrs|litre|litres|liter|liters|kg)?", text.strip().lower().replace(",", ""))
        if not mm:
            return "Type a number, e.g. *62084*"
        litres = bool(mm.group(3)) and st["sheet"] in SALES.values()
        conf = {"mode": "confirm", "action": "tally_set", "sheet": st["sheet"], "d": st["d"], "name": st["name"], "amount": mm.group(1),
                "rate": mm.group(2), "from_ledger": st.get("from_ledger"), "litres": litres}
        save_state(cur, phone, conf, touch=True)
        return f"➕ *{st['sheet']}* {st['d'][8:]}/{st['d'][5:7]}: {st.get('from_ledger') + ' → ' if st.get('from_ledger') else ''}*{st['name']}* = {mm.group(1)}{' L' if litres else ''}\nReply *YES*."
    return "Send *excel adjust 2/11* to start again."


def _pick_name(cur, st, phone, step, col=None):
    from .wa import save_state
    from .tally_site import sheet_names
    sheet = st["sheet"]
    if col is None:
        col = {"from": 3, "to": 4, "item": 4, "name": 3}.get(st.get("field"), 4 if sheet in (PAYMENT, LUBE) else 3)
    opts, _a = sheet_names(_book(cur), sheet, col)
    st.update(step=step, opts=opts)
    save_state(cur, phone, st, touch=True)
    rows = [(f"adj:p:{i}", n, n) for i, n in enumerate(opts)] + [("adj:find", "🔎 Search a name", "Type a few letters"), ("adj:back", "↩ Back", "")]
    return _menu(f"*{sheet}* — choose the name (most used in your Excel):", "Choose name", rows, f"*{sheet}* — choose the name:")


# ------------------------------------------------------------------ commands
def handle(cur, phone, low, text):
    """→ reply (str / dict) or None when the message is not a workbook command."""
    from .wa import save_state
    from .tally_site import daily_check, day_view
    m = re.match(r"^(excel|tally excel)\s+(.+)$", low)
    if low in ("excel undo", "excel back"):
        save_state(cur, phone, {"mode": "confirm", "action": "tally_undo"}, touch=True)
        return "↩️ Go back to the previous version of the workbook?\nReply *YES*."
    DATE_ONLY = r"(today|yesterday|day before yesterday|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{1,2}(?:st|nd|rd|th)?\s+[a-z]+(?:\s+\d{4})?)"
    if re.fullmatch(rf"(daily check|check|excel check|cash)\s+{DATE_ONLY}", low) and _when(low):
        d = _when(low)
        view = day_view(_book(cur), d); chk = daily_check(view); cnt = counted_cash(cur, d)
        if low.startswith("cash"):
            msg = f"💵 *Cash {d:%d/%m/%Y}*\nWorkbook final cash: *₹{inr(chk['Final cash balance'])}*"
            bad = [m for m in chk.get("match", []) if not m[4]]
            msg += ("\n" + "\n".join(f"❌ {lb}: meter {m} vs sheet {s_} (diff {df:+})" for lb, m, s_, df, ok in bad)) if bad else "\n✅ Meter = sales (HSD, MS, CNG litres and amounts)"
            if cnt is not None:
                diff = cnt - chk["Final cash balance"]
                msg += f"\nCounted in Daily Closing: ₹{inr(cnt)}\n" + ("✅ Matches — adjustment is correct." if abs(diff) <= 10 else f"❌ Difference ₹{inr(diff)} — check the adjustment.")
            return msg
        return {"image": table_png(d, view, chk, cnt), "caption": f"Daily check {d:%d/%m/%Y} — final cash ₹{inr(chk['Final cash balance'])}"}
    mx = re.match(r"^xml\s+(.+?)(?:\s+(?:to|-)\s+(.+))?$", low)
    if mx:
        a = _when(mx.group(1)); b = _when(mx.group(2)) if mx.group(2) else a
        if not a or not b:
            return "Write dates like: *xml 1/11 to 3/11*"
        from .tally_convert import convert
        res = convert(_book(cur), a, b)
        if not res["ok"]:
            return "❌ *XML blocked* (your converter's checks):\n" + "\n".join("• " + e for e in res["errors"][:12]) if res["errors"] else "No vouchers in those dates."
        from .security import audit
        audit(cur, "create", "tally_xml", None, f"Tally XML {a:%d/%m/%Y}–{b:%d/%m/%Y}: {res['vouchers']} vouchers (WhatsApp)")
        order = ["HSD SALES", "MS SALES", "CNG SALES", "LUBE SALES", "METER READING", "PAYMENT", "RECEIPT"]
        summary = (f"✅ *XML {a:%d/%m/%Y} – {b:%d/%m/%Y}*: {res['vouchers']} vouchers — all 7 sheets, like your converter\n"
                   + "\n".join(f"• {sh}: {res['counts'].get(sh, 0)}" for sh in order))
        return {"docs": [(res["name"], res["xml"].encode("utf8"), "text/plain")], "caption": res["name"], "reply": summary, "summary": True}
    if not m:
        return None
    rest = m.group(2).strip()
    mp = re.match(r"^autopilot\s+(.+)$", rest)
    if mp:
        d = _when(mp.group(1))
        if not d:
            return "Which day? e.g. *close 6/11*"
        return autopilot(cur, phone, d)
    mj = re.match(r"^(adjust|adj)\s+(.+)$", rest)
    if mj:
        d = _when(mj.group(2))
        if not d:
            return "Write: *excel adjust 1/11*"
        save_state(cur, phone, {"mode": "adj", "d": d.isoformat(), "step": "sheet"}, touch=True)
        return _menu(f"Adjust *{d:%d/%m/%Y}* — choose the sheet:", "Choose sheet", [(f"adj:s:{i}", s_, "") for i, s_ in enumerate(ADJ_SHEETS)],
                     f"Adjust {d:%d/%m/%Y} — choose the sheet:")
    if rest in ("add", "adjust", "adj"):
        return f"Which date? e.g. *excel {rest} 2/11* or *excel {rest} yesterday*"
    ma = re.match(r"^add\s+(.+)$", rest)
    if ma:
        d = _when(ma.group(1))
        if not d:
            return "Write: *excel add 2/11*"
        remember_day(cur, phone, d)
        save_state(cur, phone, {"mode": "confirm", "action": "tally_add", "d": d.isoformat()}, touch=True)
        return f"➕ Add *{d:%d/%m/%Y}* from Daily Closing to the workbook (meters, credit, lube, expenses, receipts)?\nReply *YES*."
    mc = re.match(rf"^change\s+{SHEET_RX}(?:\s+sales?|\s+sheet)?\s+(?:ledger\s+)?(?:from\s+)?(.+?)\s+(?:to|into)\s+(.+?)(?:\s+(?:on|for|of)\s+(\S+))?$", rest)
    if mc:
        sheet = SHEET_WORDS[mc.group(1)]
        d = _when(mc.group(4)) if mc.group(4) else last_day(cur, phone)
        if not d:
            return "Which date? e.g. *change hsd sales from dtplus to cash on 2/11*"
        from .tally_site import day_view, norm, search_names
        from .tally_book import Adder
        a = Adder(_book(cur))
        rows = day_view(a, d)[sheet]
        key = "from" if sheet in (PAYMENT, RECEIPT) else ("item" if sheet == LUBE else "party")
        old = norm(mc.group(2))
        hit = [r for r in rows if norm(r[key]).startswith(old) or old in norm(r[key])]
        if not hit:
            return f"No “{mc.group(2)}” in {sheet} on {d:%d/%m}. Rows: " + ", ".join(f"{r[key]} {inr(r['amount'])}" for r in rows)
        want = mc.group(3).strip()
        if norm(want) == "cash":
            new = "Cash"
        else:
            hits = search_names(a, sheet, want)
            exact = [h for h in hits if norm(h) == norm(want)]
            new = exact[0] if exact else (hits[0] if hits else None)
        if not new:
            return f"“{want}” is not in your LEDGER MASTER — write it as in Tally."
        field = "party"
        save_state(cur, phone, {"mode": "confirm", "action": "tally_change_all", "sheet": sheet, "d": d.isoformat(),
                                "rows": [r["row"] for r in hit], "field": field, "value": new}, touch=True)
        remember_day(cur, phone, d)
        lines_ = "\n".join(f"• row {r['row']}: {r[key]} {inr(r['amount'])} → *{new}*" for r in hit)
        return f"✏️ *{sheet}* {d:%d/%m}:\n{lines_}\nReply *YES*."
    mr = re.match(r"^(?:rename|change name)\s+(hsd|diesel|ms|petrol|cng|lube|payment|expense|receipt|received)\s+(\S+)\s+(.+?)\s+to\s+(.+)$", rest)
    if mr:
        sheet, d = SHEET_WORDS[mr.group(1)], _when(mr.group(2))
        if not d:
            return "Write: *excel rename hsd 1/11 paytm to phone pe*"
        from .tally_site import day_view, norm, search_names
        from .tally_book import Adder
        a = Adder(_book(cur))
        rows = day_view(a, d)[sheet]
        key = "to" if sheet in (PAYMENT, RECEIPT) else ("item" if sheet == LUBE else "party")
        hit = [r for r in rows if norm(r[key]) == norm(mr.group(3)) or norm(r[key]).startswith(norm(mr.group(3)))]
        if len(hit) != 1:
            return f"Which row? On {d:%d/%m}: " + ", ".join(str(r[key]) for r in rows)
        new = search_names(a, sheet, mr.group(4))
        new = new[0] if new and norm(new[0]) == norm(mr.group(4)) else (new[0] if len(new) == 1 else None)
        if not new:
            return f"“{mr.group(4)}” is not in your LEDGER / ITEM MASTER — write it as in Tally."
        save_state(cur, phone, {"mode": "confirm", "action": "tally_edit", "sheet": sheet, "d": d.isoformat(), "row": hit[0]["row"], key: new}, touch=True)
        return f"✏️ *{sheet}* {d:%d/%m}: *{hit[0][key]}* → *{new}* (row {hit[0]['row']})\nReply *YES*."
    mm_ = re.match(r"^meter\s+(\S+)\s+(.+?)\s+(\d+(?:\.\d+)?)$", rest)
    if mm_:
        d = _when(mm_.group(1))
        from .tally_site import day_view, norm
        meters = [r["meter"] for r in day_view(_book(cur), d)[METER]] if d else []
        words = [w for w in re.findall(r"[a-z0-9]+", mm_.group(2))]
        hit = [m for m in meters if all(w in m.lower().replace(" ", "") or w in m.lower() for w in words)]
        if not d or len(hit) != 1:
            return "Write: *excel meter 2/11 petrol du1 691091.33*\nMeters: " + ", ".join(meters or ["(none on that date)"])
        save_state(cur, phone, {"mode": "confirm", "action": "tally_edit", "sheet": METER, "d": d.isoformat(), "meter": hit[0], "closing": mm_.group(3)}, touch=True)
        return f"⛽ METER {d:%d/%m}: *{hit[0]}* closing → *{mm_.group(3)}* (the next day's opening follows)\nReply *YES*."
    ms_ = re.match(r"^(hsd|diesel|ms|petrol|cng|lube|payment|expense|receipt|received)\s+(\S+)\s+(.+?)\s+(-?\d+(?:\.\d+)?)\s*(l|ltr|ltrs|litre|litres|liter|liters|kg)?$", rest)
    if ms_:
        sheet = SHEET_WORDS[ms_.group(1)]
        d = _when(ms_.group(2))
        if not d:
            return "Write: *excel hsd 1/11 phone pe 62084*"
        name = ms_.group(3).strip().title() if ms_.group(3).strip().lower() not in ("phone pe", "phonepe") else "Phone Pe"
        if ms_.group(3).strip().lower() in ("paytm", "cash", "dtplus hpcl"):
            name = {"paytm": "Paytm", "cash": "Cash", "dtplus hpcl": "DTPLUS HPCL"}[ms_.group(3).strip().lower()]
        litres = bool(ms_.group(5)) and sheet in SALES.values()
        save_state(cur, phone, {"mode": "confirm", "action": "tally_set", "sheet": sheet, "d": d.isoformat(), "name": name,
                                "amount": ms_.group(4), "litres": litres}, touch=True)
        what = f"*{ms_.group(4)} L* (amount = litres × rate)" if litres else f"*₹{inr(ms_.group(4))}*"
        return f"✏️ *{sheet}* {d:%d/%m/%Y}: *{name}* = {what}\n(changes that row, or adds it if it is not there)\nReply *YES*."
    d = _when(rest)
    if d:
        remember_day(cur, phone, d)
        view = day_view(_book(cur), d); chk = daily_check(view)
        return {"image": table_png(d, view, chk, counted_cash(cur, d)),
                "caption": f"Workbook {d:%d/%m/%Y} — change: excel hsd {d:%d/%m} phone pe 62084 · check: daily check {d:%d/%m}"}
    return ("📗 *Tally workbook*\n• *excel 1/11* — table photo of the date\n• *excel add 2/11* — add the day from Daily Closing\n"
            "• *excel adjust 1/11* — tap sheet → tap name → type amount\n• *excel rename hsd 1/11 paytm to phone pe* · *excel meter 2/11 petrol du1 691091.33*\n• *excel hsd 1/11 phone pe 62084* (ms / cng / payment / receipt)\n• *daily check 1/11* · *cash 1/11*\n• *xml 1/11 to 3/11* · *excel undo*")


def run(cur, st):
    """After YES."""
    from .security import audit, current_user
    from .tally_site import add_day, save_version, set_amount
    a = st.get("action")
    if a not in ("tally_add", "tally_set", "tally_undo", "tally_edit", "tally_change_all"):
        return None
    who = current_user()["username"]
    try:
        if a == "tally_add":
            d = date.fromisoformat(st["d"])
            new, added = add_day(cur, _book(cur), d)
            save_version(cur, new, f"Added {d:%d/%m/%Y} from Daily Closing ({len(added)} rows)", who)
            audit(cur, "update", "tally_book", None, f"Tally workbook: {d:%d/%m/%Y} added — {len(added)} rows", business_date=d)
            from .tally_site import daily_check, day_view
            view = day_view(new, d); chk = daily_check(view)
            return {"image": table_png(d, view, chk, counted_cash(cur, d)),
                    "caption": f"✅ {d:%d/%m/%Y} added and adjusted ({len(added)} rows) — final cash ₹{inr(chk['Final cash balance'])}. "
                               "Check the ledgers. To correct: change hsd sales from dtplus to cash · all correct: upload to tally"}
        if a == "tally_change_all":
            from .tally_book import Adder
            from .tally_site import daily_check, day_view
            d = date.fromisoformat(st["d"])
            ad = Adder(_book(cur))
            for r_ in st["rows"]:
                ad.change(st["sheet"], int(r_), party=st["value"])
            new = ad.save()
            what = f"{st['sheet']} {d:%d/%m/%Y}: rows {', '.join(map(str, st['rows']))} → {st['value']}"
            save_version(cur, new, what, who)
            audit(cur, "update", "tally_book", None, what + " (WhatsApp)", business_date=d)
            view = day_view(new, d); chk = daily_check(view)
            return {"image": table_png(d, view, chk, counted_cash(cur, d)),
                    "caption": f"✅ {what}. Check the ledgers again — all correct? write: upload to tally (or close {d:%d/%m})"}
        if a == "tally_edit":
            from .tally_site import edit_row
            d = date.fromisoformat(st["d"])
            new = edit_row(_book(cur), st["sheet"], st.get("row") or 0, dict(st, d=d))
            what = f"{st['sheet']} {d:%d/%m/%Y}: " + (f"{st['meter']} closing {st['closing']}" if st.get("meter") else f"row {st.get('row')} " + ", ".join(f"{k} {st[k]}" for k in ("party", "item", "to", "amount", "qty", "rate") if st.get(k)))
            save_version(cur, new, what, who)
            audit(cur, "update", "tally_book", None, what + " (WhatsApp)", business_date=d)
            return f"✅ {what}. See it: *excel {d:%d/%m}*"
        if a == "tally_set":
            d = date.fromisoformat(st["d"])
            new, what = set_amount(_book(cur), st["sheet"], d, st["name"], Decimal(st["amount"]),
                                   Decimal(st["rate"]) if st.get("rate") else None, st.get("from_ledger"), st.get("litres", False))
            save_version(cur, new, f"{st['sheet']} {d:%d/%m/%Y}: {st['name']} = {st['amount']} ({what})", who)
            audit(cur, "update", "tally_book", None, f"{st['sheet']} {d:%d/%m/%Y}: {st['name']} = {st['amount']} ({what})", business_date=d)
            return (f"✅ {st['sheet']}: {st['name']} = {st['amount'] + (' L' if st.get('litres') else '') if st['sheet'] == LUBE or st.get('litres') else '₹' + inr(st['amount'])} ({what}).\n"
                    f"Next: *excel adjust {d:%d/%m}* · check: *cash {d:%d/%m}*")
        cur.execute("SELECT id, note FROM tally_books ORDER BY id DESC LIMIT 2")
        vs = cur.fetchall()
        if len(vs) < 2:
            return "Nothing to go back to."
        cur.execute("SELECT data FROM tally_books WHERE id=%s", (vs[1]["id"],))
        save_version(cur, bytes(cur.fetchone()["data"]), f"Undo: back to #{vs[1]['id']} (was: {vs[0]['note'][:60]})", who)
        audit(cur, "update", "tally_book", vs[1]["id"], "Tally workbook: undo (WhatsApp)")
        return f"↩️ Back to the version before “{vs[0]['note'][:60]}”."
    except UserError as e:
        return f"❌ Not done — {e}"


# ------------------------------------------------------------------ daily autopilot (owner, 06 Nov)
def autopilot(cur, phone, d):
    """close 6/11 → (Daily Closing must be saved) → add the day to the Tally Excel with the owner's adjustment rule
    (or show it if it is already there) → table photo with the cash check → OK starts the Tally import of that day."""
    from .wa import save_state
    from .security import audit, current_user
    from .tally_book import Adder
    from .tally_site import add_day, already_has, daily_check, day_view, save_version
    cur.execute("SELECT COUNT(*) AS n FROM nozzle_readings WHERE business_date=%s AND closing IS NOT NULL", (d,))
    if not cur.fetchone()["n"]:
        return (f"📋 {d:%d/%m/%Y}: Daily Closing is not saved yet.\nScan or enter it first (website → Daily Closing → "
                f"📷 Scan the closing page), then send *close {d:%d/%m}* again.")
    data = _book(cur)
    added = ""
    if not any(already_has(Adder(data), d).values()):
        try:
            data, rows = add_day(cur, data, d)
        except UserError as e:
            return f"❌ {d:%d/%m/%Y} could not be added to the Tally Excel — {e}"
        save_version(cur, data, f"Added {d:%d/%m/%Y} from Daily Closing ({len(rows)} rows) — autopilot", current_user()["username"])
        audit(cur, "update", "tally_book", None, f"Tally workbook: {d:%d/%m/%Y} added — {len(rows)} rows (autopilot)", business_date=d)
        added = f"added and adjusted ({len(rows)} rows) — "
    remember_day(cur, phone, d)
    view = day_view(data, d); chk = daily_check(view)
    save_state(cur, phone, {"mode": "autopilot", "d": d.isoformat()}, touch=True)
    bad = [m for m in chk.get("match", []) if not m[4]]
    warn = (" ⚠️ meter ≠ sales: " + ", ".join(m[0] for m in bad) + " —") if bad else ""
    return {"image": table_png(d, view, chk, counted_cash(cur, d)),
            "caption": f"✅ {d:%d/%m/%Y} {added}final cash ₹{inr(chk['Final cash balance'])}.{warn} "
                       f"All correct? Reply *OK* to import into Tally. To correct: change hsd sales from dtplus to cash"}


def autopilot_ok(cur, phone, state):
    from .wa import save_state
    from . import tally_jobs
    d = date.fromisoformat(state["d"])
    save_state(cur, phone, {}, touch=True)
    cmd = f"tally import {d:%d/%m}"
    reply = tally_jobs.handle(cur, phone, cmd, cmd)
    return reply or f"Send *tally import {d:%d/%m}*."
