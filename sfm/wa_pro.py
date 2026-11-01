"""WhatsApp assistant — owner commands for the rest of the website: staff & attendance, transporters, tank dips,
submit / lock a day, all monthly bills at once. Every change asks YES first and is audited "(WhatsApp)"."""
import re
from datetime import date, timedelta
from decimal import Decimal

from .util import UserError, today

STATUS = {"present": "P", "absent": "A", "leave": "L", "off": "O", "half": "H"}
STATUS_NAME = {"P": "Present", "A": "Absent", "L": "Leave", "O": "Off", "H": "Half day"}


def _wa():
    from . import wa
    return wa


def _staff_match(cur, name):
    cur.execute("SELECT id, name, designation FROM staff WHERE active ORDER BY name")
    rows = cur.fetchall()
    key = name.strip().lower()
    exact = [r for r in rows if r["name"].lower() == key]
    if exact:
        return exact, rows
    return [r for r in rows if key and (key in r["name"].lower() or r["name"].lower().split()[0] == key.split()[0])], rows


def _day(low):
    w = _wa()._when(low)
    return w[1] if w and w[0] == "day" else today()


def _strip_when(text):
    return re.sub(r"day before yesterday|yesterday|today|\bkal\b|\baaj\b|\bparso\b|\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", " ", text).strip()


# ------------------------------------------------------------------ read-only answers
def staff_text(cur):
    t = today()
    cur.execute("""SELECT s.name, s.designation, s.shift, a.status, a.captured_at FROM staff s
                   LEFT JOIN attendance a ON a.staff_id=s.id AND a.work_date=%s WHERE s.active ORDER BY s.name""", (t,))
    rows = cur.fetchall()
    if not rows:
        return "No staff yet. Add: *add staff Ramesh Kumar role pump attendant shift morning salary 12000*"
    icon = {"P": "✅", "H": "🌓", "A": "❌", "L": "🏖️", "O": "💤"}
    lines = [f"👥 *Staff — {t:%d %b}*"]
    for r in rows:
        st = r["status"]
        lines.append(f"{icon.get(st, '⏳')} {r['name']}" + (f" · {r['designation']}" if r["designation"] else "")
                     + (f" — {STATUS_NAME.get(st, st)}" if st else " — not marked"))
    p = sum(1 for r in rows if r["status"] in ("P", "H"))
    a = sum(1 for r in rows if r["status"] == "A")
    lines.append(f"\nPresent {p} · Absent {a} · Not marked {len(rows) - p - a - sum(1 for r in rows if r['status'] in ('L', 'O'))}")
    lines.append("Mark: *present ramesh* · *absent suresh* · *leave pooja yesterday*")
    return "\n".join(lines)


