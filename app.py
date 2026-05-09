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


if __name__ == "__main__":
    app.run(debug=True)