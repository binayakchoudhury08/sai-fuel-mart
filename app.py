from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, flash
from datetime import timedelta, datetime
import sqlite3
import os
import psycopg2
import psycopg2.extras
import json
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, PieChart, LineChart, Reference
from openpyxl.utils import get_column_letter
import tempfile
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from supabase import create_client
import uuid


app = Flask(__name__)
app.secret_key = "sai-fuel-mart-secret-key"
app.permanent_session_lifetime = timedelta(days=30)

USERS = {

    "admin": {
        "password": "1234",
        "role": "admin"
    },

    "manager": {
        "password": "1234",
        "role": "manager"
    }

}

DB_PATH = os.path.join(os.getcwd(), "sai_fuel_mart.db")

def is_admin():
    return session.get("role") == "admin"

def is_manager():
    return session.get("role") == "manager"

def get_conn():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def style_excel_sheet(ws):
    header_fill = PatternFill(start_color="07120C", end_color="07120C", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=12)
    center = Alignment(horizontal="center", vertical="center")

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center

    for column_cells in ws.columns:
        max_length = 0
        col_no = column_cells[0].column

        for cell in column_cells:
            if cell.value is not None:
                max_length = max(max_length, len(str(cell.value)))

        ws.column_dimensions[get_column_letter(col_no)].width = max_length + 4

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            ms_rate REAL,
            hsd_rate REAL,
            cng_rate REAL
        )
    """)

    cur.execute("""
        INSERT OR IGNORE INTO settings (id, ms_rate, hsd_rate, cng_rate)
        VALUES (1, 102.46, 93.72, 78)
    """)

    try:
        cur.execute("ALTER TABLE settings ADD COLUMN cng_rate REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_closing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            ms_litres REAL,
            hsd_litres REAL,
            cng_sale REAL,
            total_fuel_sale REAL,
            lube_sale REAL,
            digital_collection REAL,
            credit_given REAL,
            transport_received REAL,
            net_credit_due REAL,
            total_expense REAL,
            cash_in_hand REAL
        )
    """)

    

    for col, typ in [
        ("phonepe", "REAL DEFAULT 0"),
        ("card_swipe", "REAL DEFAULT 0"),
        ("hp_pay", "REAL DEFAULT 0"),
        ("hpcl_otp", "REAL DEFAULT 0"),
        ("upi_other", "REAL DEFAULT 0")
    ]:
        try:
            cur.execute(f"ALTER TABLE daily_closing ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass

    try:
        cur.execute("ALTER TABLE daily_closing ADD COLUMN cng_sale REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

     

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tank_level (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            fuel_type TEXT,
            opening_stock REAL,
            received_stock REAL,
            own_tanker_stock REAL,
            sale_stock REAL,
            gain_qty REAL,
            shortage_qty REAL,
            current_stock REAL,
            gain_amount REAL,
            shortage_amount REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
CREATE TABLE IF NOT EXISTS salary_payments(

    id INTEGER PRIMARY KEY AUTOINCREMENT,

    payment_date TEXT,

    emp_id TEXT,

    employee_name TEXT,

    payment_mode TEXT,

    bank_account TEXT,

    amount REAL,

    month_name TEXT,

    remarks TEXT

)
""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS staff_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_name TEXT,
            role TEXT,
            status TEXT
        )
    """)

    for col, typ in [
    ("emp_id", "TEXT"),
    ("department", "TEXT"),
    ("joined_date", "TEXT"),
    ("bank_account", "TEXT"),
    ("shift", "TEXT")
    ]:
        try:
            cur.execute(f"ALTER TABLE staff_master ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass

        
      
    cur.execute("""
    CREATE TABLE IF NOT EXISTS credit_transporters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        party_name TEXT,
        opening_balance REAL DEFAULT 0,
        credit_given REAL DEFAULT 0,
        payment_received REAL DEFAULT 0,
        balance_due REAL DEFAULT 0,
        status TEXT
    )
""")

    for col, typ in [

    ("mobile", "TEXT"),
    ("vehicle_no", "TEXT"),

    ("fuel_credit", "REAL DEFAULT 0"),
    ("lube_credit", "REAL DEFAULT 0"),

    ("amount_received", "REAL DEFAULT 0")

]:
        try:
            cur.execute(
            f"ALTER TABLE credit_transporters ADD COLUMN {col} {typ}"
        )
        except sqlite3.OperationalError:
         pass

     
    
    try:
       cur.execute(
        "ALTER TABLE transport_entries ADD COLUMN transporter_id INTEGER"
    )
    except sqlite3.OperationalError:
     pass
    cur.execute("""

    CREATE TABLE IF NOT EXISTS transporter_ledger (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        date TEXT,

        transporter_id INTEGER,
        transporter_name TEXT,

        entry_type TEXT,

        fuel_credit REAL DEFAULT 0,
        lube_credit REAL DEFAULT 0,

        received_amount REAL DEFAULT 0,

        balance_after REAL DEFAULT 0,

        remarks TEXT,

        created_at TEXT DEFAULT CURRENT_TIMESTAMP

    )

""") 
    cur.execute("""
    CREATE TABLE IF NOT EXISTS lube_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        product_id INTEGER,
        product_name TEXT,
        transaction_type TEXT,
        qty REAL,
        rate REAL,
        amount REAL,
        remarks TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transport_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            transporter_id INTEGER,
            payment_amount REAL
        )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS credit_lube_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        transporter_id INTEGER,
        transporter_name TEXT,
        product_id INTEGER,
        product_name TEXT,
        qty REAL,
        rate REAL,
        amount REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transport_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date TEXT,
            sl_no INTEGER,
            transporter_name TEXT,
            challan_no TEXT,
            vehicle_no TEXT,
            slip_no TEXT,
            hsd_qty REAL,
            rate REAL,
            hsd_amount REAL,
            cash_taken REAL,
            total_amount REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        cur.execute("ALTER TABLE transport_entries ADD COLUMN transporter_name TEXT")
    except sqlite3.OperationalError:
        pass

    cur.execute("""
        CREATE TABLE IF NOT EXISTS nozzle_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nozzle_name TEXT,
            machine_no TEXT,
            fuel_type TEXT,
            status TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS nozzle_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date TEXT,
            nozzle_id INTEGER,
            opening_reading REAL,
            closing_reading REAL,
            testing_qty REAL,
            total_sale REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    default_nozzles = [

    ("MS1", "351", "MS", "Active"),
    ("MS2", "356", "MS", "Active"),
    ("MS3", "725", "MS", "Active"),

    ("HSD1", "351", "HSD", "Active"),
    ("HSD2", "356", "HSD", "Active"),
    ("HSD3", "725", "HSD", "Active"),

    ("CNG1", "CNG-1", "CNG", "Active"),
    ("CNG2", "CNG-2", "CNG", "Active")

]

    for nozzle in default_nozzles:
        cur.execute("SELECT id FROM nozzle_master WHERE nozzle_name = ?", (nozzle[0],))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO nozzle_master (
                    nozzle_name, machine_no, fuel_type, status
                )
                VALUES (?, ?, ?, ?)
            """, nozzle)

    conn.commit()
    conn.close()


@app.route("/")
def home():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():

    error = ""

    if request.method == "POST":

        username = request.form.get("username")
        password = request.form.get("password")

        # CHECK USER

        if username in USERS and USERS[username]["password"] == password:

            session["logged_in"] = True

            session["username"] = username

            session["role"] = USERS[username]["role"]

            # REMEMBER LOGIN

            if request.form.get("remember"):
                session.permanent = True

            return redirect(url_for("dashboard"))

        else:

            error = "Invalid username or password"

    return render_template(
        "login.html",
        error=error
    )


@app.route("/dashboard")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_closing ORDER BY id DESC LIMIT 1")
    latest = cur.fetchone()

    cur.execute("""

    SELECT

        ROUND(
            SUM(total_fuel_sale),
            2
        ) AS monthly_sale,

        ROUND(
            SUM(cng_sale * (
                SELECT cng_rate
                FROM settings
                WHERE id=1
            )),
            2
        ) AS monthly_cng,

        ROUND(
            SUM(total_expense),
            2
        ) AS monthly_expense,

        ROUND(
            SUM(net_credit_due),
            2
        ) AS monthly_credit

    FROM daily_closing

    WHERE TO_CHAR(date, 'YYYY-MM') = TO_CHAR(CURRENT_DATE, 'YYYY-MM')

""")
    monthly = cur.fetchone()

    cur.execute("""
    SELECT
        dc.date,
        COALESCE(dc.total_fuel_sale, 0) AS total_fuel_sale,
        COALESCE(dc.cng_sale, 0) * COALESCE(s.cng_rate, 0) AS cng_revenue
    FROM daily_closing dc
    CROSS JOIN settings s
    WHERE s.id = 1
    ORDER BY dc.date ASC
    LIMIT 15
""") 
    chart_rows = cur.fetchall()

    chart_labels = []
    chart_sales = []
    cng_data = []

    for r in chart_rows:
        chart_labels.append(r["date"])
        chart_sales.append(round(float(r["total_fuel_sale"] or 0)))

        if "cng_revenue" in r.keys():
            cng_data.append(round(float(r["cng_revenue"] or 0)))
        else:
            cng_data.append(round(float(r["cng_sale"] or 0)))

    cur.execute("""
        SELECT party_name, credit_given, payment_received, balance_due
        FROM credit_transporters
        ORDER BY balance_due DESC
    """)
    transporter_summary = cur.fetchall()

    cur.execute("""
        SELECT product_name, closing_stock
        FROM lube_stock
        WHERE closing_stock <= 5
        ORDER BY closing_stock ASC
    """)
    low_lube = cur.fetchall()

    cur.execute("SELECT * FROM tank_level WHERE fuel_type='MS' ORDER BY id DESC LIMIT 1")
    ms_tank = cur.fetchone()

    cur.execute("SELECT * FROM tank_level WHERE fuel_type='HSD' ORDER BY id DESC LIMIT 1")
    hsd_tank = cur.fetchone()

    today = datetime.now()
    show_backup_popup = today.day <= 3

    conn.close()

    return render_template(
        "dashboard.html",
        chart_labels=chart_labels,
        chart_sales=chart_sales,
        cng_data=cng_data,
        latest=latest,
        monthly=monthly,
        chart_rows=chart_rows,
        transporter_summary=transporter_summary,
        low_lube=low_lube,
        ms_tank=ms_tank,
        hsd_tank=hsd_tank,
        show_backup_popup=show_backup_popup
    )