# ------------------------------------------------------------------ asking (YES first)
def ask(cur, phone, low, text):
    """Returns a reply for owner-level commands, or None if the message is not one of them."""
    wa = _wa()
    m = re.match(r"^(add|new)\s+(staff|employee|worker)\s+(.+)$", low)
    if m:
        rest = text.strip().split(None, 2)[2] if len(text.strip().split(None, 2)) > 2 else m.group(3)
        fields = {}
        for key, pat in (("salary", r"\bsalary\s+(\d+(?:\.\d+)?)"), ("phone", r"\b(?:phone|mobile)\s+(\+?\d[\d ]{8,14})"),
                         ("shift", r"\bshift\s+(morning|evening|night|general)\b"), ("role", r"\b(?:role|as)\s+(.+?)(?=\s+(?:shift|salary|phone|mobile)\b|$)")):
            mm = re.search(pat, rest, re.I)
            if mm:
                fields[key] = mm.group(1).strip()
                rest = rest[:mm.start()] + " " + rest[mm.end():]
        name = re.sub(r"\s+", " ", re.sub(r"\b(shift|salary|phone|mobile|role|as)\b", " ", rest, flags=re.I)).strip().title()
        if len(name) < 2:
            return "Write the name: *add staff Ramesh Kumar role pump attendant shift morning salary 12000*"
        wa.save_state(cur, phone, {"mode": "confirm", "action": "add_staff", "name": name[:60], "role": fields.get("role", "").title()[:40],
                                   "shift": fields.get("shift", "").title(), "salary": fields.get("salary", "0"),
                                   "phone": re.sub(r"\D", "", fields.get("phone", ""))[-10:]}, touch=True)
        return (f"👤 *Add staff*: {name}" + (f" · {fields['role'].title()}" if fields.get("role") else "")
                + (f" · {fields['shift'].title()} shift" if fields.get("shift") else "")
                + (f" · salary ₹{fields['salary']}" if fields.get("salary") else "") + "\nReply *YES* to add.")
    m = re.match(r"^(remove|delete)\s+(staff|employee|worker)\s+(.+)$", low)
    if m:
        hits, rows = _staff_match(cur, m.group(3))
        if len(hits) != 1:
            return "Which one? " + ", ".join(r["name"] for r in (hits or rows)[:15])
        wa.save_state(cur, phone, {"mode": "confirm", "action": "remove_staff", "id": hits[0]["id"], "name": hits[0]["name"]}, touch=True)
        return f"👤 Remove *{hits[0]['name']}* from staff (old attendance stays)?\nReply *YES*."
    m = re.match(r"^(present|absent|leave|off|half)\s+(.+)$", low)
    if m:
        d = _day(low)
        hits, rows = _staff_match(cur, _strip_when(m.group(2)))
        if len(hits) != 1:
            return "Which one? " + ", ".join(r["name"] for r in (hits or rows)[:15])
        st = STATUS[m.group(1)]
        wa.save_state(cur, phone, {"mode": "confirm", "action": "mark", "id": hits[0]["id"], "name": hits[0]["name"],
                                   "status": st, "d": d.isoformat()}, touch=True)
        return f"📝 *{hits[0]['name']}* — {STATUS_NAME[st]} on {d:%d %b}?\nReply *YES*."
    m = re.match(r"^(add|new)\s+(transporter|party|customer)\s+(.+)$", low)
    if m:
        rest = text.strip().split(None, 2)[2]
        lim = re.search(r"\blimit\s+(\d+)", rest, re.I)
        ph = re.search(r"\b(?:phone|mobile)\s+(\+?\d[\d ]{8,14})", rest, re.I)
        name = re.sub(r"\s+", " ", re.sub(r"\b(limit|phone|mobile)\s+\+?[\d ]+", " ", rest, flags=re.I)).strip()
        cur.execute("SELECT name FROM parties WHERE LOWER(name)=LOWER(%s)", (name,))
        if cur.fetchone():
            return f"A transporter named *{name}* already exists."
        wa.save_state(cur, phone, {"mode": "confirm", "action": "add_party", "name": name.upper()[:80], "limit": lim.group(1) if lim else "0",
                                   "phone": re.sub(r"\D", "", ph.group(1))[-10:] if ph else ""}, touch=True)
        return f"🚚 *Add transporter* {name.upper()}" + (f" · limit ₹{lim.group(1)}" if lim else "") + "\nReply *YES* to add."
    m = re.match(r"^dip\s+(ms|hsd|cng|petrol|diesel)\s+(\d+(?:\.\d+)?)\s*(?:l|ltr|litres?)?\b", low)
    if m:
        code = {"petrol": "MS", "diesel": "HSD"}.get(m.group(1), m.group(1).upper())
        cur.execute("SELECT id, name, capacity_l FROM tanks WHERE fuel_code=%s AND active ORDER BY id", (code,))
        tanks = cur.fetchall()
        if len(tanks) != 1:
            return f"{'No' if not tanks else 'More than one'} {code} tank — enter it on the website (Tank Level)."
        vol, d = Decimal(m.group(2)), _day(low)
        if tanks[0]["capacity_l"] and vol > Decimal(tanks[0]["capacity_l"]):
            return f"{vol} L is more than the tank's capacity ({tanks[0]['capacity_l']} L)."
        wa.save_state(cur, phone, {"mode": "confirm", "action": "dip", "tank_id": tanks[0]["id"], "tank": tanks[0]["name"],
                                   "vol": str(vol), "d": d.isoformat()}, touch=True)
        return f"🛢️ Dip *{tanks[0]['name']}*: {vol:,.0f} L on {d:%d %b}?\nReply *YES*."
    m = re.match(r"^(submit|lock)\b", low)
    if m:
        d = _day(low)
        wa.save_state(cur, phone, {"mode": "confirm", "action": m.group(1), "d": d.isoformat()}, touch=True)
        return f"📋 *{m.group(1).title()}* the day {d:%d %b %Y}? (only if all its checks pass)\nReply *YES*."
    return None


