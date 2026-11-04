"""Small helpers shared by every module."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ZERO = Decimal("0")
PAISA = Decimal("0.01")


class UserError(Exception):
    """A problem the person can fix (bad input, locked day...). Shown as-is."""


def now_ist():
    return datetime.now(IST)


def today():
    # Render runs on UTC; the pump runs on IST. Never use date.today().
    return now_ist().date()


def parse_date(value, field="Date"):
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise UserError(f"{field} is not a valid date.")


def to_dec(value, field="Amount", allow_negative=False, allow_blank=True, places=PAISA):
    """Parse user input into an exact Decimal. Rejects junk instead of
    silently turning it into 0 the way float(x or 0) did."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        if allow_blank:
            return ZERO
        raise UserError(f"{field} is required.")
    if isinstance(value, Decimal):
        d = value
    else:
        s = str(value).strip().replace(",", "").replace("₹", "")
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise UserError(f"{field} must be a number (got “{value}”).")
    if not d.is_finite():
        raise UserError(f"{field} must be a number.")
    if d < 0 and not allow_negative:
        raise UserError(f"{field} cannot be negative.")
    if abs(d) > Decimal("999999999"):
        raise UserError(f"{field} is too large.")
    return d.quantize(places, rounding=ROUND_HALF_UP)


def rupee(d):
    """Nearest whole rupee (half rounds up): 9082.19 → 9082, 9082.50 → 9083."""
    return Decimal(d or 0).quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(PAISA)


def crore(value):
    """₹ in crores, two decimals: 12403550 → '₹ 1.24 Cr'."""
    v = Decimal(value or 0) / Decimal(10000000)
    return f"₹ {v.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f} Cr"


def rs(value, symbol=True):
    """Whole-rupee display: ₹9,082"""
    return inr(value, symbol=symbol, decimals=0)


def money(d):
    return (Decimal(d or 0)).quantize(PAISA, rounding=ROUND_HALF_UP)


def inr(value, symbol=True, decimals=2):
    """Indian digit grouping: 12,34,567.89"""
    if value is None:
        value = 0
    d = Decimal(value).quantize(Decimal(1).scaleb(-decimals) if decimals else Decimal(1),
                                rounding=ROUND_HALF_UP)
    neg = d < 0
    d = abs(d)
    s = f"{d:.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    out = whole + ("." + frac if decimals else "")
    if symbol:
        out = "₹" + out
    return ("−" if neg else "") + out


def qty(value, decimals=2):
    return inr(value, symbol=False, decimals=decimals)


def fy_label(d):
    """Indian financial year, e.g. 2026-27 for any date Apr 2026–Mar 2027."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def month_bounds(ym):
    y, m = (int(x) for x in ym.split("-"))
    first = date(y, m, 1)
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return first, nxt - timedelta(days=1)


def fmt_date(d):
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = parse_date(d)
        except UserError:
            return d
    return d.strftime("%d %b %Y")


def clean(text, limit=200):
    return (text or "").strip()[:limit]
