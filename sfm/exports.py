"""PDF / Excel builders. Pure functions: data in, BytesIO out."""
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .services import PARTY_KIND_LABEL
from .util import inr, now_ist

INK = colors.HexColor("#1B2A41")
RULE = colors.HexColor("#C9CED6")
TINT = colors.HexColor("#EEF1F5")


def _particulars(r):
    k = r["kind"]
    if k == "fuel":
        return f"{r['fuel_code']} {r['qty']} {r.get('unit') or 'L'} @ {r['rate']}"
    if k == "lube":
        return f"{r.get('lube_name') or r.get('remarks') or 'Lube'} × {r['qty']}"
    if k == "payment":
        s = f"Received – {r['pay_mode']}"
        if r.get("reference"):
            s += f" ({r['reference']})"
        if r.get("receipt_no"):
            s += f" #{r['receipt_no']}"
        return s
    s = PARTY_KIND_LABEL.get(k, k)
    if r.get("remarks") and k not in ("fuel",):
        s += f" – {r['remarks']}"
    return s


def party_bill_pdf(p, stmt, start, end, st):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm,
                            title=f"Statement – {p['name']}", author=st.get("station_name", ""))
    ss = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=17, textColor=INK,
                       alignment=0, spaceAfter=1 * mm)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=8.5, textColor=colors.HexColor("#4A5568"),
                           leading=11)
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=10, leading=13)
    cell = ParagraphStyle("c", parent=ss["Normal"], fontSize=8.5, leading=10.5)
    story = [Paragraph(st.get("station_name") or "Statement", h)]
    sub = " | ".join(x for x in [st.get("station_address"), st.get("station_phone") and f"Ph {st.get('station_phone')}",
                                 st.get("station_gstin") and f"GSTIN {st.get('station_gstin')}"] if x)
    if sub:
        story.append(Paragraph(sub, small))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(f"<b>Account statement:</b> {p['name']}"
                           + (f" &nbsp; Mobile {p['mobile']}" if p.get("mobile") else "")
                           + (f" &nbsp; GSTIN {p['gstin']}" if p.get("gstin") else ""), body))
    story.append(Paragraph(f"Period {start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}", small))
    story.append(Spacer(1, 3 * mm))

    data = [["Date", "Particulars", "Vehicle", "Challan / Slip", "Debit (Rs)",
             "Credit (Rs)", "Balance (Rs)"]]
    data.append([start.strftime("%d-%m-%y"), "Opening balance", "", "", "", "", inr(stmt["opening"], symbol=False)])
    for r in stmt["rows"]:
        if r["voided"]:
            continue
        cs = " / ".join(x for x in [r.get("challan_no"), r.get("slip_no")] if x)
        data.append([r["business_date"].strftime("%d-%m-%y"), Paragraph(_particulars(r), cell),
                     r.get("vehicle_no") or "", cs,
                     inr(r["debit"], symbol=False) if r["debit"] else "",
                     inr(r["credit"], symbol=False) if r["credit"] else "",
                     inr(r["running"], symbol=False)])
    tot = stmt["totals"]
    data.append(["", "Total for period", "", "", inr(tot["debit"], symbol=False),
                 inr(tot["credit"], symbol=False), ""])
    data.append(["", "Closing balance (amount due)" if stmt["closing"] >= 0 else "Closing balance (advance)",
                 "", "", "", "", inr(stmt["closing"], symbol=False)])
    widths = [22 * mm, 92 * mm, 30 * mm, 38 * mm, 30 * mm, 30 * mm, 31 * mm]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -3), 0.3, RULE),
        ("BACKGROUND", (0, -2), (-1, -1), TINT),
        ("FONT", (0, -2), (-1, -1), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, 1), "Helvetica-Oblique", 8.5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(f"Fuel supplied in period: {tot['fuel_qty']} L. "
                           "Please check and report any difference within 7 days.", small))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(12 * mm, 7 * mm, f"Generated {now_ist().strftime('%d %b %Y %I:%M %p')}")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 7 * mm, f"Page {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    return buf


HEAD_FILL = PatternFill("solid", start_color="1B2A41")
HEAD_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="C9CED6")


def _excel_value(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    return v


def write_sheet(ws, headers, rows, money_cols=(), widths=None):
    ws.append(headers)
    for c in ws[ws.max_row]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center")
    for r in rows:
        ws.append([_excel_value(v) for v in r])
    for col in money_cols:
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            for c in row:
                c.number_format = "#,##,##0.00"
    for i, h in enumerate(headers, 1):
        w = (widths or {}).get(i) or max(10, min(45, max((len(str(r[i - 1])) for r in rows), default=0) + 2,
                                               len(str(h)) + 2))
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def party_bill_xlsx(p, stmt, start, end, st):
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    ws.append([st.get("station_name", "")])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Statement: {p['name']}  |  {start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}"])
    ws.append([])
    rows = [[start, "Opening balance", "", "", "", None, None, stmt["opening"]]]
    for r in stmt["rows"]:
        if r["voided"]:
            continue
        rows.append([r["business_date"], _particulars(r), r.get("vehicle_no") or "", r.get("challan_no") or "",
                     r.get("slip_no") or "", r["debit"] or None, r["credit"] or None, r["running"]])
    rows.append([None, "Total", "", "", "", stmt["totals"]["debit"], stmt["totals"]["credit"], None])
    rows.append([None, "Closing balance", "", "", "", None, None, stmt["closing"]])
    start_row = ws.max_row + 1
    write_sheet(ws, ["Date", "Particulars", "Vehicle", "Challan", "Slip", "Debit", "Credit", "Balance"],
                rows, money_cols=(6, 7, 8), widths={1: 12, 2: 42})
    for row in ws.iter_rows(min_row=start_row + 1, min_col=1, max_col=1):
        for c in row:
            if isinstance(c.value, (date, datetime)):
                c.number_format = "dd-mm-yyyy"
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def workbook_bytes(sheets):
    """sheets: list of (title, headers, rows, money_cols)"""
    wb = Workbook()
    wb.remove(wb.active)
    for title, headers, rows, money_cols in sheets:
        ws = wb.create_sheet(title[:31])
        write_sheet(ws, headers, rows, money_cols)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, (date, datetime)) and not isinstance(c.value, datetime):
                    c.number_format = "dd-mm-yyyy"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
