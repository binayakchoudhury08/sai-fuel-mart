"""WhatsApp assistant (Meta WhatsApp Cloud API, free test number) — answers the owner from the same data and
rules as the website. Replies are sent inside WhatsApp's 24-hour window (free); messages the assistant starts
by itself (scheduled) are sent only while that window is open, otherwise they wait and are delivered the moment
the owner writes next.

Environment (Vercel):
  WA_TOKEN         permanent access token (System user)            WA_PHONE_ID   test number's "Phone number ID"
  WA_VERIFY_TOKEN  any text you choose (webhook check)             WA_APP_SECRET App secret (checks messages come from Meta)
  WA_OWNERS        who may use it: 919876543210:boss,919812345678:mgr2   (number with 91, a colon, the website username)
"""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal

import requests

from . import db
from .util import IST, UserError, now_ist, today

GRAPH = os.environ.get("WA_GRAPH_URL", "https://graph.facebook.com")


def ver():
    return os.environ.get("WA_API_VERSION", "v23.0")


def provider():
    return "twilio" if os.environ.get("TWILIO_ACCOUNT_SID") else "meta"


def ready():
    if provider() == "twilio":
        return bool(os.environ.get("TWILIO_AUTH_TOKEN") and os.environ.get("TWILIO_WHATSAPP_FROM"))
    return bool(os.environ.get("WA_TOKEN") and os.environ.get("WA_PHONE_ID"))


def twilio_signature_ok(url, params, header):
    """Twilio signs every webhook: base64(HMAC-SHA1(auth token, url + sorted key+value pairs))."""
    import base64
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    data = url + "".join(k + params[k] for k in sorted(params))
    want = base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()
    return bool(token) and hmac.compare_digest(want, header or "")


def owners():
    """{phone: username} from WA_OWNERS."""
    out = {}
    for part in os.environ.get("WA_OWNERS", "").split(","):
        if ":" in part:
            ph, user = part.split(":", 1)
            ph = re.sub(r"\D", "", ph)
            if len(ph) == 10:
                ph = "91" + ph
            if ph and user.strip():
                out[ph] = user.strip()
    return out


def signature_ok(raw, header):
    secret = os.environ.get("WA_APP_SECRET", "")
    if not secret:
        return True          # not configured: allowed (owner-number check still applies)
    want = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header or "")


# ------------------------------------------------------------------ sending / media
def _send(phone, body):
    if provider() == "twilio":
        sid = os.environ.get("TWILIO_ACCOUNT_SID")
        frm = os.environ.get("TWILIO_WHATSAPP_FROM", "+14155238886").replace("whatsapp:", "")
        r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", timeout=15,
                          auth=(sid, os.environ.get("TWILIO_AUTH_TOKEN")),
                          data={"From": f"whatsapp:{frm}", "To": f"whatsapp:+{phone}", "Body": body["text"]["body"]})
        if r.status_code >= 300:
            import logging
            logging.getLogger("sfm").warning("Twilio send failed %s: %s", r.status_code, r.text[:300])
        return r.status_code < 300
    r = requests.post(f"{GRAPH}/{ver()}/{os.environ.get('WA_PHONE_ID')}/messages", timeout=15,
                      headers={"Authorization": f"Bearer {os.environ.get('WA_TOKEN')}", "Content-Type": "application/json"},
                      json=dict({"messaging_product": "whatsapp", "to": phone}, **body))
    if r.status_code >= 300:
        import logging
        logging.getLogger("sfm").warning("WhatsApp send failed %s: %s", r.status_code, r.text[:300])
    return r.status_code < 300


def send_text(phone, text):
    """Plain text; long texts are split at line breaks (WhatsApp limit 4096 characters)."""
    ok, chunk = True, ""
    limit = 1500 if provider() == "twilio" else 3800          # Twilio WhatsApp: 1,600 characters per message
    for line in (text or "").split("\n"):
        if len(chunk) + len(line) + 1 > limit:
            ok = _send(phone, {"type": "text", "text": {"body": chunk, "preview_url": False}}) and ok
            chunk = ""
        chunk += ("\n" if chunk else "") + line
    if chunk:
        ok = _send(phone, {"type": "text", "text": {"body": chunk, "preview_url": False}}) and ok
    return ok


