import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session
from datetime import timedelta
from flask import jsonify
from openpyxl import Workbook
from flask import send_file
from io import BytesIO
from datetime import timedelta, datetime

app = Flask(__name__)

app.secret_key = "sai-fuel-mart-secret-key"
app.permanent_session_lifetime = timedelta(days=30)

USERNAME = "admin"
PASSWORD = "1234"


@app.route("/")
def home():
    if session.get("logged_in"):
        return render_template("dashboard.html")
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

            return redirect(url_for("home"))
        else:
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

    conn.close()

    return render_template(
        "dashboard.html",
        latest=latest,
        monthly=monthly
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

    conn.close()

    return render_template("daily_closing.html", settings=settings_data)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS staff_master (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        staff_name TEXT,
        role TEXT,
        status TEXT
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

    conn.commit()
    conn.close()

@app.route("/save-daily-closing", methods=["POST"])
def save_daily_closing():

    data = request.get_json()

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM daily_closing WHERE date = ?",
        (data["date"],)
    )

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
            credit_given,
            transport_received,
            net_credit_due,
            total_expense,
            cash_in_hand
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["date"],
        data["ms_litres"],
        data["hsd_litres"],
        data["total_fuel_sale"],
        data["lube_sale"],
        data["digital_collection"],
        data["credit_given"],
        data["transport_received"],
        data["net_credit_due"],
        data["total_expense"],
        data["cash_in_hand"]
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "status": "success",
        "message": "Daily closing saved successfully"
    })

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

    cur.execute(
        "SELECT * FROM daily_closing WHERE id = ?",
        (id,)
    )

    row = cur.fetchone()

    conn.close()

    return render_template(
        "edit_report.html",
        row=row
    )


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
        request.form["digital_collection"],
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

    ms_rate = request.form["ms_rate"]
    hsd_rate = request.form["hsd_rate"]

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

    cur.execute("""
        UPDATE settings
        SET ms_rate = ?, hsd_rate = ?
        WHERE id = 1
    """, (ms_rate, hsd_rate))

    conn.commit()
    conn.close()

    return redirect(url_for("settings"))

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

    conn.close()

    if row:
        return jsonify(dict(row))

    return jsonify({})

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
        "Digital Collection", "Credit Given", "Transport Received",
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

    cur.execute("SELECT COUNT(*) AS present_today FROM attendance WHERE date=? AND attendance_status='Present'", (today,))
    present_today = cur.fetchone()["present_today"]

    cur.execute("SELECT COUNT(*) AS absent_leave FROM attendance WHERE date=? AND attendance_status IN ('Absent','Leave')", (today,))
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
    if not session.get("lo" \
    "gged_in"):
        return redirect(url_for("login"))

    return render_template("lube_stock.html")



if __name__ == "__main__":
    init_db()
    app.run(debug=True)