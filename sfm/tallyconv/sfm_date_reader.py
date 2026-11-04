"""Range-independent date interpretation for original SFM Excel workbooks."""
from datetime import date, datetime
import re
from openpyxl import load_workbook

POLICIES = ('SFM displayed dates (day first)', 'Stored Excel dates (already correct)')

def cell_date(cell, policy=POLICIES[0]):
    import sfm_converter_core as c
    value = cell.value
    if not isinstance(value, (date, datetime)):
        return c.parse_date(value)
    stored = value.date() if isinstance(value, datetime) else value
    if policy == POLICIES[1]:
        return stored
    if policy != POLICIES[0]:
        raise ValueError('Unknown Excel date policy')
    fmt = re.sub(r'\[[^\]]*\]|"[^"]*"|\\.', '', cell.number_format.lower()).split(';')[0]
    tokens = re.findall(r'd+|m+|y+', fmt)
    if len(tokens) != 3 or any(len(t)>2 for t in tokens if t[0] in 'dm'):
        raise ValueError('Unsupported date display. Use d/m/yyyy or select Stored Excel dates after checking the date.')
    order = ''.join(t[0] for t in tokens)
    if order in ('dmy', 'ymd'):
        return stored
    if order == 'mdy':
        # Interpret the first displayed number as day, as requested for SFM.
        # Never select a meaning according to the requested export range.
        return date(stored.year, stored.day, stored.month)
    raise ValueError('Unsupported date display order: '+cell.number_format)

def load_sales_workbook(path, policy=POLICIES[0]):
    import sfm_converter_core as c
    wb = load_workbook(path, data_only=True)
    wb.sfm_date_audit = []
    for name, ws in c.build_available_sheets(wb).items():
        header, hm = c.find_header_map(ws, ['Date'])
        col = hm[c.norm_header('Date')]
        for r in range(header+1, ws.max_row+1):
            cell = ws.cell(r,col)
            raw = cell.value
            if raw in (None,''):
                # A prefilled serial number or formula-only template is not a transaction.
                if any(ws.cell(r,k).value not in (None,'',0) for k in (3,4)):
                    cell.value = f'MISSING DATE at {ws.title}!{cell.coordinate}'
                continue
            try:
                resolved = cell_date(cell,policy)
                wb.sfm_date_audit.append((name,r,str(raw),cell.number_format,c.human_date(resolved)))
                cell.value = c.human_date(resolved)
            except (ValueError,TypeError) as exc:
                cell.value = f'INVALID DATE at {ws.title}!{cell.coordinate}: {raw!r}: {exc}'
    return wb

def write_date_audit(wb, path, ranges):
    import csv
    import sfm_converter_core as c
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f)
        writer.writerow(['Sheet','Excel row','Stored value','Excel format','Date d/m/yyyy','In selected range'])
        for row in wb.sfm_date_audit:
            if row[0] in ranges:
                start,end=ranges[row[0]]
                writer.writerow([*row,start<=c.parse_date(row[4])<=end])

def reconcile_sheet(ws, start, end, vouchers, errors):
    """Independently require exported row counts and daily gross amounts to match."""
    import sfm_converter_core as c
    from collections import defaultdict
    from decimal import Decimal
    import xml.etree.ElementTree as ET
    header, hm = c.find_header_map(ws, ['Date','Amount'])
    expected=defaultdict(lambda:[0,Decimal(0)])
    for r in range(header+1,ws.max_row+1):
        raw=c.get_cell(ws,r,hm,'Date')
        if raw in (None,''):continue
        try:d=c.parse_date(raw)
        except ValueError:continue  # Processor reports the specific invalid date.
        if not start<=d<=end:continue
        amount=c.get_cell(ws,r,hm,'Amount')
        if amount in (None,''):
            errors.append(f'{ws.title} row {r}: Amount is blank or formula result is not saved. Recalculate and save Excel.')
            continue
        expected[c.tally_date(d)][0]+=1
        expected[c.tally_date(d)][1]+=c.q2(c.D(amount))
    actual=defaultdict(lambda:[0,Decimal(0)])
    for xml in vouchers:
        n=ET.fromstring(xml.replace('&#4;','')).find('VOUCHER')
        d=n.findtext('DATE')
        if ws.title=='METER READING':
            lines=n.findall('INVENTORYENTRIESOUT.LIST')
            count=len(lines)
            amount=sum((c.D(x.find('.//{TallyUDF}MRVALUE').text) for x in lines),Decimal(0))
        else:
            count=len(n.findall('ALLINVENTORYENTRIES.LIST')) if ws.title=='LUBE SALES' else 1
            tag='ALLLEDGERENTRIES.LIST' if ws.title in ('PAYMENT','RECEIPT') else 'LEDGERENTRIES.LIST'
            amount=-c.D(n.findtext(tag+'/AMOUNT'))
        actual[d][0]+=count
        actual[d][1]+=amount
    for d in sorted(set(expected)|set(actual)):
        if expected[d]!=actual[d]:
            errors.append(f'{ws.title} {c.human_date(c.parse_date(d))}: reconciliation failed: source rows/amount {expected[d]}, XML {actual[d]}. Export blocked.')
    return [f'{ws.title} {c.human_date(c.parse_date(d))}: {x[0]} rows; {x[1]:.2f} amount; '+('MATCH' if actual[d]==x else 'MISMATCH') for d,x in sorted(expected.items())]
