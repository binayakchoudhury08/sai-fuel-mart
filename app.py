from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from datetime import timedelta, datetime
import sqlite3
import os
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
        CREATE TABLE IF NOT EXISTS staff_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_name TEXT,
            role TEXT,
            status TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            staff_name TEXT,
            attendance_status TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS lube_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT,
            selling_rate REAL,
            opening_stock REAL,
            purchase_qty REAL,
            sale_qty REAL DEFAULT 0,
            closing_stock REAL
        )
    """)

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transport_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            transporter_id INTEGER,
            payment_amount REAL
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_closing ORDER BY id DESC LIMIT 1")
    latest = cur.fetchone()

    monthly = cur.execute("""

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

    WHERE strftime('%Y-%m', date)=strftime('%Y-%m','now')

""").fetchone()

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

@app.route("/daily-closing")
def daily_closing():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    selected_date = request.args.get(
        "date",
        datetime.now().strftime("%Y-%m-%d")
    )

    conn = get_conn()
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

            ne.opening_reading,
            ne.closing_reading,
            ne.testing_qty,
            ne.total_sale

        FROM nozzle_master nm

        LEFT JOIN nozzle_entries ne

        ON nm.id = ne.nozzle_id
        AND ne.entry_date = ?

        ORDER BY
        nm.fuel_type,
        nm.nozzle_name

    """, (selected_date,))

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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM daily_closing
        WHERE substr(date, 1, 10) = ?
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
        WHERE substr(nozzle_entries.entry_date, 1, 10) = ?
        ORDER BY nozzle_master.fuel_type, nozzle_master.nozzle_name
    """, (date,))
    nozzle_rows = cur.fetchall()

    cur.execute("""
        SELECT *
        FROM tank_level
        WHERE substr(date, 1, 10) = ?
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