@app.route("/receive-transport-payment/<int:id>", methods=["POST"])
def receive_transport_payment(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    amount = float(request.form.get("amount", 0))

    if amount <= 0:
        return redirect(url_for("credit_transport"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""

        UPDATE credit_transporters

        SET

            amount_received =
            COALESCE(amount_received,0) + %s,

            balance_due =
            balance_due - %s

        WHERE id=%s

    """, (

        amount,
        amount,
        id

    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))

@app.route("/backup-database")
def backup_database():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_admin():
        return redirect(url_for("dashboard"))

    # your backup code below
    backup_name = f"sai_fuel_mart_backup_{datetime.now().strftime('%d_%m_%Y_%H_%M')}.db"

    return send_file(
        DB_PATH,
        as_attachment=True,
        download_name=backup_name
    )

# app.py
# DAILY CLOSING ROUTE

@app.route("/export-party-transport-excel")
def export_party_transport_excel():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    transporter_id = request.args.get("transporter_id")
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT party_name
        FROM credit_transporters
        WHERE id=%s
    """, (transporter_id,))
    party = cur.fetchone()

    party_name = party["party_name"] if party else "Transporter"

    cur.execute("""
        SELECT *
        FROM transport_entries
        WHERE transporter_id=%s
          AND entry_date >= %s
          AND entry_date <= %s
        ORDER BY entry_date ASC, sl_no ASC
    """, (transporter_id, from_date, to_date))

    rows = cur.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Transport Entry Report"

    ws.append([
        "Date", "SL No", "Party Name", "Challan No", "Vehicle No",
        "Slip No", "HSD Qty", "Rate", "HSD Amount",
        "Diesel", "Final Amount"
    ])

    total_hsd = 0
    total_rate = 0
    total_hsd_amount = 0
    total_cash = 0
    total_final = 0

    for r in rows:
        total_hsd += float(r["hsd_qty"] or 0)
        total_rate += float(r["rate"] or 0)
        total_hsd_amount += float(r["hsd_amount"] or 0)
        total_cash += float(r["cash_taken"] or 0)
        total_final += float(r["total_amount"] or 0)

        ws.append([
            r["entry_date"],
            r["sl_no"],
            r["transporter_name"],
            r["challan_no"],
            r["vehicle_no"],
            r["slip_no"],
            r["hsd_qty"],
            r["rate"],
            r["hsd_amount"],
            r["cash_taken"],
            r["total_amount"]
        ])

    ws.append([])

    total_row = [
       "", "", "", "", "", "TOTAL",
       round(total_hsd, 2),
       round(total_rate, 2),
       round(total_hsd_amount),
       round(total_cash),
       round(total_final)
]

    ws.append(total_row)

    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="07120C")

    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9EAD3")

    style_excel_sheet(ws)

    file = BytesIO()
    wb.save(file)
    file.seek(0)

    safe_party = party_name.replace(" ", "_").replace("/", "_")
    filename = f"{safe_party}_{from_date}_to_{to_date}_Transport_Report.xlsx"

    return send_file(
        file,
        as_attachment=True,
        download_name=filename
    )


@app.route("/export-party-transport-pdf")
def export_party_transport_pdf():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    transporter_id = request.args.get("transporter_id")
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT party_name
        FROM credit_transporters
        WHERE id=%s
    """, (transporter_id,))
    party = cur.fetchone()

    party_name = party["party_name"] if party else "Transporter"

    cur.execute("""
        SELECT *
        FROM transport_entries
        WHERE transporter_id=%s
          AND entry_date >= %s
          AND entry_date <= %s
        ORDER BY entry_date ASC, sl_no ASC
    """, (transporter_id, from_date, to_date))

    rows = cur.fetchall()
    conn.close()

    file = BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(820, 15, f"Page {doc.page}")
        canvas.drawString(20, 15, "Sai Fuel Mart - Credit Transport Report")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        file,
        pagesize=landscape(A4),
        rightMargin=12,
        leftMargin=12,
        topMargin=14,
        bottomMargin=25
    )

    styles = getSampleStyleSheet()
    elements = []

    title_style = styles["Title"]
    title_style.fontSize = 16
    title_style.leading = 18

    normal_style = styles["Normal"]
    normal_style.fontSize = 9

    elements.append(Paragraph("SAI FUEL MART", title_style))
    elements.append(Paragraph("Credit Transport Entry Report", normal_style))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"<b>Party:</b> {party_name}", normal_style))
    elements.append(Paragraph(f"<b>Date Range:</b> {from_date} to {to_date}", normal_style))
    elements.append(Paragraph(f"<b>Generated On:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}", normal_style))
    elements.append(Spacer(1, 10))

    data = [[
        "Date", "SL", "Party", "Challan", "Vehicle",
        "Slip", "HSD Qty", "Rate", "HSD Amt",
        "Diesel", "Final Amt"
    ]]

    total_hsd = 0
    total_rate = 0
    total_hsd_amount = 0
    total_cash = 0
    total_final = 0

    for r in rows:
        hsd_qty = float(r["hsd_qty"] or 0)
        rate = float(r["rate"] or 0)
        hsd_amount = float(r["hsd_amount"] or 0)
        cash = float(r["cash_taken"] or 0)
        final = float(r["total_amount"] or 0)

        total_hsd += hsd_qty
        total_rate += rate
        total_hsd_amount += hsd_amount
        total_cash += cash
        total_final += final

        data.append([
            str(r["entry_date"]),
            r["sl_no"],
            r["transporter_name"],
            r["challan_no"],
            r["vehicle_no"],
            r["slip_no"],
            f"{hsd_qty:.2f}",
            f"{rate:.2f}",
            f"{hsd_amount:.2f}",
            f"{cash:.2f}",
            f"{final:.2f}"
        ])

    data.append(["", "", "", "", "", "", "", "", "", "", ""])

    data.append([
        "", "", "", "", "", "TOTAL",
        f"{total_hsd:.2f}",
        f"{total_rate:.2f}",
        f"{total_hsd_amount:.2f}",
        f"{total_cash:.2f}",
        f"{total_final:.2f}"
    ])

    table = Table(
        data,
        colWidths=[58, 28, 110, 60, 65, 50, 55, 45, 65, 60, 70],
        repeatRows=1
    )

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#07120C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),

        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),

        ("FONTSIZE", (0, 0), (-1, -1), 6.5),
        ("LEADING", (0, 0), (-1, -1), 7.5),

        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),

        ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ("ALIGN", (6, 1), (-1, -1), "RIGHT"),

        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DCFCE7")),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#07120C")),

        ("ROWBACKGROUNDS", (0, 1), (-1, -3), [
            colors.white,
            colors.HexColor("#F8FAFC")
        ]),
    ]))

    elements.append(table)

    doc.build(elements, onFirstPage=footer, onLaterPages=footer)

    file.seek(0)

    safe_party = party_name.replace(" ", "_").replace("/", "_")
    filename = f"{safe_party}_{from_date}_to_{to_date}_Credit_Transport_Report.pdf"

    return send_file(
        file,
        as_attachment=True,
        download_name=filename
    )

@app.route("/save-daily-closing", methods=["POST"])
def save_daily_closing():

    if not session.get("logged_in"):
        return jsonify({"status": "error", "message": "Not logged in"})

    data = request.get_json()

    if not data or not data.get("date"):
        return jsonify({"status": "error", "message": "Date missing"})

    conn = None

    try:
        conn = get_pg_conn()
        cur = conn.cursor()

        cur.execute("SELECT id FROM daily_closing WHERE date=%s", (data["date"],))
        existing = cur.fetchone()

        if existing:
            cur.execute("DELETE FROM daily_closing WHERE id=%s", (existing["id"],))
            cur.execute("DELETE FROM nozzle_entries WHERE entry_date=%s", (data["date"],))

        cur.execute("""
            INSERT INTO daily_closing (
                date, ms_litres, hsd_litres, cng_sale,
                total_fuel_sale, lube_sale, digital_collection,
                phonepe, card_swipe, hp_pay, hpcl_otp, upi_other,
                credit_given, transport_received, net_credit_due,
                total_expense, cash_in_hand
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data["date"],
            float(data.get("ms_litres", 0)),
            float(data.get("hsd_litres", 0)),
            float(data.get("cng_sale", 0)),
            float(data.get("total_fuel_sale", 0)),
            float(data.get("lube_sale", 0)),
            float(data.get("digital_collection", 0)),
            float(data.get("phonepe", 0)),
            float(data.get("card_swipe", 0)),
            float(data.get("hp_pay", 0)),
            float(data.get("hpcl_otp", 0)),
            float(data.get("upi_other", 0)),
            float(data.get("credit_given", 0)),
            float(data.get("transport_received", 0)),
            float(data.get("net_credit_due", 0)),
            float(data.get("total_expense", 0)),
            float(data.get("cash_in_hand", 0))
        ))

        # SAVE NOZZLES
        for item in data.get("nozzle_entries", []):

            nozzle_id = item.get("nozzle_id")

            if not nozzle_id:
                continue

            opening = float(item.get("opening", 0))
            closing = float(item.get("closing", 0))
            testing = float(item.get("testing", 0))

            sale = max(round(closing - opening - testing, 2), 0)

            cur.execute("""
                INSERT INTO nozzle_entries (
                    entry_date, nozzle_id, opening_reading,
                    closing_reading, testing_qty, total_sale, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                data["date"],
                nozzle_id,
                opening,
                closing,
                testing,
                sale,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))

        # SAVE LUBE CASH / CREDIT
        for item in data.get("lube_sales", []):

            qty = float(item.get("qty", 0))
            rate = float(item.get("rate", 0))
            amount = float(item.get("amount", 0))

            product_id = item.get("product_id")
            product_name = item.get("product_name", "")

            mode = item.get("mode", "Cash")
            transporter_id = item.get("transporter_id", "")
            transporter_name = item.get("transporter_name", "")

            if qty <= 0 or not product_id:
                continue

            cur.execute("""
                UPDATE lube_stock
                SET sale_qty = COALESCE(sale_qty,0) + %s,
                    closing_stock = COALESCE(closing_stock,0) - %s
                WHERE id=%s
            """, (qty, qty, product_id))

            cur.execute("""
                INSERT INTO lube_transactions (
                    date, product_id, product_name, transaction_type,
                    qty, rate, amount, remarks
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data["date"],
                product_id,
                product_name,
                "Sale",
                qty,
                rate,
                amount,
                f"{mode} Lube Sale"
            ))

            if mode == "Credit":

                if not transporter_id:
                    return jsonify({
                        "status": "error",
                        "message": f"Please select transporter for credit lube: {product_name}"
                    })

                cur.execute("""
                    UPDATE credit_transporters
                    SET lube_credit = COALESCE(lube_credit,0) + %s,
                        credit_given = COALESCE(credit_given,0) + %s,
                        balance_due = COALESCE(balance_due,0) + %s
                    WHERE id=%s
                """, (
                    amount,
                    amount,
                    amount,
                    transporter_id
                ))

                cur.execute("""
                    SELECT party_name, balance_due
                    FROM credit_transporters
                    WHERE id=%s
                """, (transporter_id,))

                tr = cur.fetchone()

                cur.execute("""
                    INSERT INTO transporter_ledger (
                        date, transporter_id, transporter_name,
                        entry_type, lube_credit, balance_after, remarks
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    data["date"],
                    transporter_id,
                    tr["party_name"] if tr else transporter_name,
                    "Lube Credit",
                    amount,
                    tr["balance_due"] if tr else amount,
                    product_name
                ))

        # SAVE FUEL CREDIT FROM DAILY CLOSING
        for item in data.get("credit_transport_sales", []):

            amount = float(item.get("amount", 0))
            transporter_id = item.get("transporter_id", "")

            if amount <= 0 or not transporter_id:
                continue

            cur.execute("""
                UPDATE credit_transporters
                SET fuel_credit = COALESCE(fuel_credit,0) + %s,
                    credit_given = COALESCE(credit_given,0) + %s,
                    balance_due = COALESCE(balance_due,0) + %s
                WHERE id=%s
            """, (
                amount,
                amount,
                amount,
                transporter_id
            ))

            cur.execute("""
                SELECT party_name, balance_due
                FROM credit_transporters
                WHERE id=%s
            """, (transporter_id,))

            tr = cur.fetchone()

            cur.execute("""
                INSERT INTO transporter_ledger (
                    date, transporter_id, transporter_name,
                    entry_type, fuel_credit, balance_after, remarks
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                data["date"],
                transporter_id,
                tr["party_name"] if tr else "",
                "Fuel Credit",
                amount,
                tr["balance_due"] if tr else amount,
                "Fuel Credit from Daily Closing"
            ))

        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Daily closing saved successfully"
        })

    except Exception as e:

        if conn:
            conn.rollback()

        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        })

    finally:

        if conn:
            conn.close()

@app.route("/save-lube-product", methods=["POST"])
def save_lube_product():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    product_name = request.form.get("product_name")
    selling_rate = float(request.form.get("selling_rate") or 0)
    opening_stock = float(request.form.get("opening_stock") or 0)
    purchase_qty = float(request.form.get("purchase_qty") or 0)

    closing_stock = opening_stock + purchase_qty

    cur.execute("""

        INSERT INTO lube_stock (

            product_name,
            selling_rate,
            opening_stock,
            purchase_qty,
            sale_qty,
            closing_stock

        )

        VALUES (%s, %s, %s, %s, %s, %s)

    """, (

        product_name,
        selling_rate,
        opening_stock,
        purchase_qty,
        0,
        closing_stock

    ))

    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))

@app.route("/daily-closing")
def daily_closing():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    selected_date = request.args.get(
        "date",
        datetime.now().strftime("%Y-%m-%d")
    )

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT *
    FROM lube_stock
    ORDER BY product_name ASC
""")

    lube_items = cur.fetchall()

    cur.execute("""
    SELECT *
    FROM credit_transporters
    ORDER BY party_name ASC
""")
    transporters = cur.fetchall()

    # SETTINGS

    cur.execute("""
        SELECT *
        FROM settings
        WHERE id=1
    """)

    settings = cur.fetchone()

    # FETCH ALL NOZZLES

    cur.execute("""

    SELECT

        nm.id,
        nm.nozzle_name,
        nm.machine_no,
        nm.fuel_type,

        COALESCE(

            ne.opening_reading,

            (

                SELECT prev.closing_reading

                FROM nozzle_entries prev

                WHERE prev.nozzle_id = nm.id
                AND prev.entry_date < %s

                ORDER BY prev.entry_date DESC,
                         prev.id DESC

                LIMIT 1

            ),

            0

        ) AS opening_reading,

        ne.closing_reading,
        ne.testing_qty,
        ne.total_sale

    FROM nozzle_master nm

    LEFT JOIN nozzle_entries ne

    ON nm.id = ne.nozzle_id
    AND ne.entry_date = %s

    ORDER BY
    nm.fuel_type,
    nm.nozzle_name

""", (selected_date, selected_date))

        

    all_nozzles = cur.fetchall()

    ms_nozzles = []
    hsd_nozzles = []
    cng_nozzles = []

    for n in all_nozzles:

        if n["fuel_type"] == "MS":
            ms_nozzles.append(n)

        elif n["fuel_type"] == "HSD":
            hsd_nozzles.append(n)

        elif n["fuel_type"] == "CNG":
            cng_nozzles.append(n)

    conn.close()

    return render_template(

        "daily_closing.html",

        settings=settings,
        transporters=transporters,
        ms_nozzles=ms_nozzles,
        hsd_nozzles=hsd_nozzles,
        cng_nozzles=cng_nozzles,
        lube_items=lube_items,
        today=selected_date
    )
@app.route("/print-daily-report/<date>")
def print_daily_report(date):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM daily_closing
        WHERE TO_CHAR(date, 'YYYY-MM-DD') = %s
        ORDER BY id DESC
        LIMIT 1
    """, (date,))
    closing = cur.fetchone()

    cur.execute("""
        SELECT
            nozzle_entries.*,
            nozzle_master.nozzle_name,
            nozzle_master.fuel_type,
            nozzle_master.machine_no
        FROM nozzle_entries
        LEFT JOIN nozzle_master
        ON nozzle_entries.nozzle_id = nozzle_master.id
        WHERE TO_CHAR(nozzle_entries.entry_date, 'YYYY-MM-DD') = %s
        ORDER BY nozzle_master.fuel_type, nozzle_master.nozzle_name
    """, (date,))
    nozzle_rows = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM tank_level
        WHERE TO_CHAR(date, 'YYYY-MM-DD') = %s
        ORDER BY fuel_type ASC, id DESC
    """, (date,))
    tank_rows = cur.fetchall()

    conn.close()

    return render_template(
        "daily_report.html",
        closing=closing,
        nozzle_rows=nozzle_rows,
        tank_rows=tank_rows,
        report_date=date
    )


@app.route("/edit-transport-entry/<int:id>")
def edit_transport_entry(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM transport_entries WHERE id=%s",
        (id,)
    )

    entry = cur.fetchone()

    cur.execute("""
        SELECT *
        FROM credit_transporters
        ORDER BY party_name
    """)

    transporters = cur.fetchall()

    conn.close()

    return render_template(
        "edit_transport_entry.html",
        entry=entry,
        transporters=transporters
    )

@app.route(
    "/update-transport-entry/<int:id>",
    methods=["POST"]
)
def update_transport_entry(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    hsd_qty = float(
        request.form.get("hsd_qty") or 0
    )

    rate = float(
        request.form.get("rate") or 0
    )

    cash_taken = float(
        request.form.get("cash_taken") or 0
    )

    hsd_amount = hsd_qty * rate
    total_amount = round(hsd_amount + cash_taken)

    cur.execute("""
        UPDATE transport_entries
        SET
            entry_date=%s,
            transporter_id=%s,
            challan_no=%s,
            vehicle_no=%s,
            slip_no=%s,
            hsd_qty=%s,
            rate=%s,
            hsd_amount=%s,
            cash_taken=%s,
            total_amount=%s
        WHERE id=%s
    """, (

        request.form["entry_date"],
        request.form["transporter_id"],
        request.form.get("challan_no",""),
        request.form.get("vehicle_no",""),
        request.form.get("slip_no",""),

        hsd_qty,
        rate,
        hsd_amount,
        cash_taken,
        total_amount,

        id

    ))

    conn.commit()
    conn.close()

    return redirect(
        url_for("credit_transport")
    )

@app.route("/edit-lube-transaction/<int:id>")
def edit_lube_transaction(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM lube_transactions
        WHERE id=%s
    """, (id,))
    tx = cur.fetchone()

    conn.close()

    return render_template(
        "edit_lube_transaction.html",
        tx=tx
    )