def send_document(phone, filename, data, mime, caption=""):
    """Send a PDF / Excel file. Meta: upload to WhatsApp, then send it as a document."""
    if provider() == "twilio":
        return send_text(phone, f"📄 {filename} is ready on the website (Twilio needs a public link to send files).")
    h = {"Authorization": f"Bearer {os.environ.get('WA_TOKEN')}"}
    up = requests.post(f"{GRAPH}/{ver()}/{os.environ.get('WA_PHONE_ID')}/media", headers=h, timeout=30,
                       files={"file": (filename, data, mime)}, data={"messaging_product": "whatsapp", "type": mime})
    if up.status_code >= 300 or not up.json().get("id"):
        import logging
        logging.getLogger("sfm").warning("WhatsApp upload failed %s: %s", up.status_code, up.text[:300])
        return send_text(phone, f"❌ Could not attach {filename}. Download it from the website.")
    return _send(phone, {"type": "document", "document": {"id": up.json()["id"], "filename": filename, "caption": caption[:900]}})


def download_media(media_id):
    if provider() == "twilio":                 # Twilio gives a media URL instead of an id
        f = requests.get(media_id, auth=(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")), timeout=30)
        if f.status_code >= 300:
            raise UserError("Could not download the photo from WhatsApp.")
        return f.content, f.headers.get("Content-Type", "image/jpeg").split(";")[0]
    h = {"Authorization": f"Bearer {os.environ.get('WA_TOKEN')}"}
    r = requests.get(f"{GRAPH}/{ver()}/{media_id}", headers=h, timeout=15)
    if r.status_code >= 300:
        raise UserError("Could not get the photo from WhatsApp.")
    meta = r.json()
    f = requests.get(meta["url"], headers=h, timeout=30)
    if f.status_code >= 300:
        raise UserError("Could not download the photo from WhatsApp.")
    return f.content, meta.get("mime_type", "image/jpeg")


# ------------------------------------------------------------------ 24-hour window, sessions, outbox
def session(cur, phone):
    cur.execute("SELECT * FROM wa_sessions WHERE phone=%s", (phone,))
    s = cur.fetchone()
    return s or {"phone": phone, "last_in": None, "state": {}}


def save_state(cur, phone, state, touch=False):
    cur.execute("""INSERT INTO wa_sessions (phone, last_in, state) VALUES (%s, CASE WHEN %s THEN NOW() END, %s)
                   ON CONFLICT (phone) DO UPDATE SET state=EXCLUDED.state,
                   last_in=CASE WHEN %s THEN NOW() ELSE wa_sessions.last_in END""",
                (phone, touch, json.dumps(state), touch))


def window_open(cur, phone):
    cur.execute("SELECT last_in FROM wa_sessions WHERE phone=%s", (phone,))
    r = cur.fetchone()
    return bool(r and r["last_in"] and datetime.now(r["last_in"].tzinfo) - r["last_in"] < timedelta(hours=23, minutes=30))


def notify(cur, phone, text):
    """Message the assistant starts by itself: sent now if the free 24-hour window is open, otherwise kept
    and delivered when the owner writes next (no paid template messages)."""
    if window_open(cur, phone) and send_text(phone, text):
        return "sent"
    cur.execute("INSERT INTO wa_outbox (phone, text) VALUES (%s,%s)", (phone, text))
    return "waiting"


def sandbox_note(cur, phone, first):
    """Twilio Sandbox ends 3 days after you join: remember when you (re)joined and warn after 60 hours."""
    if provider() != "twilio":
        return ""
    cur.execute("SELECT joined_at FROM wa_sessions WHERE phone=%s", (phone,))
    r = cur.fetchone() or {}
    j = r.get("joined_at")
    now = datetime.now(IST)
    if first or not j or now - j > timedelta(hours=72):
        cur.execute("UPDATE wa_sessions SET joined_at=NOW() WHERE phone=%s", (phone,))
        return ""
    left = timedelta(hours=72) - (now - j)
    if left < timedelta(hours=12):
        code = os.environ.get("TWILIO_SANDBOX_JOIN", "join <your code>")
        return f"\n\n⏳ _WhatsApp link ends in about {max(1, int(left.total_seconds() // 3600))} h — send *{code}* to keep it working._"
    return ""


def deliver_waiting(cur, phone):
    cur.execute("SELECT id, text, created_at FROM wa_outbox WHERE phone=%s AND sent_at IS NULL ORDER BY id", (phone,))
    rows = cur.fetchall()
    if not rows:
        return 0
    text = "📬 *While you were away*\n\n" + "\n\n— — —\n\n".join(
        f"_{r['created_at'].astimezone(IST):%d %b %I:%M %p}_\n{r['text']}" for r in rows)
    send_text(phone, text)
    cur.execute("UPDATE wa_outbox SET sent_at=NOW() WHERE id = ANY(%s)", ([r["id"] for r in rows],))
    return len(rows)


# ------------------------------------------------------------------ replies (same numbers as the website)
def inr(v):
    """Whole rupees in Indian format: -2732.73 → '-₹2,733'."""
    from .scan import indian
    v = Decimal(v or 0)
    return ("-" if v < 0 else "") + "₹" + indian(abs(v))


def cr(v):
    from .util import crore
    return crore(v)


MENU = """👋 *Sai Fuel Mart assistant*
Send a number or a word:

1️⃣ *Tank* — current HSD / MS stock
2️⃣ *Bill* — bill message from photos
3️⃣ *Pending* — transporter credit (or *pending SVT*)
4️⃣ *Closing* — daily closing status
5️⃣ *Reopen* — reopen dates for the manager
6️⃣ *Rates* — see / change HSD, MS, CNG
7️⃣ *Lube* — lube stock / lube arrived
8️⃣ *Report* — today’s / yesterday’s summary
9️⃣ *Alerts* — what needs attention now
🔟 *Profit* — this month (also *variation*, *score*, *compare*)
📄 *svt pdf 26/10* — bill PDF / Excel of one transporter
   (also *svt excel yesterday*, *svt bill day before yesterday*, *svt pdf october*)

Send *menu* any time."""


def tank_text(cur):
    from .services import avg_daily_sale, tank_series
    t = today()
    rows = tank_series(cur, t, t)
    if not rows:
        return "No tanks set up."
    lines = [f"⛽ *Tank stock* — {now_ist():%d %b %I:%M %p}"]
    for r in rows:
        tk = r["tank"]
        stock = r["actual"] if r["actual"] is not None else r["book"]
        src = ("ATG" if r["mode"] == "atg" else "dip") if r["actual"] is not None else "book"
        when = ""
        if r.get("dip") and r["dip"].get("updated_at"):
            when = f", {r['dip']['updated_at'].astimezone(IST):%I:%M %p}"
        cap = Decimal(tk["capacity_l"] or 0)
        pct = f" ({stock / cap * 100:.0f}%)" if (stock is not None and cap) else ""
        avg = avg_daily_sale(cur, tk["fuel_code"], t)
        days = f" · ~{(stock / avg):.1f} days left" if (stock is not None and avg) else ""
        lines.append(f"\n*{tk['name']}*: {stock:,.0f} L{pct}{days}\n  _{src}{when} · capacity {cap:,.0f} L_" if stock is not None
                     else f"\n*{tk['name']}*: no reading yet")
    return "\n".join(lines)


def pending_text(cur, name=None):
    from .insights import credit_scores
    rows = credit_scores(cur, today())
    if name:
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        rows = [x for x in rows if key in re.sub(r"[^a-z0-9]", "", x["party"]["name"].lower())]
        if not rows:
            return f"No transporter matching “{name}”."
        out = []
        for x in rows:
            out.append(f"🚚 *{x['party']['name']}*\nPending: *{inr(x['balance'])}*\nOldest unpaid: {x['oldest_days']} days\n"
                       f"Avg days to pay: {x['avg_days'] if x['avg_days'] is not None else '—'}\nScore: {x['grade']} ({x['score']}/100)"
                       + (f"\nLimit used: {x['use_pct']}%" if x["use_pct"] is not None else ""))
        return "\n\n".join(out)
    rows = sorted([x for x in rows if x["balance"] > 0], key=lambda x: -x["balance"])
    if not rows:
        return "✅ No transporter has anything pending."
    total = sum((x["balance"] for x in rows), Decimal(0))
    lines = [f"🚚 *Pending credit* — total *{inr(total)}*"]
    for x in rows[:20]:
        lines.append(f"• {x['party']['name']}: {inr(x['balance'])} · oldest {x['oldest_days']} d · {x['grade']}")
    lines.append("\nSend *pending <name>* for one transporter.")
    return "\n".join(lines)


def closing_text(cur):
    t = today()
    cur.execute("""SELECT g::date AS d, x.status, x.is_legacy FROM generate_series(%s::date, %s::date, interval '1 day') g
                   LEFT JOIN days x ON x.business_date=g::date ORDER BY 1 DESC""", (t - timedelta(days=6), t))
    icon = {"locked": "🔒 locked", "submitted": "✅ submitted", "open": "⏳ open", None: "⏳ not started"}
    lines = ["📋 *Daily closing — last 7 days*"]
    for r in cur.fetchall():
        st = "📦 old app" if r["is_legacy"] else icon.get(r["status"], r["status"])
        lines.append(f"• {r['d']:%a %d %b}: {st}")
    from .services import compute_day
    try:
        c = compute_day(cur, t - timedelta(days=1))
        if c.get("recon") and not c.get("recon_ok", True):
            lines.append("\n⚠ Yesterday's transporter totals don't match the slips.")
    except Exception:
        pass
    return "\n".join(lines)


def rates_text(cur):
    from .services import rates_for
    r = rates_for(cur, today())
    return "💲 *Today’s rates*\n" + "\n".join(f"• {k}: ₹{v}" for k, v in sorted(r.items())) + \
           "\n\nTo change: *rate HSD 92.50 MS 101.20* (add *tomorrow* or a date like *28/10*)."


def lube_text(cur):
    from .services import lube_stock
    rows = lube_stock(cur)
    lines = ["🛢️ *Lube stock*"]
    for r in rows[:40]:
        low = " ⚠ low" if r.get("stock") is not None and r.get("reorder_level") is not None and r["stock"] <= r["reorder_level"] else ""
        lines.append(f"• {r['name']}: {r.get('stock', 0):g}{low}")
    lines.append("\nArrived: *lube add <name> <qty> @ <purchase rate>*  e.g. *lube add servo 4t 12 @ 280*")
    return "\n".join(lines)


def report_text(cur, d=None):
    """Owner's summary for one day (default: yesterday before 2 PM, else today so far)."""
    from .services import range_summary
    from .automation import compute
    n = now_ist()
    d = d or (n.date() - timedelta(days=1) if n.hour < 14 else n.date())
    day = range_summary(cur, d, d)
    r = day[0] if day else {}
    q = r.get("qty") or {}
    lines = [f"📊 *Report — {d:%a %d %b %Y}*",
             f"Sale: *{inr(r.get('gross_sale'))}* ({cr(r.get('gross_sale'))})",
             f"HSD {Decimal(q.get('HSD') or 0):,.0f} L · MS {Decimal(q.get('MS') or 0):,.0f} L" + (f" · CNG {Decimal(q.get('CNG') or 0):,.0f}" if q.get("CNG") else ""),
             f"Lube: {inr(r.get('lube_total'))}",
             f"Credit given: {inr(r.get('credit'))} · received: {inr(r.get('payments'))}",
             f"Expenses: {inr(r.get('expenses'))}",
             f"Closing: {r.get('status', '—')}"]
    if r.get("difference") is not None:
        diff = Decimal(r["difference"])
        lines.append(f"Cash: {'short' if diff < 0 else 'excess'} {inr(abs(diff))}" if diff else "Cash: ✅ matches")
    cur.execute("""SELECT s.name FROM staff s LEFT JOIN attendance a ON a.staff_id=s.id AND a.work_date=%s
                   WHERE s.active AND (a.status IS NULL OR a.status NOT IN ('P','H')) ORDER BY s.name""", (n.date(),))
    absent = [x["name"] for x in cur.fetchall()]
    if n.hour >= 12 and absent:
        lines.append(f"Absent today: {', '.join(absent[:10])}")
    alerts = [a for a in compute(cur, lambda p: True) if a["sev"] == "high"][:5]
    if alerts:
        lines.append("\n⚠ *Needs attention*\n" + "\n".join(f"• {a['title']}" for a in alerts))
    return "\n".join(lines)


def alerts_text(cur):
    from .automation import compute
    A = compute(cur, lambda p: True)
    if not A:
        return "✅ Nothing needs attention right now."
    mark = {"high": "🔴", "medium": "🟠", "low": "🔵"}
    return "🔔 *What to do now*\n" + "\n".join(f"{mark.get(a['sev'], '•')} {a['title']}\n   _{a['detail']}_" for a in A[:12])


def profit_text(cur):
    from .insights import profit
    t = today()
    p = profit(cur, t.replace(day=1), t)
    lines = [f"💰 *Profit {t:%B} (1–{t.day})*" + ("" if p["final"] else " — _not final_"),
             f"Net profit: *{inr(p['net'])}* ({cr(p['net'])})",
             f"Fuel margin: {inr(p['fuel']['margin'])} on {Decimal(p['fuel']['litres']):,.0f} L"]
    for f in p["fuels"]:
        lines.append(f"  • {f['code']}: {inr(f['margin'])} (₹{f['margin_per_l']}/L, cost ₹{f['cost_per_l']}/L)")
    lines += [f"Lube margin: {inr(p['lube']['margin'])}", f"Stock loss/gain: {inr(p['stock_diff'])}",
              f"Expenses + staff: {inr(p['expenses_total'] + p['staff_total'])}"]
    if p["issues"]:
        lines.append("\n⚠ *Missing before it is final:*\n" + "\n".join(f"• {i}" for i in p["issues"][:6]))
    return "\n".join(lines)


def variation_text(cur):
    from .insights import tank_variation
    t = today()
    lines = [f"🧪 *Tank variation {t:%B}*"]
    for v in tank_variation(cur, t.replace(day=1), t):
        lines.append(f"• {v['tank']['name']}: {v['variance']:,.0f} L ({v['pct'] if v['pct'] is not None else '—'}%) "
                     f"= {inr(v['value'])} · *{v['verdict']}*" + (f" · {v['trend']}" if v["trend"] else ""))
    return "\n".join(lines)


def score_text(cur):
    from .insights import credit_scores
    rows = credit_scores(cur, today())
    return "🏷️ *Transporter credit score* (weakest first)\n" + "\n".join(
        f"• {x['party']['name']}: *{x['grade']}* {x['score']}/100 · pending {inr(x['balance'])}" for x in rows[:15]) if rows else "No transporters yet."


def compare_text(cur):
    from .insights import month_compare
    m = month_compare(cur, today())
    lines = [f"📈 *This month vs same days last month* ({m['now']['days']} days)"]
    for r in m["rows"]:
        val = (lambda v: inr(v)) if r["money"] else (lambda v: f"{Decimal(v):,.0f} L")
        arrow = "" if r["pct"] is None else (f" ↑{r['pct']}%" if r["pct"] >= 0 else f" ↓{abs(r['pct'])}%")
        lines.append(f"• {r['label']}: {val(r['now'])} vs {val(r['same'])}{arrow}")
    return "\n".join(lines)


# ------------------------------------------------------------------ understanding the owner's message
def _date(s, default=None):
    from .scan import parse_date as pd
    s = s.strip().lower()
    if s in ("today", "aaj"):
        return today()
    if s in ("tomorrow", "kal"):
        return today() + timedelta(days=1)
    if s == "yesterday":
        return today() - timedelta(days=1)
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?$", s)
    if m:
        y = m.group(3) or str(today().year)
        return pd(f"{m.group(1)}.{m.group(2)}.{y}", default)
    return default


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _when(low):
    """→ ('day', date) | ('month', first_day) | None, from words like yesterday, 26/10, 26th october 2026, october."""
    from datetime import date as _d
    t = today()
    if re.search(r"day before yesterday|parso|parson", low):
        return ("day", t - timedelta(days=2))
    if re.search(r"\byesterday\b|\bkal\b", low):
        return ("day", t - timedelta(days=1))
    if re.search(r"\btoday\b|\baaj\b", low):
        return ("day", t)
    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", low)
    if m:
        y = int(m.group(3) or t.year); y = y + 2000 if y < 100 else y
        try:
            return ("day", _d(y, int(m.group(2)), int(m.group(1))))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+(\d{4}))?", low)
    if m:
        try:
            return ("day", _d(int(m.group(3) or t.year), MONTHS[m.group(2)], int(m.group(1))))
        except ValueError:
            return None
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b(?:\s+(\d{4}))?", low)
    if m:
        y = int(m.group(2) or t.year)
        if (y, MONTHS[m.group(1)]) > (t.year, t.month):
            y -= 1
        return ("month", _d(y, MONTHS[m.group(1)], 1))
    return None


def _which_party(cur, low):
    """Transporter named in the message: full name, first word, or initials (SVT for 'SVT Logistics')."""
    cur.execute("SELECT id, name FROM parties WHERE active ORDER BY name")
    parties = cur.fetchall()
    words = set(re.findall(r"[a-z0-9]+", low))
    hits = []
    for p in parties:
        nm = re.sub(r"[^a-z0-9 ]", " ", p["name"].lower()).split()
        initials = "".join(w[0] for w in nm)
        if " ".join(nm) in low or (nm and nm[0] in words and len(nm[0]) >= 3) or (len(initials) >= 2 and initials in words):
            hits.append(p)
    return hits, parties


def wants_documents(low):
    """PDF/Excel words, or the word "bill" together with a date ("svt bill yesterday"). Plain "bill" or
    "bill svt" starts the photo bill-message flow instead."""
    if re.search(r"\b(pdf|excel|xls|xlsx|sheet)\b", low):
        return True
    return bool(re.search(r"\bbill\b", low)) and _when(low) is not None


def documents_reply(cur, low):
    """Bill PDF / Excel of ONE transporter for ONE date (or one month) — the same files as the website."""
    from .exports import bill_filename, transport_bill_pdf, transport_bill_xlsx
    from .services import settings_map, transport_register
    when = _when(low)
    hits, parties = _which_party(cur, low)
    if len(hits) != 1:
        names = ", ".join(p["name"] for p in (hits or parties)[:12])
        return ("Which transporter? " if not hits else "More than one matches: ") + names + \
               "\nExample: *svt pdf 26/10/2026* · *svt excel yesterday* · *svt pdf october*"
    p = hits[0]
    if when is None:
        when = ("day", today())
    kind, d = when
    if kind == "day":
        start = end = d
        label = d.strftime("%d %b %Y")
    else:
        from .util import month_bounds
        start, end = month_bounds(d.strftime("%Y-%m"))
        end = min(end, today())
        label = d.strftime("%B %Y")
    want_pdf = bool(re.search(r"\bpdf\b", low))
    want_xls = bool(re.search(r"\b(excel|xls|xlsx|sheet)\b", low))
    if re.search(r"\bbill\b", low) or not (want_pdf or want_xls):
        want_pdf = want_xls = True
    cur.execute("SELECT * FROM parties WHERE id=%s", (p["id"],))
    party = cur.fetchone()
    rows = transport_register(cur, start, end, p["id"], include_voided=False)
    if not rows:
        return f"No credit transport entries for *{p['name']}* on {label}."
    st = settings_map(cur)
    total = sum((Decimal(r["debit"] or 0) for r in rows), Decimal(0))
    qty = sum((Decimal(r["qty"] or 0) for r in rows), Decimal(0))
    caption = f"{p['name']} · {label} · {len(rows)} entries · {qty:,.2f} L · {inr(total)}"
    docs = []
    if want_pdf:
        docs.append((bill_filename(p["name"], start, "pdf", end), transport_bill_pdf(party, rows, start, end, st).getvalue(), "application/pdf"))
    if want_xls:
        docs.append((bill_filename(p["name"], start, "xlsx", end), transport_bill_xlsx(party, rows, start, end, st).getvalue(),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    return {"docs": docs, "caption": caption, "reply": f"📄 {caption}"}


def handle(cur, phone, user, msg):
    """One incoming WhatsApp message → reply text (or None when a background job will answer)."""
    s = session(cur, phone)
    state = s["state"] or {}
    mtype = msg.get("type")
    text = (msg.get("text") or {}).get("body", "") if mtype == "text" else (msg.get("image", {}) or {}).get("caption", "")
    low = re.sub(r"\s+", " ", text.strip().lower())

    # ---- photos for a bill message
    if mtype in ("image", "document"):
        media = msg.get("image") or msg.get("document") or {}
        if state.get("mode") != "bill":
            state = {"mode": "bill", "photos": [], "party": None}
        state["photos"].append(media.get("id"))
        save_state(cur, phone, state, touch=True)
        n = len(state["photos"])
        return f"📷 {n} bill photo{'s' if n > 1 else ''} received. Send more, or *done* to make the message." if n == 1 or n % 3 == 0 else None

    if low in ("cancel", "stop", "no") and state.get("mode"):
        save_state(cur, phone, {}, touch=True)
        return "Cancelled. Send *menu* for options."

    # ---- confirmations
    if state.get("mode") == "confirm":
        if low in ("yes", "y", "ok", "haan", "ha", "confirm"):
            save_state(cur, phone, {}, touch=True)
            return run_confirmed(cur, state)
        save_state(cur, phone, {}, touch=True)
        return "Not done. Send *menu* for options."

    # ---- bill flow
    if state.get("mode") == "bill" and low in ("done", "ok", "make", "finish", "bas"):
        photos = state.get("photos") or []
        if not photos:
            return "No photos yet. Send the bill photos first."
        cur.execute("INSERT INTO wa_jobs (phone, kind, data) VALUES (%s,'bill',%s) RETURNING id",
                    (phone, json.dumps({"photos": photos, "party": state.get("party"), "user": user["username"]})))
        jid = cur.fetchone()["id"]
        save_state(cur, phone, {"mode": "bill_wait", "job": jid}, touch=True)
        return {"start_job": jid, "reply": f"⏳ Reading {len(photos)} bill{'s' if len(photos) > 1 else ''}… about {max(8, len(photos) * 4)} seconds."}

    if low.startswith("join "):                 # Twilio Sandbox join message
        save_state(cur, phone, {}, touch=True)
        cur.execute("UPDATE wa_sessions SET joined_at=NOW() WHERE phone=%s", (phone,))
        return "✅ Connected to the Sai Fuel Mart assistant.\n\n" + MENU
    if wants_documents(low):
        return documents_reply(cur, low)
    words = low.split(" ")
    cmd, rest = (words[0], " ".join(words[1:])) if words else ("", "")
    if low in ("hi", "hello", "hey", "menu", "help", "0", "start") or not low:
        save_state(cur, phone, {}, touch=True)
        return MENU
    if cmd in ("1", "tank", "tanks", "stock"):
        return tank_text(cur)
    if cmd in ("2", "bill", "bills"):
        save_state(cur, phone, {"mode": "bill", "photos": [], "party": rest or None}, touch=True)
        return ("🧾 *Bill message*" + (f" for *{rest}*" if rest else "") +
                "\nSend the bill photos now (one photo per bill, any number). Then send *done*.")
    if cmd in ("3", "pending", "credit", "due"):
        return pending_text(cur, rest or None)
    if cmd in ("4", "closing", "close"):
        return closing_text(cur)
    if cmd in ("5", "reopen", "open"):
        return ask_reopen(cur, phone, rest)
    if cmd in ("6", "rate", "rates", "price", "prices"):
        return ask_rates(cur, phone, rest) if rest else rates_text(cur)
    if cmd in ("7", "lube", "lubes"):
        return ask_lube(cur, phone, rest) if rest.startswith("add ") else lube_text(cur)
    if cmd in ("8", "report", "today", "summary"):
        d = _date(rest) if rest else None
        return report_text(cur, d)
    if cmd in ("9", "alerts", "alert", "todo"):
        return alerts_text(cur)
    if cmd in ("10", "profit"):
        return profit_text(cur)
    if cmd in ("variation", "loss"):
        return variation_text(cur)
    if cmd in ("score", "scores"):
        return score_text(cur)
    if cmd in ("compare", "comparison"):
        return compare_text(cur)
    return "🤔 I didn't understand that.\n\n" + MENU


def ask_reopen(cur, phone, rest):
    if not rest:
        return ("🔓 *Reopen dates for the manager*\nSend for example:\n• *reopen 20/10*\n• *reopen 18/10-20/10*\n"
                "• *reopen 20/10 for ramesh* (choose the login)\nIt stays open 24 hours, then locks again by itself.")
    m = re.match(r"^(\S+?)(?:\s*(?:-|to)\s*(\S+))?(?:\s+for\s+(.+))?$", rest)
    d1 = _date(m.group(1)) if m else None
    d2 = _date(m.group(2)) if m and m.group(2) else d1
    if not d1 or not d2:
        return "Date not clear. Example: *reopen 20/10* or *reopen 18/10-20/10*."
    who = (m.group(3) or "").strip() if m else ""
    if who:
        cur.execute("SELECT id, username, full_name FROM users WHERE active AND role<>'owner' AND (LOWER(username)=%s OR LOWER(full_name) LIKE %s)",
                    (who.lower(), f"%{who.lower()}%"))
    else:
        cur.execute("SELECT id, username, full_name FROM users WHERE active AND role='manager' ORDER BY id")
    users = cur.fetchall()
    if len(users) != 1:
        names = ", ".join(u["username"] for u in users) or "none"
        return f"Which login? Add *for <username>* — managers/logins found: {names}."
    u = users[0]
    save_state(cur, phone, {"mode": "confirm", "action": "reopen", "user_id": u["id"], "user": u["username"],
                            "d1": d1.isoformat(), "d2": d2.isoformat()}, touch=True)
    return (f"🔓 Reopen *{d1:%d %b}*" + (f" – *{d2:%d %b}*" if d2 != d1 else "") +
            f" for *{u['full_name'] or u['username']}* for 24 hours?\nReply *YES* to confirm.")


def ask_rates(cur, phone, rest):
    pairs = re.findall(r"\b(hsd|ms|cng|diesel|petrol)\s*[:=]?\s*(\d{1,4}(?:\.\d{1,2})?)", rest)
    alias = {"diesel": "HSD", "petrol": "MS"}
    rates = {alias.get(k, k.upper()): v for k, v in pairs}
    if not rates:
        return "Example: *rate HSD 92.50 MS 101.20* (add *tomorrow* or a date)."
    tail = re.sub(r"\b(hsd|ms|cng|diesel|petrol)\s*[:=]?\s*\d{1,4}(?:\.\d{1,2})?", "", rest).strip()
    d = _date(tail.replace("from", "").strip()) if tail else today()
    if not d:
        return "Date not clear. Use *tomorrow* or a date like *28/10*."
    save_state(cur, phone, {"mode": "confirm", "action": "rates", "rates": rates, "d": d.isoformat()}, touch=True)
    from .services import rates_for
    now_r = rates_for(cur, d)
    lines = [f"💲 Change rates from *{d:%d %b %Y}*" + (" (6 AM price)" if d > today() else "") + ":"]
    for k, v in rates.items():
        lines.append(f"• {k}: ₹{now_r.get(k, '—')} → *₹{v}*")
    return "\n".join(lines) + "\nReply *YES* to confirm."


def ask_lube(cur, phone, rest):
    m = re.match(r"^add\s+(.+?)\s+(\d+(?:\.\d+)?)\s*(?:@|at|rate)\s*(\d+(?:\.\d+)?)$", rest)
    if not m:
        return "Example: *lube add servo 4t 12 @ 280* (name, quantity, purchase rate)."
    name, qty, rate = m.group(1), m.group(2), m.group(3)
    cur.execute("SELECT id, name FROM lube_products WHERE active AND LOWER(name) LIKE %s ORDER BY name", (f"%{name.lower()}%",))
    found = cur.fetchall()
    if len(found) != 1:
        return (f"Which product? Found: {', '.join(p['name'] for p in found[:8])}" if found else f"No lube product matching “{name}”.")
    p = found[0]
    save_state(cur, phone, {"mode": "confirm", "action": "lube", "product_id": p["id"], "name": p["name"], "qty": qty, "rate": rate}, touch=True)
    return f"🛢️ Lube arrived: *{p['name']}* +{qty} @ ₹{rate} (₹{Decimal(qty) * Decimal(rate):,.2f})?\nReply *YES* to confirm."


def run_confirmed(cur, st):
    from .actions import lube_purchase, reopen_for, set_rates
    from datetime import date
    try:
        if st["action"] == "reopen":
            g_ = reopen_for(cur, st["user_id"], date.fromisoformat(st["d1"]), date.fromisoformat(st["d2"]), 24,
                            "Reopened from WhatsApp by the owner")
            return (f"✅ Opened for *{st['user']}* until {g_['until']:%d %b %I:%M %p}. "
                    f"{len(g_['reopened'])} locked day(s) opened; they lock again automatically.")
        if st["action"] == "rates":
            ch = set_rates(cur, date.fromisoformat(st["d"]), {k: Decimal(v) for k, v in st["rates"].items()})
            return ("✅ Rates saved from " + date.fromisoformat(st["d"]).strftime("%d %b") + ":\n" +
                    "\n".join(f"• {c}: ₹{a or '—'} → ₹{b}" for c, a, b in ch)) if ch else "No rate changed (same as now)."
        if st["action"] == "lube":
            lube_purchase(cur, st["product_id"], st["qty"], st["rate"])
            return f"✅ {st['name']}: +{st['qty']} @ ₹{st['rate']} saved."
    except UserError as e:
        return f"❌ Not done: {e}"
    return "Nothing to confirm."


# ------------------------------------------------------------------ bill message from photos (background)
def bill_job_step(cur, jid, idx):
    """Read photo number idx of a bill job; when all are read, build and send the message (once)."""
    from .scan import bill_summary, read_bill_total
    cur.execute("SELECT * FROM wa_jobs WHERE id=%s", (jid,))
    j = cur.fetchone()
    if not j or j["finished"]:
        return
    photos = j["data"]["photos"]
    if str(idx) not in (j["results"] or {}):
        try:
            data, mime = download_media(photos[idx])
            raw = read_bill_total(data, mime)
            res = {"ok": True, "raw": raw}
        except UserError as e:
            res = {"ok": False, "error": str(e)}
        except Exception:
            res = {"ok": False, "error": "could not read"}
        cur.execute("UPDATE wa_jobs SET results = results || %s::jsonb WHERE id=%s", (json.dumps({str(idx): res}), jid))
    cur.execute("UPDATE wa_jobs SET finished=TRUE WHERE id=%s AND NOT finished AND (SELECT COUNT(*) FROM jsonb_object_keys(results)) >= %s RETURNING *",
                (jid, len(photos)))
    done = cur.fetchone()
    if done:
        finish_bill_job(cur, done)


def finish_bill_job(cur, j):
    from .scan import bill_message, bill_summary, match_party
    results = j["results"]
    bills, failed = [], []
    for i in range(len(j["data"]["photos"])):
        r = results.get(str(i), {})
        if r.get("ok"):
            bills.append(dict(bill_summary(r["raw"]), n=i + 1, raw=r["raw"]))
        else:
            failed.append(i + 1)
    bills.sort(key=lambda b: (b["date"] is None, b["date"] or today()))
    name = None
    want = j["data"].get("party")
    if want or bills:
        p, _ = match_party(cur, want or next((b["party"] for b in bills if b["party"]), ""))
        name = p["name"] if p else (want or "")
        pid = p["id"] if p else None
    else:
        pid = None
    msg = bill_message(name or "Transporter", bills) if bills else ""
    # keep it in the website's Bill Message history too
    cur.execute("INSERT INTO msg_batches (created_by, party_id, sent_text) VALUES (%s,%s,%s) RETURNING id",
                (j["data"].get("user", "whatsapp") + " (WhatsApp)", pid, msg))
    bid = cur.fetchone()["id"]
    for b in bills:
        cur.execute("INSERT INTO msg_bills (batch_id, raw) VALUES (%s,%s)", (bid, json.dumps(b["raw"])))
    notes = [f"⚠ Bill {b['label']}: TOTAL doesn't match its rows — check it" for b in bills if not b["ok"]]
    notes += [f"⚠ Photo {n} could not be read — send it again" for n in failed]
    phone = j["phone"]
    if msg:
        send_text(phone, msg)          # the message alone, ready to forward
    send_text(phone, ("\n".join(notes) + "\n\n" if notes else "✅ All totals checked.\n\n") +
              "Forward the message above to the transporter. Send *bill* for another one, or *menu*.")
    cur.execute("UPDATE wa_sessions SET state='{}'::jsonb WHERE phone=%s", (phone,))