@app.route("/save-daily-closing", methods=["POST"])
def save_daily_closing():
    if not session.get("logged_in"):
        return jsonify({"status": "error", "message": "Not logged in"})

    data = request.get_json()
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id FROM daily_closing WHERE date = ?", (data["date"],))
    if cur.fetchone():
        conn.close()
        return jsonify({
            "status": "error",
            "message": "This date data already exists. Please edit from Reports."
        })

    cur.execute("""
        INSERT INTO daily_closing (
            date, ms_litres, hsd_litres, cng_sale, total_fuel_sale, lube_sale,
            digital_collection, phonepe, card_swipe, hp_pay, hpcl_otp,
            upi_other, credit_given, transport_received, net_credit_due,
            total_expense, cash_in_hand
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["date"],
        data["ms_litres"],
        data["hsd_litres"],
        data.get("cng_sale", 0),
        data["total_fuel_sale"],
        data["lube_sale"],
        data["digital_collection"],
        data.get("phonepe", 0),
        data.get("card_swipe", 0),
        data.get("hp_pay", 0),
        data.get("hpcl_otp", 0),
        data.get("upi_other", 0),
        data["credit_given"],
        data["transport_received"],
        data["net_credit_due"],
        data["total_expense"],
        data["cash_in_hand"]
    ))

    for item in data.get("lube_sales", []):
        qty = float(item["qty"])
        cur.execute("""
            UPDATE lube_stock
            SET sale_qty = sale_qty + ?,
                closing_stock = closing_stock - ?
            WHERE id = ?
        """, (qty, qty, item["product_id"]))

    for item in data.get("credit_transport_sales", []):
        amount = float(item["amount"])
        cur.execute("""
            UPDATE credit_transporters
            SET credit_given = credit_given + ?,
                balance_due = balance_due + ?
            WHERE id = ?
        """, (amount, amount, item["transporter_id"]))

    conn.commit()
    conn.close()

    return jsonify({"status": "success", "message": "Daily closing saved successfully"})


@app.route("/get-daily-closing/<date>")
def get_daily_closing(date):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_closing WHERE date = ? ORDER BY id DESC LIMIT 1", (date,))
    row = cur.fetchone()

    cur.execute("""
        SELECT COALESCE(SUM(payment_amount), 0) AS total_received
        FROM transport_payments
        WHERE date <= ?
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

@app.route("/edit-daily-closing/<int:id>")
def edit_daily_closing(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM daily_closing
        WHERE id=?
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""

        UPDATE daily_closing

        SET

            date=?,

            ms_litres=?,
            hsd_litres=?,
            cng_sale=?,

            total_fuel_sale=?,

            lube_sale=?,

            digital_collection=?,

            total_expense=?,

            cash_in_hand=?

        WHERE id=?

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


# =========================================
# DELETE DAILY CLOSING
# =========================================

@app.route("/delete-daily-closing/<int:id>")
def delete_daily_closing(id):

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM daily_closing
        WHERE id=?
    """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("reports"))

# ======================================================
# NOZZLE MANAGEMENT ROUTES - NO SHIFT VERSION
# ======================================================

@app.route("/nozzle-management")
def nozzle_management():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO nozzle_master (
            nozzle_name,
            machine_no,
            fuel_type,
            status
        )
        VALUES (?, ?, ?, ?)
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM nozzle_master
        WHERE id = ?
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE nozzle_master
        SET nozzle_name = ?,
            machine_no = ?,
            fuel_type = ?,
            status = ?
        WHERE id = ?
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM nozzle_entries
        WHERE nozzle_id = ?
    """, (id,))

    cur.execute("""
        DELETE FROM nozzle_master
        WHERE id = ?
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

    conn = get_conn()
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
        VALUES (?, ?, ?, ?, ?, ?, ?)
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM nozzle_entries
        WHERE id = ?
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE nozzle_entries
        SET entry_date = ?,
            nozzle_id = ?,
            opening_reading = ?,
            closing_reading = ?,
            testing_qty = ?,
            total_sale = ?,
            created_at = ?
        WHERE id = ?
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM nozzle_entries
        WHERE id = ?
    """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("nozzle_management"))


@app.route("/get-nozzle-sales/<date>")
def get_nozzle_sales(date):

    if not session.get("logged_in"):
        return jsonify([])

    conn = get_conn()
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

        WHERE substr(nozzle_entries.entry_date,1,10)=?

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

    conn = get_conn()
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
        WHERE strftime('%Y-%m', date) = strftime('%Y-%m', 'now')
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT ms_litres, hsd_litres
        FROM daily_closing
        WHERE date = ?
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM tank_level WHERE id = ?", (id,))
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

    conn = get_conn()
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
        SET date=?, fuel_type=?, opening_stock=?, received_stock=?,
            own_tanker_stock=?, sale_stock=?, gain_qty=?, shortage_qty=?,
            current_stock=?, gain_amount=?, shortage_amount=?, created_at=?
        WHERE id=?
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM tank_level WHERE id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("tank_level"))


@app.route("/credit-transport")
def credit_transport():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM credit_transporters ORDER BY id DESC")
    transporters = cur.fetchall()

    cur.execute("SELECT * FROM transport_entries ORDER BY entry_date DESC, sl_no DESC")
    entries = cur.fetchall()

    cur.execute("SELECT hsd_rate FROM settings WHERE id = 1")
    settings_data = cur.fetchone()

    conn.close()

    return render_template(
        "credit_transport.html",
        transporters=transporters,
        entries=entries,
        hsd_rate=settings_data["hsd_rate"]
    )