@app.route("/get-daily-closing/<date>")
def get_daily_closing(date):
    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_closing WHERE date = %s ORDER BY id DESC LIMIT 1", (date,))
    row = cur.fetchone()

    cur.execute("""
        SELECT COALESCE(SUM(payment_amount), 0) AS total_received
        FROM transport_payments
        WHERE date <= %s
    """, (date,))
    transport_total = cur.fetchone()["total_received"]

    conn.close()

    if row:
        result = dict(row)
        result["transport_total"] = transport_total
        return jsonify(result)

    return jsonify({"transport_total": transport_total})

# =========================================
# EDIT DAILY CLOSING
# =========================================

@app.route("/save-salary-payment", methods=["POST"])
def save_salary_payment():

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO salary_payments(
            payment_date,
            emp_id,
            employee_name,
            payment_mode,
            bank_account,
            amount,
            month_name,
            remarks
        )
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        request.form["payment_date"],
        request.form["emp_id"],
        request.form["employee_name"],
        request.form["payment_mode"],
        request.form.get("bank_account",""),
        float(request.form["amount"]),
        request.form["month_name"],
        request.form.get("remarks","")
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("attendance"))


@app.route("/edit-attendance/<int:id>")
def edit_attendance(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM attendance
        WHERE id=%s
    """, (id,))
    att = cur.fetchone()

    cur.execute("""
        SELECT *
        FROM staff_master
        ORDER BY staff_name ASC
    """)
    staff_list = cur.fetchall()

    conn.close()

    return render_template(
        "edit_attendance.html",
        att=att,
        staff_list=staff_list
    )

@app.route("/update-attendance/<int:id>", methods=["POST"])
def update_attendance(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE attendance
        SET date=%s,
            staff_name=%s,
            attendance_status=%s
        WHERE id=%s
    """, (
        request.form["date"],
        request.form["staff_name"],
        request.form["attendance_status"],
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("attendance"))

@app.route("/delete-attendance/<int:id>")
def delete_attendance(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM attendance
        WHERE id=%s
    """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("attendance"))

@app.route("/edit-staff/<int:id>")
def edit_staff(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM staff_master
        WHERE id=%s
    """, (id,))

    staff = cur.fetchone()

    conn.close()

    return render_template(
        "edit_staff.html",
        staff=staff
    )
@app.route("/update-staff/<int:id>", methods=["POST"])
def update_staff(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT staff_name
        FROM staff_master
        WHERE id=%s
    """, (id,))

    old = cur.fetchone()

    old_name = old["staff_name"]

    cur.execute("""
        UPDATE staff_master
        SET
            emp_id=%s,
            staff_name=%s,
            role=%s,
            department=%s,
            joined_date=%s,
            bank_account=%s,
            shift=%s,
            status=%s
        WHERE id=%s
    """, (

        request.form.get("emp_id"),
        request.form.get("staff_name"),
        request.form.get("role"),
        request.form.get("department"),
        request.form.get("joined_date"),
        request.form.get("bank_account"),
        request.form.get("shift"),
        request.form.get("status"),
        id
    ))

    # UPDATE ATTENDANCE NAME ALSO

    cur.execute("""
        UPDATE attendance
        SET staff_name=%s
        WHERE staff_name=%s
    """, (
        request.form.get("staff_name"),
        old_name
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("attendance"))

@app.route("/delete-staff/<int:id>")
def delete_staff(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT staff_name
        FROM staff_master
        WHERE id=%s
    """, (id,))

    staff = cur.fetchone()

    if staff:

        staff_name = staff["staff_name"]

        cur.execute("""
            DELETE FROM attendance
            WHERE staff_name=%s
        """, (staff_name,))

        cur.execute("""
            DELETE FROM staff_master
            WHERE id=%s
        """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("attendance"))




@app.route("/edit-daily-closing/<int:id>")
def edit_daily_closing(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM daily_closing
        WHERE id=%s
    """, (id,))

    row = cur.fetchone()

    conn.close()

    return render_template(
        "edit_daily_closing.html",
        row=row
    )


# =========================================
# UPDATE DAILY CLOSING
# =========================================

@app.route("/update-daily-closing/<int:id>",
methods=["POST"])
def update_daily_closing(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""

        UPDATE daily_closing

        SET

            date=%s,

            ms_litres=%s,
            hsd_litres=%s,
            cng_sale=%s,

            total_fuel_sale=%s,

            lube_sale=%s,

            digital_collection=%s,

            total_expense=%s,

            cash_in_hand=%s

        WHERE id=%s

    """, (

        request.form["date"],

        request.form["ms_litres"],
        request.form["hsd_litres"],
        request.form["cng_sale"],

        request.form["total_fuel_sale"],

        request.form["lube_sale"],

        request.form["digital_collection"],

        request.form["total_expense"],

        request.form["cash_in_hand"],

        id

    ))

    conn.commit()
    conn.close()

    return redirect(url_for("reports"))



@app.route("/nozzle-management")
def nozzle_management():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM nozzle_master
        ORDER BY fuel_type ASC, nozzle_name ASC
    """)
    nozzle_master_rows = cur.fetchall()

    cur.execute("""
        SELECT
            nozzle_entries.*,
            nozzle_master.nozzle_name,
            nozzle_master.machine_no,
            nozzle_master.fuel_type
        FROM nozzle_entries
        LEFT JOIN nozzle_master
        ON nozzle_entries.nozzle_id = nozzle_master.id
        ORDER BY nozzle_entries.entry_date DESC,
                 nozzle_entries.id DESC
    """)
    nozzle_rows = cur.fetchall()

    conn.close()

    return render_template(
        "nozzle_management.html",
        nozzle_master_rows=nozzle_master_rows,
        nozzle_rows=nozzle_rows
    )


@app.route("/save-nozzle-master", methods=["POST"])
def save_nozzle_master():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    nozzle_name = request.form.get("nozzle_name", "").strip()
    machine_no = request.form.get("machine_no", "").strip()
    fuel_type = request.form.get("fuel_type", "").strip()
    status = request.form.get("status", "Active").strip()

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO nozzle_master (
            nozzle_name,
            machine_no,
            fuel_type,
            status
        )
        VALUES (%s, %s, %s, %s)
    """, (
        nozzle_name,
        machine_no,
        fuel_type,
        status
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("nozzle_management"))


@app.route("/edit-nozzle-master/<int:id>")
def edit_nozzle_master(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM nozzle_master
        WHERE id = %s
    """, (id,))
    nozzle = cur.fetchone()

    conn.close()

    return render_template(
        "edit_nozzle_master.html",
        nozzle=nozzle
    )


@app.route("/update-nozzle-master/<int:id>", methods=["POST"])
def update_nozzle_master(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    nozzle_name = request.form.get("nozzle_name", "").strip()
    machine_no = request.form.get("machine_no", "").strip()
    fuel_type = request.form.get("fuel_type", "").strip()
    status = request.form.get("status", "Active").strip()

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE nozzle_master
        SET nozzle_name = %s,
            machine_no = %s,
            fuel_type = %s,
            status = %s
        WHERE id = %s
    """, (
        nozzle_name,
        machine_no,
        fuel_type,
        status,
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("nozzle_management"))


@app.route("/delete-nozzle-master/<int:id>")
def delete_nozzle_master(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM nozzle_entries
        WHERE nozzle_id = %s
    """, (id,))

    cur.execute("""
        DELETE FROM nozzle_master
        WHERE id = %s
    """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("nozzle_management"))


@app.route("/save-nozzle-entry", methods=["POST"])
def save_nozzle_entry():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    entry_date = request.form.get("entry_date", "")
    nozzle_id = request.form.get("nozzle_id", "")

    opening_reading = float(request.form.get("opening_reading") or 0)
    closing_reading = float(request.form.get("closing_reading") or 0)
    testing_qty = float(request.form.get("testing_qty") or 0)

    total_sale = round(
      closing_reading
      - opening_reading
      - testing_qty,
    2
)

    if total_sale < 0:
     total_sale = 0

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO nozzle_entries (
            entry_date,
            nozzle_id,
            opening_reading,
            closing_reading,
            testing_qty,
            total_sale,
            created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        entry_date,
        nozzle_id,
        opening_reading,
        closing_reading,
        testing_qty,
        total_sale,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("nozzle_management"))


@app.route("/edit-nozzle-entry/<int:id>")
def edit_nozzle_entry(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM nozzle_entries
        WHERE id = %s
    """, (id,))
    entry = cur.fetchone()

    cur.execute("""
        SELECT *
        FROM nozzle_master
        ORDER BY fuel_type ASC, nozzle_name ASC
    """)
    nozzles = cur.fetchall()

    conn.close()

    return render_template(
        "edit_nozzle_entry.html",
        entry=entry,
        nozzles=nozzles
    )


@app.route("/update-nozzle-entry/<int:id>", methods=["POST"])
def update_nozzle_entry(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    entry_date = request.form.get("entry_date", "")
    nozzle_id = request.form.get("nozzle_id", "")

    opening_reading = float(request.form.get("opening_reading") or 0)
    closing_reading = float(request.form.get("closing_reading") or 0)
    testing_qty = float(request.form.get("testing_qty") or 0)

    total_sale = round(
        closing_reading - opening_reading - testing_qty,
        2
    )

    if total_sale < 0:
        total_sale = 0

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE nozzle_entries
        SET entry_date = %s,
            nozzle_id = %s,
            opening_reading = %s,
            closing_reading = %s,
            testing_qty = %s,
            total_sale = %s,
            created_at = %s
        WHERE id = %s
    """, (
        entry_date,
        nozzle_id,
        opening_reading,
        closing_reading,
        testing_qty,
        total_sale,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("nozzle_management"))


@app.route("/delete-nozzle-entry/<int:id>")
def delete_nozzle_entry(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM nozzle_entries
        WHERE id = %s
    """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("nozzle_management"))


@app.route("/get-nozzle-sales/<date>")
def get_nozzle_sales(date):

    if not session.get("logged_in"):
        return jsonify([])

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""

        SELECT

            nozzle_entries.*,

            nozzle_master.nozzle_name,
            nozzle_master.machine_no,
            nozzle_master.fuel_type

        FROM nozzle_entries

        LEFT JOIN nozzle_master
        ON nozzle_entries.nozzle_id = nozzle_master.id

        WHERE TO_CHAR(nozzle_entries.entry_date, 'YYYY-MM-DD')=%s

        ORDER BY nozzle_master.fuel_type,
                 nozzle_master.nozzle_name

    """, (date,))

    rows = cur.fetchall()

    result = []

    for row in rows:

        result.append({

    "nozzle_name":
    row["nozzle_name"],

    "fuel_type":
    row["fuel_type"],

    "machine_no":
    row["machine_no"],

    "opening":
    row["opening_reading"],

    "closing":
    row["closing_reading"],

    "testing":
    row["testing_qty"],

    "sale":
    row["total_sale"]

})

    conn.close()

    return jsonify(result)


@app.route("/tank-level")
def tank_level():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM tank_level ORDER BY date DESC, id DESC")
    rows = cur.fetchall()

    cur.execute("""
        SELECT
            SUM(gain_qty) AS total_gain_qty,
            SUM(shortage_qty) AS total_shortage_qty,
            SUM(gain_amount) AS total_gain_amount,
            SUM(shortage_amount) AS total_shortage_amount
        FROM tank_level
        WHERE TO_CHAR(date, 'YYYY-MM') = TO_CHAR(CURRENT_DATE, 'YYYY-MM')
    """)
    monthly = cur.fetchone()

    cur.execute("SELECT * FROM tank_level WHERE fuel_type='MS' ORDER BY id DESC LIMIT 1")
    ms_latest = cur.fetchone()

    cur.execute("SELECT * FROM tank_level WHERE fuel_type='HSD' ORDER BY id DESC LIMIT 1")
    hsd_latest = cur.fetchone()

    conn.close()

    return render_template(
        "tank_level.html",
        rows=rows,
        monthly=monthly,
        ms_latest=ms_latest,
        hsd_latest=hsd_latest
    )


@app.route("/save-tank-level", methods=["POST"])
def save_tank_level():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    fuel_type = request.form["fuel_type"]
    opening_stock = float(request.form["opening_stock"] or 0)
    received_stock = float(request.form["received_stock"] or 0)
    own_tanker_stock = float(request.form["own_tanker_stock"] or 0)
    actual_dip = float(request.form["dip_stock"] or 0)

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT ms_litres, hsd_litres
        FROM daily_closing
        WHERE date = %s
        ORDER BY id DESC
        LIMIT 1
    """, (request.form["date"],))
    closing_data = cur.fetchone()

    sale_stock = 0
    if closing_data:
        sale_stock = float(closing_data["ms_litres"] or 0) if fuel_type == "MS" else float(closing_data["hsd_litres"] or 0)

    theoretical_stock = opening_stock + received_stock + own_tanker_stock - sale_stock
    difference = round(actual_dip - theoretical_stock, 2)

    cur.execute("SELECT ms_rate, hsd_rate FROM settings WHERE id = 1")
    settings_data = cur.fetchone()

    rate = float(settings_data["ms_rate"]) if fuel_type == "MS" else float(settings_data["hsd_rate"])

    gain_qty = difference if difference > 0 else 0
    shortage_qty = abs(difference) if difference < 0 else 0
    gain_amount = round(gain_qty * rate, 2)
    shortage_amount = round(shortage_qty * rate, 2)

    cur.execute("""
        INSERT INTO tank_level (
            date, fuel_type, opening_stock, received_stock, own_tanker_stock,
            sale_stock, gain_qty, shortage_qty, current_stock,
            gain_amount, shortage_amount, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        request.form["date"],
        fuel_type,
        opening_stock,
        received_stock,
        own_tanker_stock,
        sale_stock,
        gain_qty,
        shortage_qty,
        actual_dip,
        gain_amount,
        shortage_amount,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("tank_level"))


@app.route("/edit-tank-level/<int:id>")
def edit_tank_level(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM tank_level WHERE id = %s", (id,))
    row = cur.fetchone()

    conn.close()

    return render_template("edit_tank_level.html", row=row)


@app.route("/update-tank-level/<int:id>", methods=["POST"])
def update_tank_level(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    opening_stock = float(request.form["opening_stock"] or 0)
    received_stock = float(request.form["received_stock"] or 0)
    own_tanker_stock = float(request.form["own_tanker_stock"] or 0)
    sale_stock = float(request.form["sale_stock"] or 0)
    actual_dip = float(request.form["current_stock"] or 0)

    theoretical_stock = opening_stock + received_stock + own_tanker_stock - sale_stock
    difference = round(actual_dip - theoretical_stock, 2)

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT ms_rate, hsd_rate FROM settings WHERE id = 1")
    settings_data = cur.fetchone()

    rate = float(settings_data["ms_rate"]) if request.form["fuel_type"] == "MS" else float(settings_data["hsd_rate"])

    gain_qty = difference if difference > 0 else 0
    shortage_qty = abs(difference) if difference < 0 else 0
    gain_amount = round(gain_qty * rate, 2)
    shortage_amount = round(shortage_qty * rate, 2)

    cur.execute("""
        UPDATE tank_level
        SET date=%s, fuel_type=%s, opening_stock=%s, received_stock=%s,
            own_tanker_stock=%s, sale_stock=%s, gain_qty=%s, shortage_qty=%s,
            current_stock=%s, gain_amount=%s, shortage_amount=%s, created_at=%s
        WHERE id=%s
    """, (
        request.form["date"],
        request.form["fuel_type"],
        opening_stock,
        received_stock,
        own_tanker_stock,
        sale_stock,
        gain_qty,
        shortage_qty,
        actual_dip,
        gain_amount,
        shortage_amount,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("tank_level"))


@app.route("/delete-tank-level/<int:id>")
def delete_tank_level(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM tank_level WHERE id = %s", (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("tank_level"))


@app.route("/credit-transport")
def credit_transport():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM credit_transporters
        ORDER BY party_name
    """)
    transporters = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM transport_entries
        ORDER BY entry_date DESC,id DESC
    """)
    entries = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*) as total
        FROM credit_transporters
    """)
    total_transporters = cur.fetchone()["total"]

    cur.execute("""
SELECT COALESCE(SUM(credit_given), 0) AS total_credit
FROM credit_transporters
""")

    row = cur.fetchone()

    total_credit = row["total_credit"] if row else 0

    cur.execute("""
SELECT hsd_rate
FROM settings
LIMIT 1
""")

    row = cur.fetchone()

    hsd_rate = row["hsd_rate"] if row else 0

    conn.close()

    return render_template(
        "credit_transport.html",
        transporters=transporters,
        entries=entries,
        total_transporters=total_transporters,
        hsd_rate=hsd_rate,
        total_credit=total_credit
    )

