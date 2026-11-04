"""The owner's SFM_Daily_Readings.xlsx, kept in the website.

Rows are ADDED into the prepared formula rows — only the cells a person would type are written, plus the
calculated results of the formulas in those rows (the converter reads results). Every other byte of the file
(other rows, styles, dropdown lists, hidden master sheets, formulas) stays exactly as it was. No row is deleted.
Dates are written like the recent rows: day 1–12 as an Excel date shown mm-dd-yy (stored month = day, day = month,
which displays day-first), day 13–31 as text "15/10/2026".
"""
import io
import re
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

SALES = {"HSD": "HSD SALES", "MS": "MS SALES", "CNG": "CNG SALES"}
LUBE, PAYMENT, RECEIPT, METER = "LUBE SALES", "PAYMENT", "RECEIPT", "METER READING"
EXCEL_EPOCH = date(1899, 12, 30)


def _r2(x, places="0.01"):
    return Decimal(str(x)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def col_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def split_ref(ref):
    m = re.match(r"([A-Z]+)(\d+)$", ref)
    return m.group(1), int(m.group(2))


_VIEW = {}


class Book:
    """Raw xlsx edited at XML level (only the cells we touch change)."""

    def __init__(self, data: bytes):
        self.data = data
        self.z = zipfile.ZipFile(io.BytesIO(data))
        wb = self.z.read("xl/workbook.xml").decode("utf8")
        rels = self.z.read("xl/_rels/workbook.xml.rels").decode("utf8")
        rid = {m.group(1): m.group(2) for m in re.finditer(r'<Relationship [^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"', rels)}
        rid.update({m.group(2): m.group(1) for m in re.finditer(r'<Relationship [^>]*?Target="([^"]+)"[^>]*?Id="([^"]+)"', rels)})
        self.paths = {}
        for m in re.finditer(r'<sheet [^>]*?name="([^"]+)"[^>]*?r:id="([^"]+)"', wb):
            t = rid[m.group(2)].lstrip("/")
            self.paths[m.group(1).replace("&amp;", "&")] = t if t.startswith("xl/") else "xl/" + t
        self.xml = {}
        self.values = None      # openpyxl data_only view (cached results), loaded on demand

    # ---------- reading
    def sheet_xml(self, name):
        if name not in self.xml:
            self.xml[name] = self.z.read(self.paths[name]).decode("utf8")
        return self.xml[name]

    def view(self):
        if self.values is None:
            import hashlib
            import openpyxl
            key = hashlib.sha1(self.data).hexdigest()
            if _VIEW.get("key") != key:                     # read once per version (warm server reuses it)
                _VIEW.clear(); _VIEW.update(key=key, wb=openpyxl.load_workbook(io.BytesIO(self.data), data_only=True))
            self.values = _VIEW["wb"]
        return self.values

    # ---------- writing one cell
    def _row_span(self, xml, r):
        m = re.search(rf'<row r="{r}"[^>]*?(?:/>|>.*?</row>)', xml, re.S)
        if m:
            return m
        return None

    def put(self, sheet, ref, *, value=None, kind="n", formula_keep=True, style=None, new_formula=None, array=False):
        """kind: n (number) | s (text) | e (empty text result). Keeps an existing formula (only its result changes)
        unless new_formula is given; with no formula, writes a typed value."""
        xml = self.sheet_xml(sheet)
        col, r = split_ref(ref)
        rm = self._row_span(xml, r)
        if rm is None:                                       # a row that does not exist yet: add it in order
            nxt = next((m for m in re.finditer(r'<row r="(\d+)"', xml) if int(m.group(1)) > r), None)
            pos = nxt.start() if nxt else xml.index("</sheetData>")
            xml = xml[:pos] + f'<row r="{r}"></row>' + xml[pos:]
            rm = self._row_span(xml, r)
        row = rm.group(0)
        if row.endswith("/>"):
            row = row[:-2] + "></row>"
        cm = re.search(rf'<c r="{ref}"(?P<a>[^>]*?)(?:/>|>(?P<in>.*?)</c>)', row, re.S)
        attrs = cm.group("a") if cm else ""
        inner = (cm.group("in") or "") if cm else ""
        s = re.search(r' s="(\d+)"', attrs)
        s = style if style is not None else (s.group(1) if s else None)
        f = re.search(r"<f[^>]*?(?:/>|>.*?</f>)", inner, re.S)
        ftag = None
        if new_formula is not None:
            ftag = (f'<f t="array" ref="{ref}">{escape(new_formula)}</f>' if array else f"<f>{escape(new_formula)}</f>")
        elif f and formula_keep:
            ftag = f.group(0)
        extra = ' cm="1"' if (ftag and 't="array"' in ftag) else ""
        sa = f' s="{s}"' if s is not None else ""
        if ftag:
            if kind == "n":
                cell = f'<c r="{ref}"{sa}{extra}>{ftag}<v>{value}</v></c>'
            elif kind == "e":
                cell = f'<c r="{ref}"{sa} t="str"{extra}>{ftag}<v></v></c>'
            else:
                cell = f'<c r="{ref}"{sa} t="str"{extra}>{ftag}<v>{escape(str(value))}</v></c>'
        else:
            if kind == "n":
                cell = f'<c r="{ref}"{sa}><v>{value}</v></c>'
            else:
                cell = f'<c r="{ref}"{sa} t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
        if cm:
            row = row[:cm.start()] + cell + row[cm.end():]
        else:                                                 # keep cells in column order
            after = next((m for m in re.finditer(r'<c r="([A-Z]+)\d+"', row) if col_num(m.group(1)) > col_num(col)), None)
            pos = after.start() if after else row.rindex("</row>")
            row = row[:pos] + cell + row[pos:]
        self.xml[sheet] = xml[:rm.start()] + row + xml[rm.end():]

    def style_of(self, sheet, ref):
        xml = self.sheet_xml(sheet)
        m = re.search(rf'<c r="{ref}"[^>]*? s="(\d+)"', xml)
        return m.group(1) if m else None

    def save(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
            for item in self.z.infolist():
                data = self.z.read(item.filename)
                for name, path in self.paths.items():
                    if item.filename == path and name in self.xml:
                        data = self.xml[name].encode("utf8")
                if item.filename == "xl/workbook.xml":          # Excel recalculates everything when the file is opened
                    t = data.decode("utf8")
                    t = re.sub(r"<calcPr([^>]*?)/>", lambda m: "<calcPr" + re.sub(r' fullCalcOnLoad="[^"]*"', "", m.group(1)) + ' fullCalcOnLoad="1"/>', t)
                    data = t.encode("utf8")
                zo.writestr(item, data)
        return out.getvalue()


# ------------------------------------------------------------------ dates exactly like the recent rows
def date_cell(d: date):
    """→ (kind, value): day 1–12 → Excel date shown mm-dd-yy (displays day-first); day 13–31 → text d/m/yyyy."""
    if d.day <= 12:
        stored = date(d.year, d.day, d.month)
        return "n", (stored - EXCEL_EPOCH).days
    return "s", f"{d.day}/{d.month}/{d.year}"


def read_date(v):
    """The way the workbook's dates are meant (same rule as Converter Studio V16 'SFM displayed dates')."""
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return date(v.year, v.day, v.month) if v.day <= 12 else v
    if isinstance(v, str):
        m = re.match(r"\s*'?(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", v)
        if m:
            y = int(m.group(3)); y = y + 2000 if y < 100 else y
            try:
                return date(y, int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None
    return None


def data_end(ws):
    return max((r for r in range(2, ws.max_row + 1) if ws.cell(r, 2).value not in (None, "")), default=1)


def rows_for(ws, d, first=2):
    return [r for r in range(first, data_end(ws) + 1) if read_date(ws.cell(r, 2).value) == d]


# ------------------------------------------------------------------ adding rows
class Adder:
    def __init__(self, data: bytes):
        self.b = Book(data)
        self.v = self.b.view()
        self.next_row = {}
        self.cache = {}            # (sheet, ref) → value we calculated for new rows
        self.added = []            # (sheet, row, summary)

    def _val(self, sheet, ref):
        if (sheet, ref) in self.cache:
            return self.cache[(sheet, ref)]
        return self.v[sheet][ref].value

    def _date_style(self, sheet, kind):
        ws = self.v[sheet]
        for r in range(data_end(ws), 1, -1):
            c = ws.cell(r, 2)
            if c.value in (None, ""):
                continue
            if (kind == "n") == (not isinstance(c.value, str)):
                return self.b.style_of(sheet, f"B{r}")
        return None

    def _row(self, sheet):
        if sheet not in self.next_row:
            self.next_row[sheet] = data_end(self.v[sheet]) + 1
        r = self.next_row[sheet]
        self.next_row[sheet] += 1
        return r

    def _put_date(self, sheet, r, d):
        kind, val = date_cell(d)
        self.b.put(sheet, f"B{r}", value=val, kind=kind, formula_keep=False, style=self._date_style(sheet, kind))
        self.cache[(sheet, f"B{r}")] = d

    def sale(self, fuel, d, party, amount, rate=None):
        sh = SALES[fuel]
        r = self._row(sh)
        self._put_date(sh, r, d)
        self.b.put(sh, f"C{r}", value=party, kind="s", formula_keep=False)
        amount = _r2(amount, "1") if Decimal(str(amount)) == int(Decimal(str(amount))) else _r2(amount)
        self.b.put(sh, f"F{r}", value=amount, kind="n", formula_keep=False)
        carried = self._val(sh, f"E{r - 1}")
        rate = Decimal(str(rate)) if rate is not None else Decimal(str(carried))
        if carried is None or Decimal(str(carried)) != rate:
            self.b.put(sh, f"E{r}", value=rate, kind="n", formula_keep=False)        # new rate typed, like a person would
        else:
            self.b.put(sh, f"E{r}", value=rate, kind="n")                            # keep the "carry from above" formula
        self.cache[(sh, f"E{r}")] = rate
        self.cache[(sh, f"F{r}")] = Decimal(str(amount))
        qty = _r2(Decimal(str(amount)) / rate)
        self.b.put(sh, f"D{r}", value=qty, kind="n"); self.cache[(sh, f"D{r}")] = qty
        self.b.put(sh, f"A{r}", value=r - 1, kind="n")
        self.added.append((sh, r, f"{party} {amount}"))
        return r

    def lube(self, d, party, item, qty, rate):
        sh = LUBE
        r = self._row(sh)
        self._put_date(sh, r, d)
        self.b.put(sh, f"C{r}", value=party, kind="s", formula_keep=False)
        self.b.put(sh, f"D{r}", value=item, kind="s", formula_keep=False)
        self.b.put(sh, f"E{r}", value=Decimal(str(qty)).normalize(), kind="n", formula_keep=False)
        self.b.put(sh, f"F{r}", value=Decimal(str(rate)).normalize(), kind="n", formula_keep=False)
        amt = _r2(Decimal(str(qty)) * Decimal(str(rate)), "1")
        self.b.put(sh, f"G{r}", value=amt, kind="n"); self.cache[(sh, f"G{r}")] = amt
        self.b.put(sh, f"A{r}", value=r - 1, kind="n")
        self.added.append((sh, r, f"{party} {item} {qty}×{rate}"))
        return r

    def voucher(self, sheet, d, from_ledger, to_ledger, amount):
        """PAYMENT (Paid From, Paid To) or RECEIPT (Received In, Received From)."""
        r = self._row(sheet)
        self._put_date(sheet, r, d)
        self.b.put(sheet, f"C{r}", value=from_ledger, kind="s", formula_keep=False)
        self.b.put(sheet, f"D{r}", value=to_ledger, kind="s", formula_keep=False)
        self.b.put(sheet, f"E{r}", value=Decimal(str(amount)), kind="n", formula_keep=False)
        self.cache[(sheet, f"E{r}")] = Decimal(str(amount))
        self.b.put(sheet, f"A{r}", value=r - 1, kind="n")
        self.added.append((sheet, r, f"{from_ledger} → {to_ledger} {amount}"))
        return r

    def meters(self, d, closings):
        """closings: {pump meter name: closing}. The 8 rows of that date are fixed by Q1 + 8 rows per day."""
        ws = self.v[METER]
        q1 = ws["Q1"].value
        q1 = q1.date() if isinstance(q1, datetime) else q1
        names = [ws.cell(r, 18).value for r in range(4, 12)]
        master = {}
        mm = self.v["METER MASTER"]
        for r in range(2, mm.max_row + 1):
            if mm.cell(r, 1).value:
                master[mm.cell(r, 1).value] = [mm.cell(r, c).value for c in range(2, 6)]      # MPD, product, unit, rate
        base = 90 + (d - q1).days * 8
        closings = self._match_meters(closings, names, base)
        unknown = [n for n in closings if n not in names]
        if unknown:
            from .util import UserError
            raise UserError("Not a pump meter of this workbook: " + ", ".join(unknown) + " — fix it in Tally names.")
        plan, problems = [], []
        for i, name in enumerate(names):
            r = base + i
            if name not in closings:
                continue
            prev = None
            for rr in range(r - 1, 1, -1):                    # opening = last closing of the same meter
                if self._val(METER, f"D{rr}") == name and self._val(METER, f"G{rr}") is not None:
                    prev = Decimal(str(self._val(METER, f"G{rr}"))); break
            closing = Decimal(str(closings[name]))
            if prev is None:
                problems.append(f"{name}: no earlier closing in the sheet")
            elif not (0 <= closing - prev <= 50000):
                problems.append(f"{name}: closing {closing} vs opening {prev} — wrong nozzle chosen in Tally names?")
            plan.append((i, name, r, prev, closing))
        if problems:
            from .util import UserError
            raise UserError("Meter readings look wrong, nothing added: " + "; ".join(problems))
        for i, name, r, prev, closing in plan:
            mpd, product, unit, rate = master.get(name, [None, None, None, None])
            testing = Decimal(0) if product == "CNG" else Decimal(5)
            net = _r2(closing - prev - testing)
            b = self.b
            b.put(METER, f"B{r}", value=f"{d.day}/{d.month}/{d.year}", kind="s",
                  new_formula=f'TEXT($Q$1+INT((ROWS($B$90:B{r})-1)/8),"d/m/yyyy")', style=b.style_of(METER, "B1041") or b.style_of(METER, f"B{r - 8}"))
            b.put(METER, f"D{r}", value=name, kind="s", new_formula=f"INDEX($R$4:$R$11,MOD(ROWS($B$90:B{r})-1,8)+1)",
                  array=True, style=b.style_of(METER, f"D{r - 8}"))
            b.put(METER, f"G{r}", value=closing, kind="n", formula_keep=False, style=b.style_of(METER, f"G{r - 8}"))
            self.cache.update({(METER, f"D{r}"): name, (METER, f"G{r}"): closing})
            n_before = sum(1 for rr in range(2, r) if self._val(METER, f"B{rr}") not in (None, ""))
            b.put(METER, f"A{r}", value=n_before + 1, kind="n")
            self.cache[(METER, f"B{r}")] = f"{d.day}/{d.month}/{d.year}"
            for c, v in (("C", mpd), ("E", product), ("J", unit)):
                b.put(METER, f"{c}{r}", value=v or "", kind="s" if v else "e")
            if prev is not None:
                b.put(METER, f"F{r}", value=prev, kind="n")
                b.put(METER, f"I{r}", value=net, kind="n")
                b.put(METER, f"L{r}", value=_r2(net * Decimal(str(rate))) if rate is not None else "", kind="n" if rate is not None else "e")
                if rate is not None:
                    self.cache[(METER, f"L{r}")] = _r2(net * Decimal(str(rate)))
            b.put(METER, f"H{r}", value=testing, kind="n")
            b.put(METER, f"K{r}", value=rate if rate is not None else "", kind="n" if rate is not None else "e")
            self.added.append((METER, r, f"{name} closing {closing}"))

    # ---------- changing a row that is already there (no row is ever deleted)
    def change(self, sheet, r, *, party=None, amount=None, item=None, qty=None, rate=None, to=None):
        d = read_date(self.v[sheet][f"B{r}"].value)
        self.cache[(sheet, f"B{r}")] = d
        if party is not None:
            self.b.put(sheet, f"C{r}", value=party, kind="s", formula_keep=False)
        if sheet in SALES.values() and qty is not None and amount is None:      # litres given → amount = litres × rate
            rt = Decimal(str(rate if rate is not None else self._val(sheet, f"E{r}")))
            amount = _r2(Decimal(str(qty)) * rt, "1")
        if sheet in SALES.values() and rate is not None:
            self.b.put(sheet, f"E{r}", value=Decimal(str(rate)), kind="n", formula_keep=False)
            self.cache[(sheet, f"E{r}")] = Decimal(str(rate))
            if amount is None:
                amount = self._val(sheet, f"F{r}")
        if sheet in SALES.values():
            if amount is not None:
                self.b.put(sheet, f"F{r}", value=Decimal(str(amount)), kind="n", formula_keep=False)
                self.cache[(sheet, f"F{r}")] = Decimal(str(amount))
                rate_ = Decimal(str(self._val(sheet, f"E{r}")))
                q = _r2(Decimal(str(amount)) / rate_) if rate_ else Decimal(0)
                self.b.put(sheet, f"D{r}", value=q, kind="n"); self.cache[(sheet, f"D{r}")] = q
        elif sheet == LUBE:
            if item is not None:
                self.b.put(sheet, f"D{r}", value=item, kind="s", formula_keep=False)
            q = Decimal(str(qty if qty is not None else self._val(sheet, f"E{r}") or 0))
            rt = Decimal(str(rate if rate is not None else self._val(sheet, f"F{r}") or 0))
            if qty is not None:
                self.b.put(sheet, f"E{r}", value=q.normalize(), kind="n", formula_keep=False)
            if rate is not None:
                self.b.put(sheet, f"F{r}", value=rt.normalize(), kind="n", formula_keep=False)
            g = _r2(q * rt, "1"); self.b.put(sheet, f"G{r}", value=g, kind="n"); self.cache[(sheet, f"G{r}")] = g
        else:
            if to is not None:
                self.b.put(sheet, f"D{r}", value=to, kind="s", formula_keep=False)
            if amount is not None:
                self.b.put(sheet, f"E{r}", value=Decimal(str(amount)), kind="n", formula_keep=False)
                self.cache[(sheet, f"E{r}")] = Decimal(str(amount))
        self.next_row.setdefault(sheet, data_end(self.v[sheet]) + 1)
        self.added.append((sheet, r, "changed"))

    def _last_closing(self, name, r):
        for rr in range(r - 1, 1, -1):
            if self._val(METER, f"D{rr}") == name and self._val(METER, f"G{rr}") is not None:
                return Decimal(str(self._val(METER, f"G{rr}")))
        return None

    def _match_meters(self, closings, names, base):
        """Each reading belongs to the one meter whose last closing is just below it (0–50,000 L) — so a mixed-up
        name in Tally names cannot put a petrol reading on the wrong meter. Names are used only when it is unclear."""
        last = {n: self._last_closing(n, base + i) for i, n in enumerate(names)}
        fuel = lambda n: next((f for f in ("PETROL", "DIESEL", "CNG") if f in (n or "").upper()), "")
        out, used = {}, set()
        for given, value in sorted(closings.items(), key=lambda kv: Decimal(str(kv[1]))):
            v = Decimal(str(value))
            fits = [n for n in names if n not in used and last[n] is not None and Decimal(0) <= v - last[n] <= 50000]
            same = [n for n in fits if fuel(n) == fuel(given)] or fits
            pick = min(same, key=lambda n: v - last[n]) if same else given      # the closest last closing of that fuel
            used.add(pick)
            out[pick] = v
        return out

    def change_meter(self, d, name, closing):
        ws = self.v[METER]
        rows = [r for r in range(90, ws.max_row + 1) if ws.cell(r, 4).value == name and read_date(ws.cell(r, 2).value) is not None]
        mine = [r for r in rows if read_date(ws.cell(r, 2).value) == d]
        if not mine:
            from .util import UserError
            raise UserError(f"{name} has no row on {d:%d/%m/%Y}.")
        r = mine[0]
        mm = self.v["METER MASTER"]
        master = {mm.cell(i, 1).value: [mm.cell(i, c).value for c in range(2, 6)] for i in range(2, mm.max_row + 1) if mm.cell(i, 1).value}
        rate = master.get(name, [None] * 4)[3]
        closing = Decimal(str(closing))
        self.b.put(METER, f"G{r}", value=closing, kind="n", formula_keep=False)
        self.cache[(METER, f"G{r}")] = closing
        for rr in [r] + [x for x in rows if x > r][:1]:          # this row, and the next day's opening of this meter
            prev = next((Decimal(str(self._val(METER, f"G{x}"))) for x in sorted(rows, reverse=True) if x < rr and self._val(METER, f"G{x}") is not None), None)
            if prev is None:
                continue
            g = Decimal(str(self._val(METER, f"G{rr}")))
            t = Decimal(str(self._val(METER, f"H{rr}") or 0))
            net = _r2(g - prev - t)
            if net < 0:
                from .util import UserError
                raise UserError(f"{name}: closing {g} is less than opening {prev}.")
            self.b.put(METER, f"F{rr}", value=prev, kind="n"); self.b.put(METER, f"I{rr}", value=net, kind="n")
            if rate is not None:
                self.b.put(METER, f"L{rr}", value=_r2(net * Decimal(str(rate))), kind="n")
        self.added.append((METER, r, f"{name} closing {closing}"))

    # ---------- day totals (the formula shows the total only on the last row of each date)
    def _totals(self):
        if not any(sh != METER for sh, _r, _t in self.added):
            return
        spec = {**{s: [("G", "D"), ("H", "F")] for s in SALES.values()}, LUBE: [("H", "G")], PAYMENT: [("F", "E")], RECEIPT: [("F", "E")]}
        for sh, pairs in spec.items():
            if not any(s_ == sh for s_, _r, _t in self.added):
                continue
            ws = self.v[sh]
            last = self.next_row.get(sh, data_end(ws) + 1) - 1
            dates = {}
            for r in range(2, last + 1):
                dv = self._val(sh, f"B{r}")
                d = dv if isinstance(dv, date) and not isinstance(dv, datetime) else read_date(dv)
                if d is not None:
                    dates.setdefault(d, []).append(r)
            touched = {self.cache[(sh, f"B{r}")] for (s, r, _) in self.added if s == sh}
            for d in touched:
                rows = dates.get(d, [])
                for tcol, scol in pairs:
                    total = sum((Decimal(str(self._val(sh, f"{scol}{r}") or 0)) for r in rows), Decimal(0))
                    for r in rows:
                        if r == rows[-1]:
                            self.b.put(sh, f"{tcol}{r}", value=total, kind="n")
                        else:
                            self.b.put(sh, f"{tcol}{r}", value="", kind="e")

    def save(self) -> bytes:
        self._totals()
        return self.b.save()