@app.route("/save-transporter", methods=["POST"])
def save_transporter():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    opening_balance = float(request.form["opening_balance"] or 0)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO credit_transporters (
            party_name, opening_balance, credit_given,
            payment_received, balance_due, status
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        request.form["party_name"],
        opening_balance,
        0,
        0,
        opening_balance,
        request.form["status"]
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/save-transport-payment", methods=["POST"])
def save_transport_payment():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    payment_amount = float(request.form["payment_amount"] or 0)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO transport_payments (
            date, transporter_id, payment_amount
        )
        VALUES (?, ?, ?)
    """, (
        request.form["date"],
        request.form["transporter_id"],
        payment_amount
    ))

    cur.execute("""
        UPDATE credit_transporters
        SET payment_received = payment_received + ?,
            balance_due = balance_due - ?
        WHERE id = ?
    """, (
        payment_amount,
        payment_amount,
        request.form["transporter_id"]
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/save-transport-entry", methods=["POST"])
def save_transport_entry():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    entry_date = request.form["entry_date"]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS total FROM transport_entries WHERE entry_date = ?", (entry_date,))
    sl_no = cur.fetchone()["total"] + 1

    hsd_qty = float(request.form["hsd_qty"] or 0)
    rate = float(request.form["rate"] or 0)
    cash_taken = float(request.form["cash_taken"] or 0)

    hsd_amount = round(hsd_qty * rate, 2)
    total_amount = round(hsd_amount + cash_taken, 2)

    cur.execute("""
        INSERT INTO transport_entries (
            entry_date, sl_no, transporter_name, challan_no, vehicle_no, slip_no,
            hsd_qty, rate, hsd_amount, cash_taken, total_amount, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry_date,
        sl_no,
        request.form.get("transporter_name", ""),
        request.form.get("challan_no", ""),
        request.form.get("vehicle_no", ""),
        request.form.get("slip_no", ""),
        hsd_qty,
        rate,
        hsd_amount,
        cash_taken,
        total_amount,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/edit-transport-entry/<int:id>")
def edit_transport_entry(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM transport_entries WHERE id = ?", (id,))
    entry = cur.fetchone()

    cur.execute("SELECT * FROM credit_transporters ORDER BY party_name ASC")
    transporters = cur.fetchall()

    conn.close()

    return render_template(
        "edit_transport_entry.html",
        entry=entry,
        transporters=transporters
    )


@app.route("/update-transport-entry/<int:id>", methods=["POST"])
def update_transport_entry(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    hsd_qty = float(request.form["hsd_qty"] or 0)
    rate = float(request.form["rate"] or 0)
    cash_taken = float(request.form["cash_taken"] or 0)

    hsd_amount = round(hsd_qty * rate, 2)
    total_amount = round(hsd_amount + cash_taken, 2)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE transport_entries
        SET entry_date=?, transporter_name=?, challan_no=?, vehicle_no=?,
            slip_no=?, hsd_qty=?, rate=?, hsd_amount=?, cash_taken=?,
            total_amount=?, created_at=?
        WHERE id=?
    """, (
        request.form["entry_date"],
        request.form["transporter_name"],
        request.form["challan_no"],
        request.form["vehicle_no"],
        request.form["slip_no"],
        hsd_qty,
        rate,
        hsd_amount,
        cash_taken,
        total_amount,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/delete-transport-entry/<int:id>")
def delete_transport_entry(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM transport_entries WHERE id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/edit-transporter/<int:id>")
def edit_transporter(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM credit_transporters WHERE id = ?", (id,))
    transporter = cur.fetchone()

    conn.close()

    return render_template("edit_transporter.html", transporter=transporter)


@app.route("/update-transporter/<int:id>", methods=["POST"])
def update_transporter(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    opening_balance = float(request.form["opening_balance"] or 0)
    credit_given = float(request.form["credit_given"] or 0)
    payment_received = float(request.form["payment_received"] or 0)
    balance_due = opening_balance + credit_given - payment_received

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE credit_transporters
        SET party_name=?, opening_balance=?, credit_given=?,
            payment_received=?, balance_due=?, status=?
        WHERE id=?
    """, (
        request.form["party_name"],
        opening_balance,
        credit_given,
        payment_received,
        balance_due,
        request.form["status"],
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/delete-transporter/<int:id>")
def delete_transporter(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM credit_transporters WHERE id = ?", (id,))
    cur.execute("DELETE FROM transport_payments WHERE transporter_id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/digital-collection")
def digital_collection():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
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
        WHERE strftime('%Y-%m', date) = strftime('%Y-%m', 'now')
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

    conn = get_conn()
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
            WHERE substr(ne.entry_date,1,10) = substr(dc.date,1,10)
            AND nm.nozzle_name = 'MS1'
        ),0) AS ms1,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE substr(ne.entry_date,1,10) = substr(dc.date,1,10)
            AND nm.nozzle_name = 'MS2'
        ),0) AS ms2,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE substr(ne.entry_date,1,10) = substr(dc.date,1,10)
            AND nm.nozzle_name = 'MS3'
        ),0) AS ms3,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE substr(ne.entry_date,1,10) = substr(dc.date,1,10)
            AND nm.nozzle_name = 'HSD1'
        ),0) AS hsd1,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE substr(ne.entry_date,1,10) = substr(dc.date,1,10)
            AND nm.nozzle_name = 'HSD2'
        ),0) AS hsd2,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE substr(ne.entry_date,1,10) = substr(dc.date,1,10)
            AND nm.nozzle_name = 'HSD3'
        ),0) AS hsd3,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE substr(ne.entry_date,1,10) = substr(dc.date,1,10)
            AND nm.nozzle_name = 'CNG1'
        ),0) AS cng1,

        COALESCE((
            SELECT SUM(ne.total_sale)
            FROM nozzle_entries ne
            JOIN nozzle_master nm ON ne.nozzle_id = nm.id
            WHERE substr(ne.entry_date,1,10) = substr(dc.date,1,10)
            AND nm.nozzle_name = 'CNG2'
        ),0) AS cng2

    FROM daily_closing dc

    WHERE 1=1