@app.route("/add-transporter", methods=["POST"])
def add_transporter():

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO credit_transporters(
            party_name,
            mobile,
            vehicle_no,
            opening_balance,
            balance_due,
            status
        )
        VALUES(%s,%s,%s,%s,%s,%s)
    """,(
        request.form.get("party_name",""),
request.form.get("mobile",""),
request.form.get("vehicle_no",""),
float(request.form.get("opening_balance",0)),
float(request.form.get("opening_balance",0)),
request.form.get("status","Active")
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))

@app.route("/save-transport-entry", methods=["POST"])
def save_transport_entry():

    conn = get_pg_conn()
    cur = conn.cursor()

    entry_date = request.form["entry_date"]

    cur.execute("""
        SELECT COUNT(*) as total
        FROM transport_entries
        WHERE entry_date=%s
    """,(entry_date,))

    sl_no = cur.fetchone()["total"] + 1

    qty = float(request.form.get("hsd_qty") or 0)
    rate = float(request.form.get("rate") or 0)
    cash_taken = float(request.form.get("cash_taken") or 0)

    hsd_amount = qty * rate
    total_amount = round(hsd_amount + cash_taken)

    cur.execute("""
        INSERT INTO transport_entries(
            entry_date,
            sl_no,
            transporter_id,
            transporter_name,
            challan_no,
            vehicle_no,
            slip_no,
            hsd_qty,
            rate,
            hsd_amount,
            cash_taken,
            total_amount
        )
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """,(
        entry_date,
        sl_no,
        request.form["transporter_id"],
        request.form["transporter_name"],
        request.form["challan_no"],
        request.form["vehicle_no"],
        request.form["slip_no"],
        qty,
        rate,
        hsd_amount,
        cash_taken,
        total_amount
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))

@app.route("/save-transport-payment", methods=["POST"])
def save_transport_payment():

    transporter_id = request.form["transporter_id"]

    amount = float(
        request.form["payment_amount"]
    )

    date = request.form["date"]

    payment_type = request.form["payment_type"]

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE credit_transporters
        SET
        payment_received=
        COALESCE(payment_received,0)+%s,
        balance_due=
        balance_due-%s
        WHERE id=%s
    """,(
        amount,
        amount,
        transporter_id
    ))

    cur.execute("""
        SELECT *
        FROM credit_transporters
        WHERE id=%s
    """,(transporter_id,))

    party = cur.fetchone()

    cur.execute("""
        INSERT INTO transporter_ledger(
            date,
            transporter_id,
            transporter_name,
            entry_type,
            received_amount,
            balance_after,
            remarks
        )
        VALUES(%s,%s,%s,%s,%s,%s,%s)
    """,(
        date,
        transporter_id,
        party["party_name"],
        payment_type,
        amount,
        party["balance_due"],
        "Payment Received"
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))

@app.route("/transporter-history/<int:id>")
def transporter_history(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM credit_transporters
        WHERE id=%s
    """,(id,))

    transporter = cur.fetchone()

    cur.execute("""
        SELECT *
        FROM transporter_ledger
        WHERE transporter_id=%s
        ORDER BY id DESC
    """,(id,))

    history = cur.fetchall()

    conn.close()

    return render_template(
        "transporter_history.html",
        transporter=transporter,
        history=history
    )

@app.route("/edit-transporter/<int:id>")
def edit_transporter(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM credit_transporters
        WHERE id=%s
    """,(id,))

    transporter = cur.fetchone()

    conn.close()

    return render_template(
        "edit_transporter.html",
        transporter=transporter
    )

@app.route("/update-transporter/<int:id>",methods=["POST"])
def update_transporter(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE credit_transporters
        SET
        party_name=%s,
        mobile=%s,
        vehicle_no=%s,
        status=%s
        WHERE id=%s
    """,(
        request.form.get("party_name",""),
request.form.get("mobile",""),
request.form.get("vehicle_no",""),
request.form.get("status","Active"),
id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))

@app.route("/delete-daily-closing/<int:id>")
def delete_daily_closing(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT date FROM daily_closing WHERE id=%s",
        (id,)
    )

    row = cur.fetchone()

    if row:

        report_date = row["date"]

        cur.execute(
            "DELETE FROM daily_closing WHERE id=%s",
            (id,)
        )

        cur.execute(
            "DELETE FROM nozzle_entries WHERE entry_date=%s",
            (report_date,)
        )

    conn.commit()
    conn.close()

    return redirect(url_for("reports"))

@app.route("/delete-transporter/<int:id>")
def delete_transporter(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM credit_transporters
        WHERE id=%s
    """,(id,))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))

@app.route("/delete-transport-entry/<int:id>")
def delete_transport_entry(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM transport_entries
        WHERE id=%s
    """,(id,))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/digital-collection")
def digital_collection():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            SUM(digital_collection) AS total_digital,
            SUM(phonepe) AS total_phonepe,
            SUM(card_swipe) AS total_card,
            SUM(hp_pay) AS total_hp_pay,
            SUM(hpcl_otp) AS total_hpcl_otp,
            SUM(upi_other) AS total_upi,
            SUM(transport_received) AS total_transport
        FROM daily_closing
        WHERE TO_CHAR(date, 'YYYY-MM') = TO_CHAR(CURRENT_DATE, 'YYYY-MM')
    """)
    monthly = cur.fetchone()

    cur.execute("""
        SELECT date, digital_collection, phonepe, card_swipe,
               hp_pay, hpcl_otp, upi_other, transport_received
        FROM daily_closing
        ORDER BY id DESC
        LIMIT 50
    """)
    rows = cur.fetchall()

    conn.close()

    return render_template("digital_collection.html", rows=rows, monthly=monthly)


@app.route("/reports")
def reports():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    fuel_type = request.args.get("fuel_type", "")
    search = request.args.get("search", "")

    # DAILY CLOSING
    
    # DAILY CLOSING WITH NOZZLE-WISE HOVER DATA
    daily_query = """
     SELECT

        dc.*,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE TO_CHAR(ne.entry_date, 'YYYY-MM-DD') = TO_CHAR(dc.date, 'YYYY-MM-DD')
            AND nm.nozzle_name = 'MS1'
        ),0) AS ms1,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE TO_CHAR(ne.entry_date, 'YYYY-MM-DD') = TO_CHAR(dc.date, 'YYYY-MM-DD')
            AND nm.nozzle_name = 'MS2'
        ),0) AS ms2,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE TO_CHAR(ne.entry_date, 'YYYY-MM-DD') = TO_CHAR(dc.date, 'YYYY-MM-DD')
            AND nm.nozzle_name = 'MS3'
        ),0) AS ms3,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE TO_CHAR(ne.entry_date, 'YYYY-MM-DD') = TO_CHAR(dc.date, 'YYYY-MM-DD')
            AND nm.nozzle_name = 'HSD1'
        ),0) AS hsd1,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE TO_CHAR(ne.entry_date, 'YYYY-MM-DD') = TO_CHAR(dc.date, 'YYYY-MM-DD')
            AND nm.nozzle_name = 'HSD2'
        ),0) AS hsd2,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE TO_CHAR(ne.entry_date, 'YYYY-MM-DD') = TO_CHAR(dc.date, 'YYYY-MM-DD')
            AND nm.nozzle_name = 'HSD3'
        ),0) AS hsd3,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE TO_CHAR(ne.entry_date, 'YYYY-MM-DD') = TO_CHAR(dc.date, 'YYYY-MM-DD')
            AND nm.nozzle_name = 'CNG1'
        ),0) AS cng1,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE TO_CHAR(ne.entry_date, 'YYYY-MM-DD') = TO_CHAR(dc.date, 'YYYY-MM-DD')
            AND nm.nozzle_name = 'CNG2'
        ),0) AS cng2

    FROM daily_closing dc

    WHERE 1=1
"""

    daily_params = []

    if from_date:
        daily_query += " AND dc.date >= %s"
        daily_params.append(from_date)

    if to_date:
        daily_query += " AND dc.date <= %s"
        daily_params.append(to_date)

    if search:
        daily_query += " AND TO_CHAR(dc.date, 'YYYY-MM-DD') ILIKE %s"
        daily_params.append(f"%{search}%")

    daily_query += " ORDER BY dc.date DESC, dc.id DESC"

    cur.execute(daily_query, daily_params)
    daily_rows = cur.fetchall()

    # NOZZLE REPORT
    nozzle_query = """
        SELECT
            nozzle_entries.*,
            nozzle_master.nozzle_name,
            nozzle_master.machine_no,
            nozzle_master.fuel_type
        FROM nozzle_entries
        LEFT JOIN nozzle_master
        ON nozzle_entries.nozzle_id = nozzle_master.id
        WHERE 1=1
    """
    nozzle_params = []

    if from_date:
        nozzle_query += " AND nozzle_entries.entry_date >= %s"
        nozzle_params.append(from_date)

    if to_date:
        nozzle_query += " AND nozzle_entries.entry_date <= %s"
        nozzle_params.append(to_date)

    if fuel_type:
        nozzle_query += " AND nozzle_master.fuel_type = %s"
        nozzle_params.append(fuel_type)

    if search:
        nozzle_query += """
            AND (
                nozzle_master.nozzle_name ILIKE %s
                OR nozzle_master.machine_no ILIKE %s
            )
        """
        nozzle_params.extend([f"%{search}%", f"%{search}%"])

    nozzle_query += """
        ORDER BY nozzle_entries.entry_date DESC, nozzle_entries.id DESC
    """

    cur.execute(nozzle_query, nozzle_params)
    nozzle_rows = cur.fetchall()

    # TANK REPORT
    tank_query = """
        SELECT *
        FROM tank_level
        WHERE 1=1
    """
    tank_params = []

    if from_date:
        tank_query += " AND date >= %s"
        tank_params.append(from_date)

    if to_date:
        tank_query += " AND date <= %s"
        tank_params.append(to_date)

    if fuel_type:
        tank_query += " AND fuel_type = %s"
        tank_params.append(fuel_type)

    tank_query += " ORDER BY date DESC, id DESC"

    cur.execute(tank_query, tank_params)
    tank_rows = cur.fetchall()

    # LUBE REPORT
    lube_query = """
        SELECT *
        FROM lube_stock
        WHERE 1=1
    """
    lube_params = []

    if search:
        lube_query += " AND product_name ILIKE %s"
        lube_params.append(f"%{search}%")

    lube_query += " ORDER BY product_name ASC"

    cur.execute(lube_query, lube_params)
    lube_rows = cur.fetchall()

    # CREDIT TRANSPORT REPORT
    transport_query = """
        SELECT *
        FROM credit_transporters
        WHERE 1=1
    """
    transport_params = []

    if search:
        transport_query += " AND party_name ILIKE %s"
        transport_params.append(f"%{search}%")

    transport_query += " ORDER BY balance_due DESC, party_name ASC"

    cur.execute(transport_query, transport_params)
    transporter_rows = cur.fetchall()

    # TRANSPORT ENTRY REPORT
    transport_entry_query = """
        SELECT *
        FROM transport_entries
        WHERE 1=1
    """
    transport_entry_params = []

    if from_date:
        transport_entry_query += " AND entry_date >= %s"
        transport_entry_params.append(from_date)

    if to_date:
        transport_entry_query += " AND entry_date <= %s"
        transport_entry_params.append(to_date)

    if search:
        transport_entry_query += """
            AND (
                transporter_name ILIKE %s
                OR challan_no ILIKE %s
                OR vehicle_no ILIKE %s
                OR slip_no ILIKE %s
            )
        """
        transport_entry_params.extend([
            f"%{search}%",
            f"%{search}%",
            f"%{search}%",
            f"%{search}%"
        ])

    transport_entry_query += " ORDER BY entry_date DESC, sl_no DESC"

    cur.execute(transport_entry_query, transport_entry_params)
    transport_entry_rows = cur.fetchall()

    transport_total_hsd = sum(float(r["hsd_qty"] or 0) for r in transport_entry_rows)
    transport_total_amount = sum(float(r["total_amount"] or 0) for r in transport_entry_rows)

    conn.close()

    return render_template(
        "reports.html",
        daily_rows=daily_rows,
        nozzle_rows=nozzle_rows,
        tank_rows=tank_rows,
        lube_rows=lube_rows,
        transporter_rows=transporter_rows,
        transport_entry_rows=transport_entry_rows,
        transport_total_hsd=transport_total_hsd,
        transport_total_amount=transport_total_amount
    )