# ------------------------------------------------------------------ doing (after YES)
def run(cur, st):
    """Execute a confirmed owner action. Returns the reply, or None if the action is not one of ours."""
    from .security import audit
    a = st.get("action")
    if a == "add_staff":
        cur.execute("SELECT COALESCE(MAX(NULLIF(regexp_replace(emp_code,'\\D','','g'),'')::int),0)+1 AS n FROM staff")
        code = f"EMP{cur.fetchone()['n']:03d}"
        cur.execute("""INSERT INTO staff (emp_code, name, designation, shift, monthly_salary, phone, joined_on)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (code, st["name"], st["role"], st["shift"], Decimal(st["salary"] or 0), st["phone"], today()))
        sid = cur.fetchone()["id"]
        audit(cur, "create", "staff", sid, f"Staff added: {st['name']} ({code}) {st['role']} {st['shift']} ₹{st['salary']}")
        return f"✅ {st['name']} added ({code}). Attendance: *present {st['name'].split()[0].lower()}*"
    if a == "remove_staff":
        cur.execute("UPDATE staff SET active=FALSE WHERE id=%s", (st["id"],))
        audit(cur, "update", "staff", st["id"], f"Staff removed (inactive): {st['name']}")
        return f"✅ {st['name']} removed from the staff list."
    if a == "mark":
        d = date.fromisoformat(st["d"])
        cur.execute("""INSERT INTO attendance (staff_id, work_date, status, marked_via, updated_by) VALUES (%s,%s,%s,'whatsapp-owner',%s)
                       ON CONFLICT (staff_id, work_date) DO UPDATE SET status=EXCLUDED.status, marked_via=EXCLUDED.marked_via,
                       updated_by=EXCLUDED.updated_by, updated_at=NOW()""", (st["id"], d, st["status"], "owner (WhatsApp)"))
        audit(cur, "update", "attendance", st["id"], f"{st['name']} {d:%d %b}: {STATUS_NAME[st['status']]} (owner, WhatsApp)", business_date=d)
        return f"✅ {st['name']} — {STATUS_NAME[st['status']]} on {d:%d %b}."
    if a == "add_party":
        cur.execute("INSERT INTO parties (name, credit_limit, mobile) VALUES (%s,%s,%s) RETURNING id",
                    (st["name"], Decimal(st["limit"] or 0), st["phone"]))
        pid = cur.fetchone()["id"]
        audit(cur, "create", "party", pid, f"Transporter added: {st['name']} (limit ₹{st['limit']})")
        return f"✅ Transporter {st['name']} added. Slips: *slip {st['name'].split()[0].lower()} <vehicle> <litres>*"
    if a == "dip":
        d = date.fromisoformat(st["d"])
        cur.execute("INSERT INTO days (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
        cur.execute("""INSERT INTO tank_dips (business_date, tank_id, volume_l, source, updated_by) VALUES (%s,%s,%s,'manual',%s)
                       ON CONFLICT (business_date, tank_id) DO UPDATE SET volume_l=EXCLUDED.volume_l, source='manual',
                       updated_by=EXCLUDED.updated_by, updated_at=NOW()""", (d, st["tank_id"], Decimal(st["vol"]), "owner (WhatsApp)"))
        audit(cur, "update", "tank_dip", st["tank_id"], f"Dip {st['tank']} {d:%d %b}: {st['vol']} L (WhatsApp)", business_date=d)
        return f"✅ Dip saved: {st['tank']} {Decimal(st['vol']):,.0f} L on {d:%d %b}."
    if a in ("submit", "lock"):
        from .views import day as dayv
        fn = dayv.submit if a == "submit" else dayv.lock
        try:
            (fn.__wrapped__ if hasattr(fn, "__wrapped__") else fn)(st["d"])
        except UserError as e:
            return f"❌ Not {a}ted — {e}"
        return f"✅ {date.fromisoformat(st['d']):%d %b %Y} {a}ted." if a == "lock" else f"✅ {date.fromisoformat(st['d']):%d %b %Y} submitted."
    return None


# ------------------------------------------------------------------ all monthly bills at once
def monthly_bills(cur, low):
    """"bills october" / "all bills october excel" → one month bill per transporter with entries."""
    from .exports import bill_filename, transport_bill_pdf, transport_bill_xlsx
    from .services import settings_map, transport_register
    from .util import month_bounds
    w = _wa()._when(low)
    t = today()
    first = w[1].replace(day=1) if w else t.replace(day=1)
    if w and w[0] == "day":
        first = w[1].replace(day=1)
    start, end = month_bounds(first.strftime("%Y-%m"))
    end = min(end, t)
    xls = bool(re.search(r"\b(excel|xls|xlsx|sheet)\b", low))
    cur.execute("""SELECT DISTINCT p.* FROM parties p JOIN party_txns x ON x.party_id=p.id
                   WHERE NOT x.voided AND x.kind IN ('fuel','cash_advance') AND x.business_date BETWEEN %s AND %s ORDER BY p.name""",
                (start, end))
    parties = cur.fetchall()
    if not parties:
        return f"No credit transport entries in {first:%B %Y}."
    st = settings_map(cur)
    docs, lines = [], [f"📄 *Bills {first:%B %Y}* — {len(parties)} transporter(s):"]
    for p in parties[:25]:
        rows = transport_register(cur, start, end, p["id"], include_voided=False)
        if not rows:
            continue
        total = sum((Decimal(r["debit"] or 0) for r in rows), Decimal(0))
        lines.append(f"• {p['name']}: {len(rows)} entries · {_wa().inr(total)}")
        if xls:
            docs.append((bill_filename(p["name"], start, "xlsx", end), transport_bill_xlsx(p, rows, start, end, st).getvalue(),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        else:
            docs.append((bill_filename(p["name"], start, "pdf", end), transport_bill_pdf(p, rows, start, end, st).getvalue(), "application/pdf"))
    if len(parties) > 25:
        lines.append(f"(first 25 of {len(parties)} — the rest from the website)")
    return {"docs": docs, "caption": f"{first:%B %Y}", "reply": "\n".join(lines), "summary": True}