"""

    daily_params = []

    if from_date:
        daily_query += " AND dc.date >= ?"
        daily_params.append(from_date)

    if to_date:
        daily_query += " AND dc.date <= ?"
        daily_params.append(to_date)

    if search:
        daily_query += " AND dc.date LIKE ?"
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
        nozzle_query += " AND nozzle_entries.entry_date >= ?"
        nozzle_params.append(from_date)

    if to_date:
        nozzle_query += " AND nozzle_entries.entry_date <= ?"
        nozzle_params.append(to_date)

    if fuel_type:
        nozzle_query += " AND nozzle_master.fuel_type = ?"
        nozzle_params.append(fuel_type)

    if search:
        nozzle_query += """
            AND (
                nozzle_master.nozzle_name LIKE ?
                OR nozzle_master.machine_no LIKE ?
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
        tank_query += " AND date >= ?"
        tank_params.append(from_date)

    if to_date:
        tank_query += " AND date <= ?"
        tank_params.append(to_date)

    if fuel_type:
        tank_query += " AND fuel_type = ?"
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
        lube_query += " AND product_name LIKE ?"
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
        transport_query += " AND party_name LIKE ?"
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
        transport_entry_query += " AND entry_date >= ?"
        transport_entry_params.append(from_date)

    if to_date:
        transport_entry_query += " AND entry_date <= ?"
        transport_entry_params.append(to_date)

    if search:
        transport_entry_query += """
            AND (
                transporter_name LIKE ?
                OR challan_no LIKE ?
                OR vehicle_no LIKE ?
                OR slip_no LIKE ?
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

    conn.close()

    return render_template(
        "reports.html",
        daily_rows=daily_rows,
        nozzle_rows=nozzle_rows,
        tank_rows=tank_rows,
        lube_rows=lube_rows,
        transporter_rows=transporter_rows,
        transport_entry_rows=transport_entry_rows
    )


@app.route("/export-full-backup")
def export_full_backup():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_admin():
        return redirect(url_for("dashboard"))

    conn = get_conn()
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

    conn = get_conn()
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

    conn = get_conn()
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

    conn = get_conn()
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

    conn = get_conn()
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
    conn = get_conn()
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

    conn = get_conn()
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

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM daily_closing WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    return redirect(url_for("reports"))


@app.route("/edit-report/<int:id>")
def edit_report(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM daily_closing WHERE id = ?", (id,))
    row = cur.fetchone()
    conn.close()

    return render_template("edit_report.html", row=row)


@app.route("/update-report/<int:id>", methods=["POST"])
def update_report(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE daily_closing
        SET date=?, ms_litres=?, hsd_litres=?, cng_sale=?, total_fuel_sale=?,
            lube_sale=?, digital_collection=?, phonepe=?, card_swipe=?,
            hp_pay=?, hpcl_otp=?, upi_other=?, credit_given=?,
            transport_received=?, net_credit_due=?, total_expense=?, cash_in_hand=?
        WHERE id=?
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

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM settings WHERE id = 1")
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

    conn = get_conn()
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

                ms_rate=?,
                hsd_rate=?,
                cng_rate=?

            WHERE id=?

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

            VALUES (?, ?, ?)

        """, (

            ms_rate,
            hsd_rate,
            cng_rate

        ))

    conn.commit()
    conn.close()

    return redirect(url_for("settings"))