@app.route("/export-full-backup")
def export_full_backup():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_admin():
        return redirect(url_for("dashboard"))

    conn = get_pg_conn()
    cur = conn.cursor()
    wb = Workbook()

    sheets = [
        ("Daily Closing", "SELECT * FROM daily_closing ORDER BY date DESC", [
            "Date", "MS Litres", "HSD Litres", "CNG Sale", "Fuel Sale", "Lube Sale", "Digital Collection",
            "PhonePe", "Card Swipe", "HP Pay", "HPCL OTP", "UPI Other", "Credit Given",
            "Transport Received", "Net Credit Due", "Expense", "Cash In Hand"
        ], lambda r: [
            r["date"], r["ms_litres"], r["hsd_litres"], r["cng_sale"], r["total_fuel_sale"], r["lube_sale"],
            r["digital_collection"], r["phonepe"], r["card_swipe"], r["hp_pay"], r["hpcl_otp"],
            r["upi_other"], r["credit_given"], r["transport_received"], r["net_credit_due"],
            r["total_expense"], r["cash_in_hand"]
        ]),

        ("Tank Level", "SELECT * FROM tank_level ORDER BY date DESC", [
            "Date", "Fuel", "Opening", "Received", "Own Tanker", "Sale", "Current",
            "Gain Qty", "Shortage Qty", "Gain Amount", "Shortage Amount", "Updated"
        ], lambda r: [
            r["date"], r["fuel_type"], r["opening_stock"], r["received_stock"], r["own_tanker_stock"],
            r["sale_stock"], r["current_stock"], r["gain_qty"], r["shortage_qty"],
            r["gain_amount"], r["shortage_amount"], r["created_at"]
        ]),

        ("Nozzle Master", "SELECT * FROM nozzle_master ORDER BY fuel_type, nozzle_name", [
            "Nozzle", "Machine No", "Fuel", "Status"
        ], lambda r: [
            r["nozzle_name"], r["machine_no"], r["fuel_type"], r["status"]
        ]),

        ("Nozzle Entries", """
            SELECT nozzle_entries.*, nozzle_master.nozzle_name, nozzle_master.fuel_type, nozzle_master.machine_no
            FROM nozzle_entries
            LEFT JOIN nozzle_master ON nozzle_entries.nozzle_id = nozzle_master.id
            ORDER BY nozzle_entries.entry_date DESC
        """, [
            "Date", "Nozzle", "Machine", "Fuel", "Opening", "Closing", "Testing", "Sale", "Updated"
        ], lambda r: [
            r["entry_date"], r["nozzle_name"], r["machine_no"], r["fuel_type"],
            r["opening_reading"], r["closing_reading"], r["testing_qty"], r["total_sale"], r["created_at"]
        ]),

        ("Lube Stock", "SELECT * FROM lube_stock ORDER BY product_name ASC", [
            "Product", "Rate", "Opening", "Purchase", "Sale", "Closing"
        ], lambda r: [
            r["product_name"], r["selling_rate"], r["opening_stock"], r["purchase_qty"],
            r["sale_qty"], r["closing_stock"]
        ]),

        ("Transporters", "SELECT * FROM credit_transporters ORDER BY party_name ASC", [
            "Party", "Opening", "Credit Given", "Payment Received", "Balance Due", "Status"
        ], lambda r: [
            r["party_name"], r["opening_balance"], r["credit_given"], r["payment_received"],
            r["balance_due"], r["status"]
        ]),

        ("Transport Entries", "SELECT * FROM transport_entries ORDER BY entry_date DESC, sl_no DESC", [
            "SL No", "Date", "Transport Name", "Challan No", "Vehicle No", "Slip No",
            "HSD Qty", "Rate", "HSD Amount", "Cash Taken", "Total Amount", "Updated"
        ], lambda r: [
            r["sl_no"], r["entry_date"], r["transporter_name"], r["challan_no"], r["vehicle_no"],
            r["slip_no"], r["hsd_qty"], r["rate"], r["hsd_amount"], r["cash_taken"],
            r["total_amount"], r["created_at"]
        ]),

        ("Attendance", "SELECT * FROM attendance ORDER BY date DESC", [
            "Date", "Staff Name", "Attendance"
        ], lambda r: [
            r["date"], r["staff_name"], r["attendance_status"]
        ])
    ]

    first = True

    for title, query, headers, row_func in sheets:
        ws = wb.active if first else wb.create_sheet(title)
        ws.title = title
        first = False

        ws.append(headers)

        cur.execute(query)
        rows = cur.fetchall()

        for row in rows:
            ws.append(row_func(row))

        style_excel_sheet(ws)

    conn.close()

    file = BytesIO()
    wb.save(file)
    file.seek(0)

    return send_file(
        file,
        as_attachment=True,
        download_name="Sai_Fuel_Mart_Full_Backup.xlsx"
    )


@app.route("/export-nozzle-report")
def export_nozzle_report():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_admin():
        return redirect(url_for("dashboard"))

    # export code

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            nozzle_entries.*,
            nozzle_master.nozzle_name,
            nozzle_master.fuel_type,
            nozzle_master.machine_no
        FROM nozzle_entries
        LEFT JOIN nozzle_master
        ON nozzle_entries.nozzle_id = nozzle_master.id
        ORDER BY nozzle_entries.entry_date DESC
    """)
    rows = cur.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Nozzle Entries"

    ws.append([
        "Date", "Nozzle", "Machine", "Fuel",
        "Opening", "Closing", "Testing", "Sale", "Updated"
    ])

    for row in rows:
        ws.append([
            row["entry_date"], row["nozzle_name"], row["machine_no"],
            row["fuel_type"], row["opening_reading"], row["closing_reading"],
            row["testing_qty"], row["total_sale"], row["created_at"]
        ])

    style_excel_sheet(ws)

    conn.close()

    file = BytesIO()
    wb.save(file)
    file.seek(0)

    return send_file(file, as_attachment=True, download_name="Nozzle_Report.xlsx")


@app.route("/export-reports")
def export_reports():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_admin():
        return redirect(url_for("dashboard"))

    # export code

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_closing ORDER BY date DESC")
    rows = cur.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Closing"

    ws.append([
        "Date", "MS Litres", "HSD Litres", "CNG Sale", "Fuel Sale", "Lube Sale",
        "Digital", "PhonePe", "Card", "HP Pay", "HPCL OTP", "UPI Other",
        "Credit Given", "Transport Received", "Net Credit Due", "Expense", "Cash"
    ])

    for row in rows:
        ws.append([
            row["date"], row["ms_litres"], row["hsd_litres"], row["cng_sale"], row["total_fuel_sale"],
            row["lube_sale"], row["digital_collection"], row["phonepe"], row["card_swipe"],
            row["hp_pay"], row["hpcl_otp"], row["upi_other"], row["credit_given"],
            row["transport_received"], row["net_credit_due"], row["total_expense"],
            row["cash_in_hand"]
        ])

    style_excel_sheet(ws)
    conn.close()

    file = BytesIO()
    wb.save(file)
    file.seek(0)

    return send_file(file, as_attachment=True, download_name="Daily_Closing_Report.xlsx")


@app.route("/export-tank-report")
def export_tank_report():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_admin():
        return redirect(url_for("dashboard"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM tank_level ORDER BY date DESC")
    rows = cur.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Tank Level"

    ws.append([
        "Date", "Fuel", "Opening", "Received", "Own Tanker", "Sale",
        "Current", "Gain Qty", "Shortage Qty", "Gain Amount", "Shortage Amount", "Updated"
    ])

    for row in rows:
        ws.append([
            row["date"], row["fuel_type"], row["opening_stock"], row["received_stock"],
            row["own_tanker_stock"], row["sale_stock"], row["current_stock"],
            row["gain_qty"], row["shortage_qty"], row["gain_amount"],
            row["shortage_amount"], row["created_at"]
        ])

    style_excel_sheet(ws)
    conn.close()

    file = BytesIO()
    wb.save(file)
    file.seek(0)

    return send_file(file, as_attachment=True, download_name="Tank_Level_Report.xlsx")


@app.route("/export-lube-report")
def export_lube_report():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_admin():
        return redirect(url_for("dashboard"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM lube_stock ORDER BY closing_stock ASC")
    rows = cur.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Lube Stock"

    ws.append(["Product", "Rate", "Opening", "Purchase", "Sale", "Closing"])

    for row in rows:
        ws.append([
            row["product_name"], row["selling_rate"], row["opening_stock"],
            row["purchase_qty"], row["sale_qty"], row["closing_stock"]
        ])

    style_excel_sheet(ws)
    conn.close()

    file = BytesIO()
    wb.save(file)
    file.seek(0)

    return send_file(file, as_attachment=True, download_name="Lube_Stock_Report.xlsx")


@app.route("/export-transport-report")
def export_transport_report():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_admin():
        return redirect(url_for("dashboard"))
    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM credit_transporters ORDER BY balance_due DESC")
    rows = cur.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Transport Credit"

    ws.append(["Party", "Opening", "Credit Given", "Payment Received", "Balance Due", "Status"])

    for row in rows:
        ws.append([
            row["party_name"], row["opening_balance"], row["credit_given"],
            row["payment_received"], row["balance_due"], row["status"]
        ])

    style_excel_sheet(ws)
    conn.close()

    file = BytesIO()
    wb.save(file)
    file.seek(0)

    return send_file(file, as_attachment=True, download_name="Transport_Credit_Report.xlsx")



@app.route("/export-transport-entry-report")
def export_transport_entry_report():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_admin():
        return redirect(url_for("dashboard"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM transport_entries ORDER BY entry_date DESC, sl_no DESC")
    rows = cur.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Transport Entries"

    ws.append([
        "SL No", "Date", "Transport Name", "Challan No", "Vehicle No",
        "Slip No", "HSD Qty", "Rate", "HSD Amount", "Cash Taken",
        "Total Amount", "Updated"
    ])

    for row in rows:
        ws.append([
            row["sl_no"], row["entry_date"], row["transporter_name"], row["challan_no"],
            row["vehicle_no"], row["slip_no"], row["hsd_qty"], row["rate"],
            row["hsd_amount"], row["cash_taken"], row["total_amount"], row["created_at"]
        ])

    style_excel_sheet(ws)
    conn.close()

    file = BytesIO()
    wb.save(file)
    file.seek(0)

    return send_file(file, as_attachment=True, download_name="Transport_Entries.xlsx")


@app.route("/delete-report/<int:id>")
def delete_report(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM daily_closing WHERE id = %s", (id,))
    conn.commit()
    conn.close()

    return redirect(url_for("reports"))


@app.route("/edit-report/<int:id>")
def edit_report(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM daily_closing WHERE id = %s", (id,))
    row = cur.fetchone()
    conn.close()

    return render_template("edit_report.html", row=row)


@app.route("/update-report/<int:id>", methods=["POST"])
def update_report(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE daily_closing
        SET date=%s, ms_litres=%s, hsd_litres=%s, cng_sale=%s, total_fuel_sale=%s,
            lube_sale=%s, digital_collection=%s, phonepe=%s, card_swipe=%s,
            hp_pay=%s, hpcl_otp=%s, upi_other=%s, credit_given=%s,
            transport_received=%s, net_credit_due=%s, total_expense=%s, cash_in_hand=%s
        WHERE id=%s
    """, (
        request.form["date"], request.form["ms_litres"], request.form["hsd_litres"],
        request.form.get("cng_sale",0), request.form["total_fuel_sale"], request.form["lube_sale"],
        request.form.get("digital_collection", 0), request.form.get("phonepe", 0),
        request.form.get("card_swipe", 0), request.form.get("hp_pay", 0),
        request.form.get("hpcl_otp", 0), request.form.get("upi_other", 0),
        request.form["credit_given"], request.form["transport_received"],
        request.form["net_credit_due"], request.form["total_expense"],
        request.form["cash_in_hand"], id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("reports"))


