import os
import re
import csv
import io
from functools import wraps

import mysql.connector
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, Response
)
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY")
if not app.secret_key or app.secret_key == "change_this_to_a_long_random_secret":
    raise RuntimeError("Set a private, random SECRET_KEY in your .env file.")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "newsletter_db"),
}

admin_password = os.getenv("ADMIN_PASSWORD")
if not admin_password:
    raise RuntimeError("Set ADMIN_PASSWORD in your .env file.")

ADMIN_PASSWORD_HASH = generate_password_hash(admin_password)


def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_logged_in"):
            flash("Please log in to access the dashboard.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped_view


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/subscribe", methods=["POST"])
def subscribe():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not name or len(name) > 100:
        flash("Please enter a name (maximum 100 characters).", "error")
        return redirect(url_for("index"))

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("index"))

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT id, status FROM subscribers WHERE email = %s",
            (email,)
        )
        existing = cursor.fetchone()

        if existing:
            if existing[1] == "active":
                flash("This email is already subscribed.", "error")
            else:
                cursor.execute(
                    "UPDATE subscribers SET name=%s, status='active' WHERE id=%s",
                    (name, existing[0])
                )
                connection.commit()
                flash(
                    "Welcome back! Your subscription is active again.",
                    "success"
                )
        else:
            cursor.execute(
                "INSERT INTO subscribers (name, email) VALUES (%s, %s)",
                (name, email)
            )
            connection.commit()
            flash("Subscription successful! Welcome aboard.", "success")

    except mysql.connector.Error:
        app.logger.exception("Database error during subscription")
        flash("We could not save your subscription. Please try again.", "error")

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()

    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")

        if check_password_hash(ADMIN_PASSWORD_HASH, password):
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("subscribers"))

        flash("Incorrect password. Please try again.", "error")

    return render_template("login.html")


@app.route("/subscribers")
@admin_required
def subscribers():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT id, name, email, status, subscribed_at "
            "FROM subscribers ORDER BY subscribed_at DESC"
        )
        rows = cursor.fetchall()

        return render_template("subscribers.html", subscribers=rows)

    except mysql.connector.Error:
        app.logger.exception("Could not load subscribers")
        flash("Could not load the subscriber list.", "error")
        return redirect(url_for("index"))

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@app.route("/toggle-status/<int:subscriber_id>", methods=["POST"])
@admin_required
def toggle_status(subscriber_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "UPDATE subscribers SET status = "
            "IF(status = 'active', 'inactive', 'active') WHERE id = %s",
            (subscriber_id,)
        )
        connection.commit()

        if cursor.rowcount:
            flash("Subscriber status updated.", "success")
        else:
            flash("Subscriber was not found.", "error")

    except mysql.connector.Error:
        app.logger.exception("Could not update subscriber status")
        flash("Could not update subscriber status.", "error")

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()

    return redirect(url_for("subscribers"))


@app.route("/export-subscribers")
@admin_required
def export_subscribers():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT id, name, email, status, subscribed_at "
            "FROM subscribers ORDER BY subscribed_at DESC"
        )
        rows = cursor.fetchall()

        output = io.StringIO(newline="")
        writer = csv.writer(output)

        writer.writerow([
            "ID", "Name", "Email", "Status", "Date Subscribed"
        ])

        def safe_csv_value(value):
            text = "" if value is None else str(value)

            # Reduce spreadsheet formula-injection risk for text fields.
            if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                return "'" + text

            return text

        for row in rows:
            subscribed_at = row["subscribed_at"]
            date_text = (
                subscribed_at.strftime("%Y-%m-%d %H:%M:%S")
                if subscribed_at else ""
            )

            writer.writerow([
                row["id"],
                safe_csv_value(row["name"]),
                safe_csv_value(row["email"]),
                safe_csv_value(row["status"]),
                date_text,
            ])

        response = Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv; charset=utf-8"
        )
        response.headers["Content-Disposition"] = (
            'attachment; filename="newsletter_subscribers.csv"'
        )
        response.headers["Cache-Control"] = "no-store"

        return response

    except mysql.connector.Error:
        app.logger.exception("Could not export subscribers")
        flash("Could not export subscribers. Please try again.", "error")
        return redirect(url_for("subscribers"))

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)