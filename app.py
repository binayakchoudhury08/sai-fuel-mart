from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from datetime import timedelta, datetime
import sqlite3
from openpyxl import Workbook
from io import BytesIO

app = Flask(__name__)

app.secret_key = "sai-fuel-mart-secret-key"
app.permanent_session_lifetime = timedelta(days=30)

USERNAME = "admin"
PASSWORD = "1234"


def init_db():
    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            ms_rate REAL,
            hsd_rate REAL
        )
    """)

    cur.execute("""
        INSERT OR IGNORE INTO settings (id, ms_rate, hsd_rate)
        VALUES (1, 102.46, 93.72)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_closing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            ms_litres REAL,
            hsd_litres REAL,
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

    daily_columns = [
        ("phonepe", "REAL DEFAULT 0"),
        ("card_swipe", "REAL DEFAULT 0"),
        ("hp_pay", "REAL DEFAULT 0"),
        ("hpcl_otp", "REAL DEFAULT 0"),
        ("upi_other", "REAL DEFAULT 0")
    ]

    for column_name, column_type in daily_columns:
        try:
            cur.execute(f"ALTER TABLE daily_closing ADD COLUMN {column_name} {column_type}")
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

    try:
        cur.execute("ALTER TABLE tank_level ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
    except sqlite3.OperationalError:
        pass

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
        remember = request.form.get("remember")

        if username == USERNAME and password == PASSWORD:
            session["logged_in"] = True
            session["username"] = username

            if remember:
                session.permanent = True

            return redirect(url_for("dashboard"))

        error = "Invalid username or password"

    return render_template("login.html", error=error)


@app.route("/dashboard")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_closing ORDER BY id DESC LIMIT 1")
    latest = cur.fetchone()

    cur.execute("""
        SELECT
            SUM(total_fuel_sale) AS monthly_sale,
            SUM(total_expense) AS monthly_expense,
            SUM(net_credit_due) AS monthly_credit,
            SUM(cash_in_hand) AS monthly_cash
        FROM daily_closing
        WHERE strftime('%Y-%m', date) = strftime('%Y-%m', 'now')
    """)
    monthly = cur.fetchone()

    cur.execute("""
        SELECT date, total_fuel_sale, cash_in_hand, net_credit_due
        FROM daily_closing
        ORDER BY date ASC
        LIMIT 15
    """)
    chart_rows = cur.fetchall()

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

    cur.execute("""
        SELECT *
        FROM tank_level
        WHERE fuel_type = 'MS'
        ORDER BY id DESC
        LIMIT 1
    """)
    ms_tank = cur.fetchone()

    cur.execute("""
        SELECT *
        FROM tank_level
        WHERE fuel_type = 'HSD'
        ORDER BY id DESC
        LIMIT 1
    """)
    hsd_tank = cur.fetchone()

    conn.close()

    return render_template(
        "dashboard.html",
        latest=latest,
        monthly=monthly,
        chart_rows=chart_rows,
        transporter_summary=transporter_summary,
        low_lube=low_lube,
        ms_tank=ms_tank,
        hsd_tank=hsd_tank
    )


@app.route("/daily-closing")
def daily_closing():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM settings WHERE id = 1")
    settings_data = cur.fetchone()

    cur.execute("SELECT * FROM lube_stock WHERE closing_stock > 0 ORDER BY product_name ASC")
    lube_items = cur.fetchall()

    cur.execute("SELECT * FROM credit_transporters WHERE status='Active' ORDER BY party_name ASC")
    transporters = cur.fetchall()

    conn.close()

    return render_template(
        "daily_closing.html",
        settings=settings_data,
        lube_items=lube_items,
        transporters=transporters
    )


@app.route("/save-daily-closing", methods=["POST"])
def save_daily_closing():
    if not session.get("logged_in"):
        return jsonify({"status": "error", "message": "Not logged in"})

    data = request.get_json()

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("SELECT id FROM daily_closing WHERE date = ?", (data["date"],))
    existing = cur.fetchone()

    if existing:
        conn.close()
        return jsonify({
            "status": "error",
            "message": "This date data already exists. Please edit from Reports."
        })

    cur.execute("""
        INSERT INTO daily_closing (
            date,
            ms_litres,
            hsd_litres,
            total_fuel_sale,
            lube_sale,
            digital_collection,
            phonepe,
            card_swipe,
            hp_pay,
            hpcl_otp,
            upi_other,
            credit_given,
            transport_received,
            net_credit_due,
            total_expense,
            cash_in_hand
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["date"],
        data["ms_litres"],
        data["hsd_litres"],
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
        product_id = item["product_id"]
        qty = float(item["qty"])

        cur.execute("""
            UPDATE lube_stock
            SET sale_qty = sale_qty + ?,
                closing_stock = closing_stock - ?
            WHERE id = ?
        """, (qty, qty, product_id))

    for item in data.get("credit_transport_sales", []):
        transporter_id = item["transporter_id"]
        amount = float(item["amount"])

        cur.execute("""
            UPDATE credit_transporters
            SET credit_given = credit_given + ?,
                balance_due = balance_due + ?
            WHERE id = ?
        """, (amount, amount, transporter_id))

    conn.commit()
    conn.close()

    return jsonify({
        "status": "success",
        "message": "Daily closing saved successfully"
    })


@app.route("/get-daily-closing/<date>")
def get_daily_closing(date):
    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM daily_closing
        WHERE date = ?
        ORDER BY id DESC
        LIMIT 1
    """, (date,))
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


@app.route("/tank-level")
def tank_level():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM tank_level
        ORDER BY date DESC, id DESC
    """)
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

    cur.execute("""
        SELECT *
        FROM tank_level
        WHERE fuel_type = 'MS'
        ORDER BY id DESC
        LIMIT 1
    """)
    ms_latest = cur.fetchone()

    cur.execute("""
        SELECT *
        FROM tank_level
        WHERE fuel_type = 'HSD'
        ORDER BY id DESC
        LIMIT 1
    """)
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

    data = request.form

    fuel_type = data["fuel_type"]
    opening_stock = float(data["opening_stock"] or 0)
    received_stock = float(data["received_stock"] or 0)
    own_tanker_stock = float(data["own_tanker_stock"] or 0)
    actual_dip = float(data["dip_stock"] or 0)

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT ms_litres, hsd_litres
        FROM daily_closing
        WHERE date = ?
        ORDER BY id DESC
        LIMIT 1
    """, (data["date"],))
    closing_data = cur.fetchone()

    sale_stock = 0

    if closing_data:
        if fuel_type == "MS":
            sale_stock = float(closing_data["ms_litres"] or 0)
        else:
            sale_stock = float(closing_data["hsd_litres"] or 0)

    theoretical_stock = opening_stock + received_stock + own_tanker_stock - sale_stock
    difference = round(actual_dip - theoretical_stock, 2)

    cur.execute("SELECT ms_rate, hsd_rate FROM settings WHERE id = 1")
    settings_data = cur.fetchone()

    if fuel_type == "MS":
        rate = float(settings_data["ms_rate"])
    else:
        rate = float(settings_data["hsd_rate"])

    gain_qty = 0
    shortage_qty = 0
    gain_amount = 0
    shortage_amount = 0

    if difference > 0:
        gain_qty = difference
        gain_amount = round(gain_qty * rate, 2)

    elif difference < 0:
        shortage_qty = abs(difference)
        shortage_amount = round(shortage_qty * rate, 2)

    cur.execute("""
        INSERT INTO tank_level (
            date,
            fuel_type,
            opening_stock,
            received_stock,
            own_tanker_stock,
            sale_stock,
            gain_qty,
            shortage_qty,
            current_stock,
            gain_amount,
            shortage_amount
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["date"],
        fuel_type,
        opening_stock,
        received_stock,
        own_tanker_stock,
        sale_stock,
        gain_qty,
        shortage_qty,
        actual_dip,
        gain_amount,
        shortage_amount
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("tank_level"))


@app.route("/edit-tank-level/<int:id>")
def edit_tank_level(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM tank_level WHERE id = ?", (id,))
    row = cur.fetchone()

    conn.close()

    return render_template("edit_tank_level.html", row=row)


@app.route("/update-tank-level/<int:id>", methods=["POST"])
def update_tank_level(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    data = request.form

    opening_stock = float(data["opening_stock"] or 0)
    received_stock = float(data["received_stock"] or 0)
    own_tanker_stock = float(data["own_tanker_stock"] or 0)
    sale_stock = float(data["sale_stock"] or 0)
    actual_dip = float(data["current_stock"] or 0)

    theoretical_stock = opening_stock + received_stock + own_tanker_stock - sale_stock
    difference = round(actual_dip - theoretical_stock, 2)

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT ms_rate, hsd_rate FROM settings WHERE id = 1")
    settings_data = cur.fetchone()

    if data["fuel_type"] == "MS":
        rate = float(settings_data["ms_rate"])
    else:
        rate = float(settings_data["hsd_rate"])

    gain_qty = 0
    shortage_qty = 0
    gain_amount = 0
    shortage_amount = 0

    if difference > 0:
        gain_qty = difference
        gain_amount = round(gain_qty * rate, 2)

    elif difference < 0:
        shortage_qty = abs(difference)
        shortage_amount = round(shortage_qty * rate, 2)

    cur.execute("""
        UPDATE tank_level
        SET
            date=?,
            fuel_type=?,
            opening_stock=?,
            received_stock=?,
            own_tanker_stock=?,
            sale_stock=?,
            gain_qty=?,
            shortage_qty=?,
            current_stock=?,
            gain_amount=?,
            shortage_amount=?
        WHERE id=?
    """, (
        data["date"],
        data["fuel_type"],
        opening_stock,
        received_stock,
        own_tanker_stock,
        sale_stock,
        gain_qty,
        shortage_qty,
        actual_dip,
        gain_amount,
        shortage_amount,
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("tank_level"))


@app.route("/delete-tank-level/<int:id>")
def delete_tank_level(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM tank_level WHERE id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("tank_level"))


@app.route("/digital-collection")
def digital_collection():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
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
        SELECT
            date,
            digital_collection,
            phonepe,
            card_swipe,
            hp_pay,
            hpcl_otp,
            upi_other,
            transport_received
        FROM daily_closing
        ORDER BY id DESC
        LIMIT 50
    """)
    rows = cur.fetchall()

    conn.close()

    return render_template(
        "digital_collection.html",
        rows=rows,
        monthly=monthly
    )