@app.route("/settings")
def settings():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM settings WHERE id=1")
    settings_data = cur.fetchone()
    conn.close()

    return render_template("settings.html", settings=settings_data)

# =========================================
# SAVE SETTINGS
# =========================================

@app.route("/save-settings", methods=["POST"])
def save_settings():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    ms_rate = float(request.form.get("ms_rate", 0))
    hsd_rate = float(request.form.get("hsd_rate", 0))
    cng_rate = float(request.form.get("cng_rate", 0))

    conn = get_pg_conn()
    cur = conn.cursor()

    # CHECK EXISTING SETTINGS

    cur.execute("""
        SELECT id
        FROM settings
        LIMIT 1
    """)

    existing = cur.fetchone()

    # UPDATE

    if existing:

        cur.execute("""

            UPDATE settings

            SET

                ms_rate=%s,
                hsd_rate=%s,
                cng_rate=%s

            WHERE id=%s

        """, (

            ms_rate,
            hsd_rate,
            cng_rate,

            existing["id"]

        ))

    # INSERT

    else:

        cur.execute("""

            INSERT INTO settings (

                ms_rate,
                hsd_rate,
                cng_rate

            )

            VALUES (%s, %s, %s)

        """, (

            ms_rate,
            hsd_rate,
            cng_rate

        ))

    conn.commit()
    conn.close()

    return redirect(url_for("settings"))



@app.route("/lube-stock")
def lube_stock():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM lube_stock
        ORDER BY product_name ASC
    """)
    lube_items = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM lube_transactions
        ORDER BY date DESC, id DESC
    """)
    lube_transactions = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*) AS total_products,
               COALESCE(SUM(closing_stock),0) AS total_stock,
               COALESCE(SUM(sale_qty),0) AS total_sold
        FROM lube_stock
    """)
    summary = cur.fetchone()

    product_data = {}

    for item in lube_items:
        product_id = item["id"]

        cur.execute("""
            SELECT *
            FROM lube_transactions
            WHERE product_id=%s
            ORDER BY date DESC, id DESC
        """, (product_id,))
        transactions = cur.fetchall()

        total_purchase = sum(float(t["qty"] or 0) for t in transactions if t["transaction_type"] == "Purchase")
        total_sale = sum(float(t["qty"] or 0) for t in transactions if t["transaction_type"] == "Sale")

        product_data[product_id] = {
            "transactions": transactions,
            "total_purchase": total_purchase,
            "total_sale": total_sale
        }

    conn.close()

    return render_template(
        "lube_stock.html",
        lube_items=lube_items,
        lube_transactions=lube_transactions,
        product_data=product_data,
        summary=summary
    )

@app.route("/save-lube-transaction", methods=["POST"])
def save_lube_transaction():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    date = request.form.get("date")
    product_id = request.form.get("product_id")
    transaction_type = request.form.get("transaction_type")
    qty = float(request.form.get("qty") or 0)
    rate = float(request.form.get("rate") or 0)
    remarks = request.form.get("remarks", "")

    amount = round(qty * rate, 2)

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT product_name
        FROM lube_stock
        WHERE id=%s
    """, (product_id,))
    product = cur.fetchone()

    if not product:
        conn.close()
        return redirect(url_for("lube_stock"))

    product_name = product["product_name"]

    cur.execute("""
        INSERT INTO lube_transactions (
            date, product_id, product_name, transaction_type,
            qty, rate, amount, remarks
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        date, product_id, product_name, transaction_type,
        qty, rate, amount, remarks
    ))

    if transaction_type == "Purchase":
        cur.execute("""
            UPDATE lube_stock
            SET purchase_qty = purchase_qty + %s,
                closing_stock = closing_stock + %s
            WHERE id=%s
        """, (qty, qty, product_id))

    elif transaction_type == "Sale":
        cur.execute("""
            UPDATE lube_stock
            SET sale_qty = sale_qty + %s,
                closing_stock = closing_stock - %s
            WHERE id=%s
        """, (qty, qty, product_id))

    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))

@app.route("/delete-lube-transaction/<int:id>")
def delete_lube_transaction(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM lube_transactions
        WHERE id=%s
    """, (id,))
    tx = cur.fetchone()

    if tx:
        product_id = tx["product_id"]
        qty = float(tx["qty"] or 0)

        if tx["transaction_type"] == "Purchase":
            cur.execute("""
                UPDATE lube_stock
                SET purchase_qty = purchase_qty - %s,
                    closing_stock = closing_stock - %s
                WHERE id=%s
            """, (qty, qty, product_id))

        elif tx["transaction_type"] == "Sale":
            cur.execute("""
                UPDATE lube_stock
                SET sale_qty = sale_qty - %s,
                    closing_stock = closing_stock + %s
                WHERE id=%s
            """, (qty, qty, product_id))

        cur.execute("""
            DELETE FROM lube_transactions
            WHERE id=%s
        """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))

@app.route("/edit-lube/<int:id>")
def edit_lube(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM lube_stock
        WHERE id=%s
    """, (id,))

    item = cur.fetchone()

    conn.close()

    return render_template(
        "edit_lube.html",
        item=item
    )

@app.route("/update-lube-transaction/<int:id>", methods=["POST"])
def update_lube_transaction(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM lube_transactions
        WHERE id=%s
    """, (id,))
    old = cur.fetchone()

    if not old:
        conn.close()
        return redirect(url_for("lube_stock"))

    old_qty = float(old["qty"] or 0)
    old_type = old["transaction_type"]
    product_id = old["product_id"]

    # reverse old stock effect
    if old_type == "Purchase":
        cur.execute("""
            UPDATE lube_stock
            SET purchase_qty = purchase_qty - %s,
                closing_stock = closing_stock - %s
            WHERE id=%s
        """, (old_qty, old_qty, product_id))

    elif old_type == "Sale":
        cur.execute("""
            UPDATE lube_stock
            SET sale_qty = sale_qty - %s,
                closing_stock = closing_stock + %s
            WHERE id=%s
        """, (old_qty, old_qty, product_id))

    new_date = request.form.get("date")
    new_type = request.form.get("transaction_type")
    new_qty = float(request.form.get("qty") or 0)
    new_rate = float(request.form.get("rate") or 0)
    new_amount = round(new_qty * new_rate, 2)
    remarks = request.form.get("remarks", "")

    # apply new stock effect
    if new_type == "Purchase":
        cur.execute("""
            UPDATE lube_stock
            SET purchase_qty = purchase_qty + %s,
                closing_stock = closing_stock + %s
            WHERE id=%s
        """, (new_qty, new_qty, product_id))

    elif new_type == "Sale":
        cur.execute("""
            UPDATE lube_stock
            SET sale_qty = sale_qty + %s,
                closing_stock = closing_stock - %s
            WHERE id=%s
        """, (new_qty, new_qty, product_id))

    cur.execute("""
        UPDATE lube_transactions
        SET date=%s,
            transaction_type=%s,
            qty=%s,
            rate=%s,
            amount=%s,
            remarks=%s
        WHERE id=%s
    """, (
        new_date,
        new_type,
        new_qty,
        new_rate,
        new_amount,
        remarks,
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))

@app.route("/update-lube/<int:id>", methods=["POST"])
def update_lube(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE lube_stock
        SET
            product_name=%s,
            selling_rate=%s,
            opening_stock=%s,
            purchase_qty=%s,
            sale_qty=%s,
            closing_stock=%s
        WHERE id=%s
    """, (

        request.form.get("product_name"),
        request.form.get("selling_rate"),
        request.form.get("opening_stock"),
        request.form.get("purchase_qty"),
        request.form.get("sale_qty"),
        request.form.get("closing_stock"),
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))
# =========================================
# ATTENDANCE DATABASE UPGRADE
# put this inside init_db(), after staff_master table
# =========================================




# =========================================
# ATTENDANCE PAGE
# =========================================

@app.route("/attendance")
def attendance():

    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    selected_date = request.args.get(
    "date",
    datetime.now().strftime("%Y-%m-%d")
)

    conn = get_pg_conn()
    cur = conn.cursor()

    current_date = datetime.now().strftime("%Y-%m-%d")

    cur.execute("""
        SELECT *
        FROM staff_master
        ORDER BY id DESC
    """)
    staff_list = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM attendance
        ORDER BY date DESC, id DESC
    """)
    attendance_list = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM salary_payments
        ORDER BY payment_date DESC, id DESC
    """)
    salaries = cur.fetchall()

    total_staff = len(staff_list)

    present_today = 0
    absent_today = 0
    leave_today = 0

    for row in attendance_list:
        if str(row["date"]) == current_date:
            if row["attendance_status"] == "Present":
                present_today += 1
            elif row["attendance_status"] == "Absent":
                absent_today += 1
            elif row["attendance_status"] == "Leave":
                leave_today += 1

    staff_attendance = {}

    for staff in staff_list:

        staff_name = staff["staff_name"]

        staff_attendance[staff_name] = {
            "present": 0,
            "absent": 0,
            "leave": 0,
            "records": []
        }

        for att in attendance_list:

            if att["staff_name"] == staff_name:

                staff_attendance[staff_name]["records"].append(att)

                if att["attendance_status"] == "Present":
                    staff_attendance[staff_name]["present"] += 1

                elif att["attendance_status"] == "Absent":
                    staff_attendance[staff_name]["absent"] += 1

                elif att["attendance_status"] == "Leave":
                    staff_attendance[staff_name]["leave"] += 1

    salary_by_staff = {}

    for s in salaries:

        employee_name = s["employee_name"]

        if employee_name not in salary_by_staff:
            salary_by_staff[employee_name] = []

        salary_by_staff[employee_name].append(s)

    cur.execute("""
        SELECT emp_id
        FROM staff_master
        ORDER BY id DESC
        LIMIT 1
    """)
    last_emp = cur.fetchone()

    if last_emp and last_emp["emp_id"]:
        try:
            last_num = int(last_emp["emp_id"].replace("EMP", ""))
            next_emp_id = f"EMP{last_num + 1:03d}"
        except:
            next_emp_id = "EMP001"
    else:
        next_emp_id = "EMP001"

    conn.close()

    return render_template(
        "attendance.html",
        staff_list=staff_list,
        attendance_list=attendance_list,
        staff_attendance=staff_attendance,
        current_date=current_date,
        total_staff=total_staff,
        present_today=present_today,
        absent_today=absent_today,
        leave_today=leave_today,
        next_emp_id=next_emp_id,
        salaries=salaries,
        selected_date=selected_date,
        salary_by_staff=salary_by_staff
    )


@app.route("/update-salary/<int:id>", methods=["POST"])
def update_salary(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE salary_payments
        SET
            payment_date=%s,
            month_name=%s,
            employee_name=%s,
            payment_mode=%s,
            bank_account=%s,
            amount=%s,
            remarks=%s
        WHERE id=%s
    """, (

        request.form["payment_date"],
        request.form["month_name"],
        request.form["employee_name"],
        request.form["payment_mode"],
        request.form.get("bank_account",""),
        float(request.form["amount"]),
        request.form.get("remarks",""),
        id

    ))

    conn.commit()
    conn.close()

    return redirect(url_for("attendance"))

@app.route("/edit-salary/<int:id>")
def edit_salary(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM salary_payments WHERE id=%s",
        (id,)
    )

    salary = cur.fetchone()

    conn.close()

    return render_template(
        "edit_salary.html",
        salary=salary
    )

@app.route("/delete-salary/<int:id>")
def delete_salary(id):

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM salary_payments WHERE id=%s",
        (id,)
    )

    conn.commit()
    conn.close()

    return redirect(url_for("attendance"))