@app.route("/attendance")
def attendance():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM staff_master ORDER BY id DESC")
    staff_list = cur.fetchall()

    cur.execute("SELECT * FROM attendance ORDER BY id DESC LIMIT 20")
    attendance_list = cur.fetchall()

    today = datetime.now().strftime("%Y-%m-%d")

    cur.execute("SELECT COUNT(*) AS total_staff FROM staff_master WHERE status='Active'")
    total_staff = cur.fetchone()["total_staff"]

    cur.execute("""
        SELECT COUNT(*) AS present_today
        FROM attendance
        WHERE date=? AND attendance_status='Present'
    """, (today,))
    present_today = cur.fetchone()["present_today"]

    cur.execute("""
        SELECT COUNT(*) AS absent_leave
        FROM attendance
        WHERE date=? AND attendance_status IN ('Absent','Leave')
    """, (today,))
    absent_leave = cur.fetchone()["absent_leave"]

    conn.close()

    return render_template(
        "attendance.html",
        staff_list=staff_list,
        attendance_list=attendance_list,
        total_staff=total_staff,
        present_today=present_today,
        absent_leave=absent_leave
    )


@app.route("/save-staff", methods=["POST"])
def save_staff():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO staff_master (
            staff_name, role, status
        )
        VALUES (?, ?, ?)
    """, (
        request.form["staff_name"],
        request.form["role"],
        request.form["status"]
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("attendance"))


@app.route("/save-attendance", methods=["POST"])
def save_attendance():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO attendance (
            date, staff_name, attendance_status
        )
        VALUES (?, ?, ?)
    """, (
        request.form["date"],
        request.form["staff_name"],
        request.form["attendance_status"]
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("attendance"))


@app.route("/lube-stock")
def lube_stock():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM lube_stock ORDER BY id DESC")
    lube_items = cur.fetchall()
    conn.close()

    return render_template("lube_stock.html", lube_items=lube_items)


@app.route("/save-lube-product", methods=["POST"])
def save_lube_product():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    product_name = request.form["product_name"]
    selling_rate = float(request.form["selling_rate"])
    opening_stock = float(request.form["opening_stock"])
    purchase_qty = float(request.form["purchase_qty"] or 0)
    closing_stock = opening_stock + purchase_qty

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO lube_stock (
            product_name, selling_rate, opening_stock,
            purchase_qty, sale_qty, closing_stock
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        product_name, selling_rate, opening_stock,
        purchase_qty, 0, closing_stock
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))


@app.route("/edit-lube/<int:id>")
def edit_lube(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM lube_stock WHERE id = ?", (id,))
    item = cur.fetchone()
    conn.close()

    return render_template("edit_lube.html", item=item)


@app.route("/update-lube/<int:id>", methods=["POST"])
def update_lube(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    opening_stock = float(request.form["opening_stock"])
    purchase_qty = float(request.form["purchase_qty"])
    sale_qty = float(request.form["sale_qty"])
    closing_stock = opening_stock + purchase_qty - sale_qty

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE lube_stock
        SET product_name=?, selling_rate=?, opening_stock=?,
            purchase_qty=?, sale_qty=?, closing_stock=?
        WHERE id=?
    """, (
        request.form["product_name"],
        float(request.form["selling_rate"]),
        opening_stock,
        purchase_qty,
        sale_qty,
        closing_stock,
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))


@app.route("/delete-lube/<int:id>")
def delete_lube(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM lube_stock WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


init_db()

if __name__ == "__main__":
    app.run(debug=True)