@app.route("/reports")
def reports():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_closing ORDER BY id DESC")
    rows = cur.fetchall()

    conn.close()

    return render_template("reports.html", rows=rows)


@app.route("/delete-report/<int:id>")
def delete_report(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM daily_closing WHERE id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("reports"))


@app.route("/edit-report/<int:id>")
def edit_report(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_closing WHERE id = ?", (id,))
    row = cur.fetchone()

    conn.close()

    return render_template("edit_report.html", row=row)


@app.route("/update-report/<int:id>", methods=["POST"])
def update_report(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        UPDATE daily_closing
        SET
            date=?,
            ms_litres=?,
            hsd_litres=?,
            total_fuel_sale=?,
            lube_sale=?,
            digital_collection=?,
            phonepe=?,
            card_swipe=?,
            hp_pay=?,
            hpcl_otp=?,
            upi_other=?,
            credit_given=?,
            transport_received=?,
            net_credit_due=?,
            total_expense=?,
            cash_in_hand=?
        WHERE id=?
    """, (
        request.form["date"],
        request.form["ms_litres"],
        request.form["hsd_litres"],
        request.form["total_fuel_sale"],
        request.form["lube_sale"],
        request.form.get("digital_collection", 0),
        request.form.get("phonepe", 0),
        request.form.get("card_swipe", 0),
        request.form.get("hp_pay", 0),
        request.form.get("hpcl_otp", 0),
        request.form.get("upi_other", 0),
        request.form["credit_given"],
        request.form["transport_received"],
        request.form["net_credit_due"],
        request.form["total_expense"],
        request.form["cash_in_hand"],
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("reports"))


@app.route("/export-reports")
def export_reports():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_closing ORDER BY date DESC")
    rows = cur.fetchall()

    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Closing Reports"

    headers = [
        "Date", "MS Litres", "HSD Litres", "Fuel Sale", "Lube Sale",
        "Digital Collection", "PhonePe", "Card Swipe", "HP Pay",
        "HPCL OTP", "UPI Other", "Credit Given", "Transport Received",
        "Net Credit Due", "Expense", "Cash In Hand"
    ]

    ws.append(headers)

    for row in rows:
        ws.append([
            row["date"],
            row["ms_litres"],
            row["hsd_litres"],
            row["total_fuel_sale"],
            row["lube_sale"],
            row["digital_collection"],
            row["phonepe"],
            row["card_swipe"],
            row["hp_pay"],
            row["hpcl_otp"],
            row["upi_other"],
            row["credit_given"],
            row["transport_received"],
            row["net_credit_due"],
            row["total_expense"],
            row["cash_in_hand"]
        ])

    file_stream = BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)

    return send_file(
        file_stream,
        as_attachment=True,
        download_name="Sai_Fuel_Mart_Reports.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/settings")
def settings():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM settings WHERE id = 1")
    settings_data = cur.fetchone()

    conn.close()

    return render_template("settings.html", settings=settings_data)


@app.route("/save-settings", methods=["POST"])
def save_settings():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        UPDATE settings
        SET ms_rate = ?, hsd_rate = ?
        WHERE id = 1
    """, (
        request.form["ms_rate"],
        request.form["hsd_rate"]
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("settings"))


@app.route("/attendance")
def attendance():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
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

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO staff_master (staff_name, role, status)
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

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO attendance (date, staff_name, attendance_status)
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

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
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

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO lube_stock (
            product_name,
            selling_rate,
            opening_stock,
            purchase_qty,
            sale_qty,
            closing_stock
        )
        VALUES (?, ?, ?, ?, ?, ?)
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


@app.route("/edit-lube/<int:id>")
def edit_lube(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM lube_stock WHERE id = ?", (id,))
    item = cur.fetchone()

    conn.close()

    return render_template("edit_lube.html", item=item)


@app.route("/update-lube/<int:id>", methods=["POST"])
def update_lube(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    product_name = request.form["product_name"]
    selling_rate = float(request.form["selling_rate"])
    opening_stock = float(request.form["opening_stock"])
    purchase_qty = float(request.form["purchase_qty"])
    sale_qty = float(request.form["sale_qty"])

    closing_stock = opening_stock + purchase_qty - sale_qty

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        UPDATE lube_stock
        SET
            product_name=?,
            selling_rate=?,
            opening_stock=?,
            purchase_qty=?,
            sale_qty=?,
            closing_stock=?
        WHERE id=?
    """, (
        product_name,
        selling_rate,
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

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM lube_stock WHERE id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("lube_stock"))


@app.route("/credit-transport")
def credit_transport():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM credit_transporters ORDER BY id DESC")
    transporters = cur.fetchall()

    conn.close()

    return render_template("credit_transport.html", transporters=transporters)


@app.route("/save-transporter", methods=["POST"])
def save_transporter():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    party_name = request.form["party_name"]
    opening_balance = float(request.form["opening_balance"] or 0)
    status = request.form["status"]

    balance_due = opening_balance

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO credit_transporters (
            party_name,
            opening_balance,
            credit_given,
            payment_received,
            balance_due,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        party_name,
        opening_balance,
        0,
        0,
        balance_due,
        status
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/save-transport-payment", methods=["POST"])
def save_transport_payment():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    date = request.form["date"]
    transporter_id = request.form["transporter_id"]
    payment_amount = float(request.form["payment_amount"] or 0)

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO transport_payments (
            date,
            transporter_id,
            payment_amount
        )
        VALUES (?, ?, ?)
    """, (
        date,
        transporter_id,
        payment_amount
    ))

    cur.execute("""
        UPDATE credit_transporters
        SET
            payment_received = payment_received + ?,
            balance_due = balance_due - ?
        WHERE id = ?
    """, (
        payment_amount,
        payment_amount,
        transporter_id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/edit-transporter/<int:id>")
def edit_transporter(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM credit_transporters WHERE id = ?", (id,))
    transporter = cur.fetchone()

    conn.close()

    return render_template("edit_transporter.html", transporter=transporter)


@app.route("/update-transporter/<int:id>", methods=["POST"])
def update_transporter(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    party_name = request.form["party_name"]
    opening_balance = float(request.form["opening_balance"] or 0)
    credit_given = float(request.form["credit_given"] or 0)
    payment_received = float(request.form["payment_received"] or 0)
    status = request.form["status"]

    balance_due = opening_balance + credit_given - payment_received

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        UPDATE credit_transporters
        SET
            party_name=?,
            opening_balance=?,
            credit_given=?,
            payment_received=?,
            balance_due=?,
            status=?
        WHERE id=?
    """, (
        party_name,
        opening_balance,
        credit_given,
        payment_received,
        balance_due,
        status,
        id
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/delete-transporter/<int:id>")
def delete_transporter(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM credit_transporters WHERE id = ?", (id,))
    cur.execute("DELETE FROM transport_payments WHERE transporter_id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("credit_transport"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)