@app.route("/export-attendance-excel")
def export_attendance_excel():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    wb = Workbook()

    # ==========================
    # SHEET 1 : ATTENDANCE SUMMARY
    # ==========================

    ws1 = wb.active
    ws1.title = "Attendance Summary"

    ws1.append([
        "Employee",
        "Present",
        "Absent",
        "Leave",
        "Salary Paid"
    ])

    for cell in ws1[1]:
        cell.font = Font(bold=True)

    cur.execute("""
        SELECT *
        FROM staff_master
        ORDER BY staff_name
    """)
    employees = cur.fetchall()

    for emp in employees:

        staff_name = emp["staff_name"]

        cur.execute("""
            SELECT attendance_status
            FROM attendance
            WHERE staff_name=%s
        """, (staff_name,))

        records = cur.fetchall()

        present = 0
        absent = 0
        leave = 0

        for r in records:

            if r["attendance_status"] == "Present":
                present += 1

            elif r["attendance_status"] == "Absent":
                absent += 1

            elif r["attendance_status"] == "Leave":
                leave += 1

        cur.execute("""
            SELECT SUM(amount) total_salary
            FROM salary_payments
            WHERE employee_name=%s
        """, (staff_name,))

        sal = cur.fetchone()

        salary_paid = (
            sal["total_salary"]
            if sal and sal["total_salary"]
            else 0
        )

        ws1.append([
            staff_name,
            present,
            absent,
            leave,
            salary_paid
        ])

    # ==========================
    # SHEET 2 : ATTENDANCE DETAILS
    # ==========================

    ws2 = wb.create_sheet("Attendance Details")

    ws2.append([
        "Date",
        "Employee",
        "Status"
    ])

    for cell in ws2[1]:
        cell.font = Font(bold=True)

    cur.execute("""
        SELECT *
        FROM attendance
        ORDER BY date DESC
    """)

    attendance_rows = cur.fetchall()

    for row in attendance_rows:

        ws2.append([
            row["date"],
            row["staff_name"],
            row["attendance_status"]
        ])

    # ==========================
    # SHEET 3 : SALARY HISTORY
    # ==========================

    ws3 = wb.create_sheet("Salary Payments")

    ws3.append([
        "Date",
        "Month",
        "Employee",
        "Mode",
        "Bank Account",
        "Amount",
        "Remarks"
    ])

    for cell in ws3[1]:
        cell.font = Font(bold=True)

    cur.execute("""
        SELECT *
        FROM salary_payments
        ORDER BY payment_date DESC
    """)

    salaries = cur.fetchall()

    for s in salaries:

        ws3.append([
            s["payment_date"],
            s["month_name"],
            s["employee_name"],
            s["payment_mode"],
            s["bank_account"],
            s["amount"],
            s["remarks"]
        ])

    # ==========================
    # EMPLOYEE-WISE SHEETS
    # ==========================

    for emp in employees:

        staff_name = emp["staff_name"]

        sheet_name = staff_name[:31]

        ws = wb.create_sheet(sheet_name)

        ws.append([
            "Date",
            "Attendance Status"
        ])

        for cell in ws[1]:
            cell.font = Font(bold=True)

        cur.execute("""
            SELECT *
            FROM attendance
            WHERE staff_name=%s
            ORDER BY date
        """, (staff_name,))

        emp_attendance = cur.fetchall()

        present = 0
        absent = 0
        leave = 0

        for a in emp_attendance:

            ws.append([
                a["date"],
                a["attendance_status"]
            ])

            if a["attendance_status"] == "Present":
                present += 1

            elif a["attendance_status"] == "Absent":
                absent += 1

            elif a["attendance_status"] == "Leave":
                leave += 1

        cur.execute("""
            SELECT SUM(amount) total_salary
            FROM salary_payments
            WHERE employee_name=%s
        """, (staff_name,))

        sal = cur.fetchone()

        salary_paid = (
            sal["total_salary"]
            if sal and sal["total_salary"]
            else 0
        )

        ws.append([])
        ws.append(["Present", present])
        ws.append(["Absent", absent])
        ws.append(["Leave", leave])
        ws.append(["Salary Paid", salary_paid])

    conn.close()

    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".xlsx"
    )

    wb.save(temp_file.name)

    return send_file(
        temp_file.name,
        as_attachment=True,
        download_name="Attendance_Report.xlsx"
    )

# =========================================
# SAVE STAFF
# =========================================

@app.route("/save-staff", methods=["POST"])
def save_staff():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn, cur = get_pg_cursor()

    staff_name = request.form.get(
        "staff_name",
        ""
    ).strip()

    if not staff_name:

        conn.close()

        return redirect(
            url_for("attendance")
        )

    cur.execute("""
        SELECT id
        FROM staff_master
        WHERE LOWER(staff_name)=LOWER(%s)
    """, (staff_name,))

    existing = cur.fetchone()

    if not existing:

        cur.execute("""

            INSERT INTO staff_master (

                emp_id,
                staff_name,
                role,
                department,
                joined_date,
                bank_account,
                shift,
                status

            )

            VALUES (

                %s,%s,%s,%s,%s,%s,%s,%s

            )

        """, (

            request.form.get("emp_id", ""),

            staff_name,

            request.form.get("role", ""),

            request.form.get("department", ""),

            request.form.get("joined_date", None),

            request.form.get("bank_account", ""),

            request.form.get("shift", ""),

            request.form.get("status", "Active")

        ))

    conn.commit()

    conn.close()

    return redirect(
        url_for("attendance")
    )

# =========================================
# SAVE / UPDATE ATTENDANCE
# =========================================




@app.route("/save-attendance", methods=["POST"])
def save_attendance():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    attendance_date = request.form.get(
        "date",
        datetime.now().strftime("%Y-%m-%d")
    )

    staff_name = request.form.get(
        "staff_name",
        ""
    ).strip()

    attendance_status = request.form.get(
        "attendance_status",
        ""
    )

    if not staff_name or not attendance_status:
        return redirect(
            url_for("attendance", date=attendance_date)
        )

    selected = datetime.strptime(
        attendance_date,
        "%Y-%m-%d"
    )

    if selected.date() > datetime.now().date():

        flash("Future attendance not allowed", "error")

        return redirect(
            url_for("attendance", date=attendance_date)
        )

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM attendance
        WHERE date=%s AND staff_name=%s
        LIMIT 1
    """, (
        attendance_date,
        staff_name
    ))

    existing = cur.fetchone()

    if existing:

        cur.execute("""
            UPDATE attendance
            SET attendance_status=%s
            WHERE id=%s
        """, (
            attendance_status,
            existing["id"]
        ))

    else:

        cur.execute("""
            INSERT INTO attendance (
                date,
                staff_name,
                attendance_status
            )
            VALUES (%s, %s, %s)
        """, (
            attendance_date,
            staff_name,
            attendance_status
        ))

    conn.commit()
    conn.close()

    return redirect(
        url_for("attendance", date=attendance_date)
    )

@app.route("/delete-lube/<int:id>")
def delete_lube(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM lube_stock WHERE id = %s", (id,))
    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))

@app.route("/full-system-export")
def full_system_export():

    if not session.get("logged_in") or session.get("role") != "admin":
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    wb = Workbook()

    header_fill = PatternFill("solid", fgColor="07120C")
    sub_fill = PatternFill("solid", fgColor="16A34A")
    light_fill = PatternFill("solid", fgColor="F1F5F9")
    white_font = Font(color="FFFFFF", bold=True)
    title_font = Font(size=18, bold=True, color="07120C")
    header_font = Font(bold=True, color="FFFFFF")
    border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1")
    )

    def style_sheet(ws):
        for row in ws.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="center")
                cell.border = border

        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)

            for cell in col:
                try:
                    max_len = max(max_len, len(str(cell.value or "")))
                except:
                    pass

            ws.column_dimensions[col_letter].width = min(max_len + 4, 32)

    def export_table(sheet_name, table_name):
        cur.execute(f"SELECT * FROM {table_name}")
        rows = cur.fetchall()

        ws = wb.create_sheet(sheet_name)

        if not rows:
            ws.append(["No Data"])
            return ws

        headers = rows[0].keys()
        ws.append(list(headers))

        for r in rows:
            ws.append([r[h] for h in headers])

        style_sheet(ws)
        ws.freeze_panes = "A2"

        return ws

    # =========================
    # DASHBOARD SHEET
    # =========================

    ws = wb.active
    ws.title = "Dashboard"

    ws.merge_cells("A1:H1")
    ws["A1"] = "SAI FUEL MART - FULL SYSTEM EXPORT"
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(horizontal="center")
    ws["A1"].fill = light_fill

    cur.execute("""
        SELECT
            COALESCE(SUM(total_fuel_sale),0) AS fuel_sale,
            COALESCE(SUM(lube_sale),0) AS lube_sale,
            COALESCE(SUM(digital_collection),0) AS digital,
            COALESCE(SUM(credit_given),0) AS credit,
            COALESCE(SUM(total_expense),0) AS expense,
            COALESCE(SUM(cash_in_hand),0) AS cash
        FROM daily_closing
    """)
    d = cur.fetchone()

    cur.execute("SELECT COUNT(*) AS total FROM credit_transporters")
    total_transporters = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS total FROM staff_master")
    total_staff = cur.fetchone()["total"]

    cur.execute("SELECT COALESCE(SUM(balance_due),0) AS total FROM credit_transporters")
    total_due = cur.fetchone()["total"]

    dashboard_data = [
        ["Metric", "Value"],
        ["Total Fuel Sale", d["fuel_sale"]],
        ["Total Lube Sale", d["lube_sale"]],
        ["Digital Collection", d["digital"]],
        ["Total Credit Given", d["credit"]],
        ["Total Expense", d["expense"]],
        ["Cash In Hand", d["cash"]],
        ["Total Transporters", total_transporters],
        ["Total Staff", total_staff],
        ["Outstanding Credit", total_due],
        ["Generated On", datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    ]

    start_row = 3
    for r in dashboard_data:
        ws.append(r)

    for cell in ws[start_row]:
        cell.fill = header_fill
        cell.font = white_font

    for row in ws.iter_rows(min_row=start_row):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="center")

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 22

    # =========================
    # MONTHLY TREND
    # =========================

    cur.execute("""
    SELECT
        TO_CHAR(date, 'YYYY-MM') AS month,
        ROUND(SUM(total_fuel_sale),2) AS fuel,
        ROUND(SUM(lube_sale),2) AS lube,
        ROUND(SUM(credit_given),2) AS credit
    FROM daily_closing
    GROUP BY TO_CHAR(date, 'YYYY-MM')
    ORDER BY TO_CHAR(date, 'YYYY-MM')
""")
    monthly = cur.fetchall()

    ws["D3"] = "Monthly Trend"
    ws["D3"].font = Font(bold=True, size=14)

    ws.append([])
    ws["D5"] = "Month"
    ws["E5"] = "Fuel Sale"
    ws["F5"] = "Lube Sale"
    ws["G5"] = "Credit"

    for c in ["D5", "E5", "F5", "G5"]:
        ws[c].fill = sub_fill
        ws[c].font = white_font

    row_no = 6
    for m in monthly:
        ws[f"D{row_no}"] = m["month"]
        ws[f"E{row_no}"] = m["fuel"]
        ws[f"F{row_no}"] = m["lube"]
        ws[f"G{row_no}"] = m["credit"]
        row_no += 1

    if row_no > 6:
        chart = LineChart()
        chart.title = "Monthly Fuel / Lube / Credit Trend"
        chart.y_axis.title = "Amount"
        chart.x_axis.title = "Month"

        data_ref = Reference(ws, min_col=5, max_col=7, min_row=5, max_row=row_no-1)
        cats = Reference(ws, min_col=4, min_row=6, max_row=row_no-1)

        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats)
        chart.height = 8
        chart.width = 18

        ws.add_chart(chart, "D14")

    # =========================
    # SHEETS FROM TABLES
    # =========================

    tables = [
        ("Daily Closing", "daily_closing"),
        ("Nozzle Entries", "nozzle_entries"),
        ("Nozzle Master", "nozzle_master"),
        ("Tank Level", "tank_level"),
        ("Credit Transporters", "credit_transporters"),
        ("Transport Entries", "transport_entries"),
        ("Transport Ledger", "transporter_ledger"),
        ("Attendance", "attendance"),
        ("Staff Master", "staff_master"),
        ("Salary Payments", "salary_payments"),
        ("Lube Stock", "lube_stock"),
        ("Lube Transactions", "lube_transactions"),
        ("Settings", "settings")
    ]

    for sheet_name, table_name in tables:
        try:
            export_table(sheet_name, table_name)
        except Exception:
            ws_err = wb.create_sheet(sheet_name)
            ws_err.append(["Table not found or no access", table_name])

    # =========================
    # ATTENDANCE SUMMARY SHEET
    # =========================

    ws_att = wb.create_sheet("Attendance Summary")
    ws_att.append(["Employee", "Present", "Absent", "Leave", "Salary Paid"])

    cur.execute("SELECT * FROM staff_master ORDER BY staff_name")
    staff_rows = cur.fetchall()

    for staff in staff_rows:
        name = staff["staff_name"]

        cur.execute("""
            SELECT attendance_status
            FROM attendance
            WHERE staff_name=%s
        """, (name,))
        records = cur.fetchall()

        present = sum(1 for r in records if r["attendance_status"] == "Present")
        absent = sum(1 for r in records if r["attendance_status"] == "Absent")
        leave = sum(1 for r in records if r["attendance_status"] == "Leave")

        cur.execute("""
            SELECT COALESCE(SUM(amount),0) AS total
            FROM salary_payments
            WHERE employee_name=%s
        """, (name,))
        salary = cur.fetchone()["total"]

        ws_att.append([name, present, absent, leave, salary])

    style_sheet(ws_att)

    # =========================
    # TRANSPORT SUMMARY SHEET
    # =========================

    ws_tr = wb.create_sheet("Transport Summary")
    ws_tr.append(["Party", "Fuel Credit", "Lube Credit", "Total Credit", "Received", "Balance"])

    cur.execute("""
        SELECT party_name, fuel_credit, lube_credit,
               credit_given, payment_received, balance_due
        FROM credit_transporters
        ORDER BY balance_due DESC
    """)
    trs = cur.fetchall()

    for t in trs:
        ws_tr.append([
            t["party_name"],
            t["fuel_credit"],
            t["lube_credit"],
            t["credit_given"],
            t["payment_received"],
            t["balance_due"]
        ])

    style_sheet(ws_tr)

    if len(trs) > 0:
        pie = PieChart()
        pie.title = "Outstanding Credit by Transporter"

        labels = Reference(ws_tr, min_col=1, min_row=2, max_row=len(trs)+1)
        data = Reference(ws_tr, min_col=6, min_row=1, max_row=len(trs)+1)

        pie.add_data(data, titles_from_data=True)
        pie.set_categories(labels)
        pie.height = 8
        pie.width = 12

        ws_tr.add_chart(pie, "H2")

    # =========================
    # LUBE SUMMARY SHEET
    # =========================

    ws_lube = wb.create_sheet("Lube Summary")
    ws_lube.append(["Product", "Opening", "Purchase", "Sale", "Closing", "Rate", "Closing Value"])

    cur.execute("""
        SELECT *
        FROM lube_stock
        ORDER BY product_name
    """)
    lube_rows = cur.fetchall()

    for l in lube_rows:
        closing_value = float(l["closing_stock"] or 0) * float(l["selling_rate"] or 0)

        ws_lube.append([
            l["product_name"],
            l["opening_stock"],
            l["purchase_qty"],
            l["sale_qty"],
            l["closing_stock"],
            l["selling_rate"],
            closing_value
        ])

    style_sheet(ws_lube)

    # =========================
    # CHART DASHBOARD EXTRA
    # =========================

    ws_chart = wb.create_sheet("Charts")

    ws_chart.append(["Chart Type", "Description"])
    ws_chart.append(["Line Chart", "Monthly fuel, lube and credit trend is on Dashboard sheet"])
    ws_chart.append(["Pie Chart", "Outstanding credit by transporter is on Transport Summary sheet"])
    style_sheet(ws_chart)

    # =========================
    # FORMAT ALL SHEETS
    # =========================

    for sheet in wb.worksheets:
        sheet.freeze_panes = "A2"

        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="center")

    conn.close()

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(temp_file.name)

    file_name = "Sai_Fuel_Mart_Full_System_Export.xlsx"

    return send_file(
        temp_file.name,
        as_attachment=True,
        download_name=file_name
    )

@app.route("/analytics")
def analytics():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_pg_conn()
    cur = conn.cursor()

    current_month = request.args.get(
    "month",
    datetime.now().strftime("%Y-%m")
)

    # =========================
    # TODAY SALES
    # =========================

    cur.execute("""
