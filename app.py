import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session
from datetime import timedelta

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
    if session.get("logged_in"):
        return render_template("dashboard.html")
    return redirect(url_for("login"))


@app.route("/daily-closing")
def daily_closing():
    if session.get("logged_in"):
        return render_template("daily_closing.html")
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

def init_db():
    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

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

    conn.commit()
    conn.close()

@app.route("/save-daily-closing", methods=["POST"])
def save_daily_closing():
    if not session.get("logged_in"):
        return {"status": "error", "message": "Not logged in"}

    data = request.get_json()

    conn = sqlite3.connect("sai_fuel_mart.db")
    cur = conn.cursor()

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

    return {"status": "success", "message": "Daily closing saved successfully"}

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



if __name__ == "__main__":
    init_db()
    app.run(debug=True)