SELECT
COALESCE(SUM(total_fuel_sale),0) fuel_sale,
COALESCE(SUM(lube_sale),0) lube_sale,
COALESCE(SUM(cash_in_hand),0) cash_in_hand,
COALESCE(SUM(total_expense),0) expense
FROM daily_closing
WHERE TO_CHAR(date, 'YYYY-MM')=%s
""",(current_month,))

    sales = cur.fetchone()

    fuel_sale = float(sales["fuel_sale"] or 0)
    lube_sale = float(sales["lube_sale"] or 0)
    cash_in_hand = float(sales["cash_in_hand"] or 0)
    total_expense = float(sales["expense"] or 0)

    today_sale = fuel_sale + lube_sale

    net_profit = today_sale - total_expense

    # =========================
    # CREDIT TRANSPORT
    # =========================

    cur.execute("""
        SELECT
            COUNT(*) total_transporters,
            COALESCE(SUM(credit_given),0) total_credit,
            COALESCE(SUM(payment_received),0) total_received,
            COALESCE(SUM(balance_due),0) outstanding
        FROM credit_transporters
    """)

    transport = cur.fetchone()

    # =========================
    # STAFF
    # =========================

    cur.execute("""
        SELECT COUNT(*) total_staff
        FROM staff_master
        WHERE status='Active'
    """)

    total_staff = cur.fetchone()["total_staff"]

    cur.execute("""
        SELECT attendance_status
        FROM attendance
        WHERE TO_CHAR(date, 'YYYY-MM')=%s
    """, (current_month,))

    attendance_rows = cur.fetchall()

    present_today = 0
    absent_today = 0
    leave_today = 0

    for row in attendance_rows:

        if row["attendance_status"] == "Present":
            present_today += 1

        elif row["attendance_status"] == "Absent":
            absent_today += 1

        elif row["attendance_status"] == "Leave":
            leave_today += 1

    # =========================
    # SALARY
    # =========================

    cur.execute("""
        SELECT
        COALESCE(SUM(amount),0) salary_paid
        FROM salary_payments
        WHERE TO_CHAR(payment_date, 'YYYY-MM')=%s
    """, (current_month,))

    salary_paid = cur.fetchone()["salary_paid"]

    # =========================
    # LUBE ANALYTICS
    # =========================

    cur.execute("""
        SELECT
            product_name,
            closing_stock,
            selling_rate
        FROM lube_stock
    """)

    lube_rows = cur.fetchall()

    total_lube_value = 0

    for item in lube_rows:

        stock = float(item["closing_stock"] or 0)
        rate = float(item["selling_rate"] or 0)

        total_lube_value += stock * rate

    cur.execute("""
        SELECT
        product_name,
        sale_qty
        FROM lube_stock
        ORDER BY sale_qty DESC
        LIMIT 1
    """)

    top_product = cur.fetchone()

    top_product_name = (
        top_product["product_name"]
        if top_product
        else "-"
    )

    # =========================
    # LOW STOCK ALERTS
    # =========================

    cur.execute("""
        SELECT COUNT(*) low_stock
        FROM lube_stock
        WHERE closing_stock < 10
    """)

    low_stock = cur.fetchone()["low_stock"]

    # =========================
    # TOP CREDITORS
    # =========================

    cur.execute("""
        SELECT
            party_name,
            balance_due
        FROM credit_transporters
        ORDER BY balance_due DESC
        LIMIT 10
    """)

    top_creditors = cur.fetchall()

    # =========================
    # MONTHLY TREND
    # =========================

    cur.execute("""
        SELECT
            TO_CHAR(date, 'YYYY-MM') AS month,
            ROUND(SUM(total_fuel_sale),2) fuel_sale,
            ROUND(SUM(lube_sale),2) lube_sale
        FROM daily_closing
        GROUP BY TO_CHAR(date, 'YYYY-MM')
        ORDER BY month
    """)

    monthly_trend = cur.fetchall()

    conn.close()

    import json

    trend_labels = json.dumps(
    [x["month"] for x in monthly_trend]
)

    fuel_data = json.dumps(
    [float(x["fuel_sale"] or 0) for x in monthly_trend]
)

    lube_data = json.dumps(
    [float(x["lube_sale"] or 0) for x in monthly_trend]
)

    if total_staff > 0:
     staff_percent = int((present_today * 100) / total_staff)
    else:
     staff_percent = 0


    return render_template(
        "analytics.html",

        today_sale=today_sale,
        fuel_sale=fuel_sale,
        lube_sale=lube_sale,

        cash_in_hand=cash_in_hand,
        total_expense=total_expense,
        net_profit=net_profit,

        total_transporters=transport["total_transporters"],
        total_credit=transport["total_credit"],
        total_received=transport["total_received"],
        outstanding_credit=transport["outstanding"],

        total_staff=total_staff,
        present_today=present_today,
        absent_today=absent_today,
        leave_today=leave_today,

        salary_paid=salary_paid,

        total_lube_value=total_lube_value,
        top_product_name=top_product_name,

        low_stock=low_stock,

        top_creditors=top_creditors,
        monthly_trend=monthly_trend,
        trend_labels=trend_labels,
        fuel_data=fuel_data,
        lube_data=lube_data,
        staff_percent=staff_percent,
        
    )

def get_supabase_client():

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")

    return create_client(url, key)

def upload_proof_file(file, folder):
    if not file or file.filename == "":
        return ""

    supabase = get_supabase_client()

    ext = file.filename.rsplit(".", 1)[-1].lower()
    file_name = f"{folder}/{uuid.uuid4()}.{ext}"

    file_bytes = file.read()

    supabase.storage.from_("proof-files").upload(
        file_name,
        file_bytes,
        {"content-type": file.content_type}
    )

    return supabase.storage.from_("proof-files").get_public_url(file_name)


@app.route("/proof-upload")
def proof_upload():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    return render_template(
        "proof_upload.html",
        today=datetime.now().strftime("%Y-%m-%d")
    )




@app.route("/proof-register")
def proof_register():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    category = request.args.get("category", "")
    fuel_type = request.args.get("fuel_type", "")

    conn = get_pg_conn()
    cur = conn.cursor()

    query = """
        SELECT *
        FROM proof_register
        WHERE 1=1
    """

    params = []

    if from_date:
        query += " AND proof_date >= %s"
        params.append(from_date)

    if to_date:
        query += " AND proof_date <= %s"
        params.append(to_date)

    if category:
        query += " AND proof_category = %s"
        params.append(category)

    if fuel_type:
        query += " AND fuel_type = %s"
        params.append(fuel_type)

    query += """
        ORDER BY proof_date DESC, proof_time DESC, id DESC
    """

    cur.execute(query, params)
    rows = cur.fetchall()

    conn.close()

    return render_template(
        "proof_register.html",
        rows=rows
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


## init_db()

@app.route("/download-db")
def download_db():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    return send_file(
        "sai_fuel_mart.db",
        as_attachment=True,
        download_name="SaiFuelMart_Backup.db"
    )

def get_pg_conn():

    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
        sslmode="require"
    )

def get_pg_cursor():

    conn = get_pg_conn()

    cur = conn.cursor()

    return conn, cur

@app.route("/delete-proof-register", methods=["POST"])
def delete_proof_register():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    proof_ids = [int(x) for x in request.form.getlist("proof_ids")]

    if not proof_ids:
        return redirect(url_for("proof_register"))

    conn = get_pg_conn()
    cur = conn.cursor()

    # get photo urls first
    cur.execute("""
        SELECT photo_url
        FROM proof_register
        WHERE id = ANY(%s)
    """, (proof_ids,))

    rows = cur.fetchall()

    supabase = get_supabase_client()

    for row in rows:
        photo_url = row["photo_url"]

        if photo_url:
            try:
                file_path = photo_url.split("/proof-files/")[-1]

                supabase.storage.from_("proof-files").remove([
                    file_path
                ])

            except Exception:
                pass

    # delete database rows
    cur.execute("""
        DELETE FROM proof_register
        WHERE id = ANY(%s)
    """, (proof_ids,))

    conn.commit()
    conn.close()

    return redirect(url_for("proof_register"))

@app.route("/save-proof-upload", methods=["POST"])
def save_proof_upload():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    proof_date = request.form.get("proof_date")
    proof_time = datetime.now().strftime("%H:%M:%S")
    proof_type = request.form.get("proof_type")

    latitude = request.form.get("latitude", "")
    longitude = request.form.get("longitude", "")
    client_time = request.form.get("client_time", "")

    location_url = ""
    if latitude and longitude:
        location_url = f"https://www.google.com/maps?q={latitude},{longitude}"

    conn = get_pg_conn()
    cur = conn.cursor()

    def insert_proof(category, fuel, item, status, photo_url="", remarks=""):
        cur.execute("""
            INSERT INTO proof_register (
                proof_date, proof_time, proof_category,
                fuel_type, item_name, stock_status,
                photo_url, video_url, remarks,
                latitude, longitude, location_url, client_time
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            proof_date,
            proof_time,
            category,
            fuel,
            item,
            status,
            photo_url,
            "",
            remarks,
            latitude,
            longitude,
            location_url,
            client_time
        ))

    try:

        if proof_type == "Nozzle Testing":

            for fuel in ["MS", "HSD"]:

                fuel_status = request.form.get(f"{fuel.lower()}_stock_status")
                nozzles = ["MS1", "MS2", "MS3"] if fuel == "MS" else ["HSD1", "HSD2", "HSD3"]

                if fuel_status == "No Stock":

                    for nozzle in nozzles:
                        insert_proof(
                            "Nozzle Testing",
                            fuel,
                            nozzle,
                            "No Stock",
                            "",
                            f"{fuel} no stock"
                        )

                else:

                    for nozzle in nozzles:
                        photo = request.files.get(f"{nozzle}_photo")

                        if not photo:
                            conn.close()
                            return f"{nozzle} photo required"

                        photo_url = upload_proof_file(photo, "photos")

                        insert_proof(
                            "Nozzle Testing",
                            fuel,
                            nozzle,
                            "Available",
                            photo_url,
                            ""
                        )

        elif proof_type == "Dip Check":

            for fuel in ["MS", "HSD"]:

                dip_status = request.form.get(f"{fuel.lower()}_dip_status")
                sessions = ["Morning Dip", "Evening Dip"]

                if dip_status == "No Stock":

                    for session_name in sessions:
                        insert_proof(
                            "Dip Check",
                            fuel,
                            f"{fuel} {session_name}",
                            "No Stock",
                            "",
                            f"{fuel} no stock"
                        )

                else:

                    for session_name in sessions:
                        field = f"{fuel}_{session_name.replace(' ', '_')}"
                        photo = request.files.get(f"{field}_photo")

                        if not photo:
                            conn.close()
                            return f"{fuel} {session_name} photo required"

                        photo_url = upload_proof_file(photo, "photos")

                        insert_proof(
                            "Dip Check",
                            fuel,
                            f"{fuel} {session_name}",
                            "Available",
                            photo_url,
                            ""
                        )

        else:
            conn.close()
            return "Please select proof type"

        conn.commit()
        conn.close()

        return redirect(url_for("proof_register"))

    except Exception as e:
        conn.rollback()
        conn.close()
        return f"Proof upload error: {str(e)}"

@app.route("/api/transporters")
def api_transporters():

    conn = get_pg_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM credit_transporters
        ORDER BY party_name ASC
    """)

    rows = cur.fetchall()

    conn.close()

    return jsonify(rows)

@app.route("/api/transport-entry-report")
def api_transport_entry_report():

    party = request.args.get("party", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    conn = get_pg_conn()
    cur = conn.cursor()

    query = """
        SELECT *
        FROM transport_entries
        WHERE 1=1
    """

    params = []

    if party:
        query += " AND transporter_name=%s"
        params.append(party)

    if from_date:
        query += " AND entry_date >= %s"
        params.append(from_date)

    if to_date:
        query += " AND entry_date <= %s"
        params.append(to_date)

    query += " ORDER BY entry_date DESC, id DESC"

    cur.execute(query, tuple(params))

    rows = cur.fetchall()

    conn.close()

    return jsonify(rows)

@app.route("/api/save-transport-entry", methods=["POST"])
def api_save_transport_entry():

    data = request.get_json()

    conn = get_pg_conn()
    cur = conn.cursor()

    qty = float(data.get("qty") or 0)
    rate = float(data.get("rate") or 0)
    cash_taken = float(data.get("cash_taken") or 0)

    fuel_amount = qty * rate
    total_amount = fuel_amount + cash_taken

    cur.execute("""
        INSERT INTO transport_entries (
            entry_date,
            sl_no,
            transporter_id,
            transporter_name,
            challan_no,
            vehicle_no,
            slip_no,
            qty,
            rate,
            fuel_amount,
            cash_taken,
            total_amount
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        data.get("entry_date"),
        data.get("sl_no"),
        data.get("transporter_id"),
        data.get("transporter_name"),
        data.get("challan_no"),
        data.get("vehicle_no"),
        data.get("slip_no"),
        qty,
        rate,
        fuel_amount,
        cash_taken,
        total_amount
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "status": "success",
        "message": "Transport entry saved"
    })


if __name__ == "__main__":
    app.run(debug=True)