from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    send_from_directory, send_file, session
)

import os
import secrets
import hashlib
import smtplib

from email.message import EmailMessage
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from werkzeug.security import generate_password_hash, check_password_hash

from data_export import build_excel, rows_from_excel
from db import DATABASE, SCHEMA_FILE, get_db, init_db, is_hosted, using_postgres

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

app = Flask(__name__)
SECRET_KEY_FILE = Path("/etc/secrets/secret_key.txt")

if SECRET_KEY_FILE.exists():
    app.secret_key = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
else:
    app.secret_key = os.environ.get("SECRET_KEY")

if not app.secret_key:
    if os.environ.get("DATABASE_URL"):
        raise RuntimeError("SECRET_KEY is missing from Render.")
    app.secret_key = "change-this-secret-key"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("FLY_APP_NAME"):
    app.config["SESSION_COOKIE_SECURE"] = True

SQL_QUERIES = {
    "parts": {
        "title": "All spare parts",
        "sql": "SELECT * FROM parts ORDER BY name;"
    },
    "suppliers": {
        "title": "All suppliers",
        "sql": "SELECT * FROM suppliers ORDER BY name;"
    },
    "sales": {
        "title": "Sales with part names",
        "sql": """SELECT sales.id, sales.sale_date, parts.name AS part_name,
       parts.engine, parts.specification,
       sales.quantity, sales.unit_price, sales.total_amount, sales.profit
FROM sales
JOIN parts ON parts.id = sales.part_id
ORDER BY sales.id DESC;"""
    },
    "low_stock": {
        "title": "Low stock",
        "sql": """SELECT name, category, brand, engine, specification,
       stock_quantity, low_stock_level
FROM parts
WHERE stock_quantity <= low_stock_level
ORDER BY stock_quantity;"""
    },
    "price_history": {
        "title": "Price history",
        "sql": """SELECT price_history.id, parts.name AS part_name,
       price_history.old_price, price_history.new_price,
       price_history.reason, price_history.changed_at
FROM price_history
JOIN parts ON parts.id = price_history.part_id
ORDER BY price_history.id DESC;"""
    }
}


def admin_exists():
    conn = get_db()
    try:
        result = conn.execute(
            "SELECT COUNT(*) AS total FROM admin_users"
        ).fetchone()
        return result["total"] > 0
    finally:
        conn.close()


def send_reset_email(email, reset_url):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_from = os.environ.get("MAIL_FROM", smtp_user)

    if not all([smtp_host, smtp_user, smtp_password, mail_from]):
        raise RuntimeError("Email settings are not configured.")

    message = EmailMessage()
    message["Subject"] = "Auto Spares System - Password Reset"
    message["From"] = mail_from
    message["To"] = email
    message.set_content(
        "You requested a password reset for your Auto Spares account.\n\n"
        f"Use this link to reset your password:\n{reset_url}\n\n"
        "If you did not request this, ignore this email.\n"
        "This link expires after 30 minutes."
    )

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(message)


init_db()


@app.context_processor
def inject_globals():
    return {
        "now": datetime.now(),
        "auth_on": admin_exists(),
        "is_hosted": is_hosted(),
        "using_postgres": using_postgres()
    }


@app.template_filter("dt")
def format_dt(value):
    if value is None:
        return "-"
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return text[:19].replace("T", " ")


@app.before_request
def require_login():
    allowed = {
        "login", "setup", "forgot_password", "reset_password",
        "static", "service_worker", "healthz"
    }

    if request.endpoint in allowed:
        return None

    if not admin_exists():
        return redirect(url_for("setup"))

    if session.get("admin_id"):
        return None

    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if admin_exists():
        return redirect(url_for("login"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all([full_name, username, email, password, confirm_password]):
            flash("Please complete all fields.", "danger")
            return render_template("setup.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("setup.html")

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "danger")
            return render_template("setup.html")

        conn = get_db()
        try:
            password_hash = generate_password_hash(password)
            conn.insert(
                """INSERT INTO admin_users
                   (full_name, username, email, password_hash, role, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    full_name,
                    username,
                    email,
                    password_hash,
                    "admin",
                    datetime.now().isoformat()
                )
            )
            conn.commit()
        except Exception:
            conn.close()
            app.logger.exception("Administrator account creation failed.")
            flash(
                "Account creation failed. The username or email may already be in use.",
                "danger"
            )
            return render_template("setup.html")

        conn.close()
        flash("Administrator account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not admin_exists():
        return redirect(url_for("setup"))

    if session.get("admin_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        try:
            admin = conn.execute(
                "SELECT * FROM admin_users WHERE username=?",
                (username,)
            ).fetchone()
        finally:
            conn.close()

        if admin and check_password_hash(admin["password_hash"], password):
            session.clear()
            session["admin_id"] = admin["id"]
            session["admin_name"] = admin["full_name"]
            return redirect(url_for("dashboard"))

        flash("Incorrect username or password.", "danger")

    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if not admin_exists():
        return redirect(url_for("setup"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = get_db()

        try:
            admin = conn.execute(
                "SELECT * FROM admin_users WHERE email=?",
                (email,)
            ).fetchone()

            if admin:
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                created_at = datetime.now()
                expires_at = created_at + timedelta(minutes=30)

                conn.insert(
                    """INSERT INTO password_reset_tokens
                       (user_id, token_hash, expires_at, used, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        admin["id"],
                        token_hash,
                        expires_at.isoformat(),
                        0,
                        created_at.isoformat()
                    )
                )
                conn.commit()

                reset_url = url_for(
                    "reset_password",
                    token=token,
                    _external=True
                )

                try:
                    send_reset_email(email, reset_url)
                except Exception:
                    app.logger.exception("Could not send password reset email.")
        finally:
            conn.close()

        flash(
            "If an account exists with that email, password reset instructions will be sent.",
            "info"
        )

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = get_db()

    try:
        reset_record = conn.execute(
            """SELECT * FROM password_reset_tokens
               WHERE token_hash=? AND used=0""",
            (token_hash,)
        ).fetchone()

        if not reset_record:
            flash("This password reset link is invalid or has already been used.", "danger")
            return redirect(url_for("login"))

        if datetime.fromisoformat(reset_record["expires_at"]) < datetime.now():
            flash("This password reset link has expired.", "danger")
            return redirect(url_for("forgot_password"))

        if request.method == "POST":
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if len(password) < 8:
                flash("Password must contain at least 8 characters.", "danger")
                return render_template("reset_password.html", token=token)

            if password != confirm_password:
                flash("Passwords do not match.", "danger")
                return render_template("reset_password.html", token=token)

            new_hash = generate_password_hash(password)
            conn.execute(
                "UPDATE admin_users SET password_hash=? WHERE id=?",
                (new_hash, reset_record["user_id"])
            )
            conn.execute(
                "UPDATE password_reset_tokens SET used=1 WHERE id=?",
                (reset_record["id"],)
            )
            conn.commit()

            flash("Your password has been reset. You can now log in.", "success")
            return redirect(url_for("login"))

        return render_template("reset_password.html", token=token)
    finally:
        conn.close()


@app.after_request
def pwa_headers(response):
    if request.path == "/sw.js":
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/sw.js")
def service_worker():
    return send_from_directory(STATIC_DIR, "sw.js", mimetype="application/javascript")


@app.route("/offline")
def offline():
    return render_template("offline.html")


@app.route("/")
def dashboard():
    conn = get_db()
    total_items = conn.execute(
        "SELECT COALESCE(SUM(stock_quantity), 0) AS total FROM parts"
    ).fetchone()["total"]

    today = datetime.now().strftime("%Y-%m-%d")
    sales_today = conn.execute(
        "SELECT COALESCE(SUM(total_amount), 0) AS total FROM sales WHERE substr(sale_date,1,10)=?",
        (today,)
    ).fetchone()["total"]

    monthly_sales = conn.execute(
        """SELECT COALESCE(SUM(total_amount),0) AS total
           FROM sales
           WHERE substr(sale_date,1,7)=?""",
        (datetime.now().strftime("%Y-%m"),)
    ).fetchone()["total"]

    monthly_profit = conn.execute(
        """SELECT COALESCE(SUM(profit),0) AS total
           FROM sales
           WHERE substr(sale_date,1,7)=?""",
        (datetime.now().strftime("%Y-%m"),)
    ).fetchone()["total"]

    supplier_count = conn.execute(
        "SELECT COUNT(*) AS total FROM suppliers"
    ).fetchone()["total"]

    low_stock = conn.execute(
        """SELECT * FROM parts
           WHERE stock_quantity <= low_stock_level
           ORDER BY stock_quantity ASC"""
    ).fetchall()

    recent_sales = conn.execute(
        """SELECT sales.*, parts.name AS part_name
           FROM sales
           JOIN parts ON parts.id = sales.part_id
           ORDER BY sales.id DESC LIMIT 8"""
    ).fetchall()

    conn.close()
    return render_template(
        "dashboard.html",
        total_items=total_items,
        sales_today=sales_today,
        monthly_sales=monthly_sales,
        monthly_profit=monthly_profit,
        supplier_count=supplier_count,
        low_stock=low_stock,
        recent_sales=recent_sales
    )


@app.route("/inventory")
def inventory():
    search = request.args.get("search", "").strip()
    conn = get_db()

    if search:
        like = f"%{search}%"
        parts = conn.execute(
            """SELECT * FROM parts
               WHERE name LIKE ? OR category LIKE ? OR brand LIKE ?
                  OR engine LIKE ? OR specification LIKE ?
               ORDER BY id DESC""",
            (like, like, like, like, like)
        ).fetchall()
    else:
        parts = conn.execute("SELECT * FROM parts ORDER BY id DESC").fetchall()

    conn.close()
    return render_template("inventory.html", parts=parts, search=search)


@app.route("/inventory/add", methods=["GET", "POST"])
def add_part():
    if request.method == "POST":
        name = request.form["name"].strip()
        category = request.form.get("category", "").strip()
        brand = request.form.get("brand", "").strip()
        engine = request.form.get("engine", "").strip()
        specification = request.form.get("specification", "").strip()
        buying_price = float(request.form.get("buying_price", 0) or 0)
        selling_price = float(request.form.get("selling_price", 0) or 0)
        stock_quantity = int(request.form.get("stock_quantity", 0) or 0)
        low_stock_level = int(request.form.get("low_stock_level", 5) or 5)

        if not name:
            flash("Part name is required.", "danger")
            return redirect(url_for("add_part"))

        conn = get_db()
        created_at = datetime.now().isoformat()
        part_id = conn.insert(
            """INSERT INTO parts
               (name, category, brand, engine, specification, buying_price, selling_price,
                stock_quantity, low_stock_level, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, category, brand, engine, specification, buying_price, selling_price,
             stock_quantity, low_stock_level, created_at)
        )
        conn.insert(
            """INSERT INTO price_history
               (part_id, old_price, new_price, reason, changed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (part_id, None, selling_price, "Initial selling price", created_at)
        )
        conn.commit()
        conn.close()
        flash("Spare part added successfully.", "success")
        return redirect(url_for("inventory"))

    return render_template("part_form.html", part=None)


@app.route("/inventory/edit/<int:part_id>", methods=["GET", "POST"])
def edit_part(part_id):
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()

    if not part:
        conn.close()
        flash("Spare part not found.", "danger")
        return redirect(url_for("inventory"))

    if request.method == "POST":
        new_selling_price = float(request.form.get("selling_price", 0) or 0)
        price_reason = request.form.get("price_reason", "").strip()
        old_selling_price = float(part["selling_price"])

        conn.execute(
            """UPDATE parts SET
               name=?, category=?, brand=?, engine=?, specification=?, buying_price=?,
               selling_price=?, stock_quantity=?, low_stock_level=?
               WHERE id=?""",
            (
                request.form["name"].strip(),
                request.form.get("category", "").strip(),
                request.form.get("brand", "").strip(),
                request.form.get("engine", "").strip(),
                request.form.get("specification", "").strip(),
                float(request.form.get("buying_price", 0) or 0),
                new_selling_price,
                int(request.form.get("stock_quantity", 0) or 0),
                int(request.form.get("low_stock_level", 5) or 5),
                part_id
            )
        )

        if new_selling_price != old_selling_price:
            conn.insert(
                """INSERT INTO price_history
                   (part_id, old_price, new_price, reason, changed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (part_id, old_selling_price, new_selling_price,
                 price_reason or "Selling price updated", datetime.now().isoformat())
            )

        conn.commit()
        conn.close()
        flash("Spare part updated successfully.", "success")
        return redirect(url_for("inventory"))

    conn.close()
    return render_template("part_form.html", part=part)


@app.post("/inventory/delete/<int:part_id>")
def delete_part(part_id):
    conn = get_db()
    sales_exist = conn.execute(
        "SELECT COUNT(*) AS total FROM sales WHERE part_id=?", (part_id,)
    ).fetchone()["total"]

    if sales_exist:
        flash("This part cannot be deleted because it has sales records.", "danger")
    else:
        conn.execute("DELETE FROM parts WHERE id=?", (part_id,))
        conn.commit()
        flash("Spare part deleted.", "success")

    conn.close()
    return redirect(url_for("inventory"))


@app.route("/price-history/<int:part_id>")
def price_history(part_id):
    conn = get_db()
    part = conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
    if not part:
        conn.close()
        flash("Spare part not found.", "danger")
        return redirect(url_for("inventory"))

    history = conn.execute(
        """SELECT * FROM price_history
           WHERE part_id=?
           ORDER BY id DESC""",
        (part_id,)
    ).fetchall()
    conn.close()
    return render_template("price_history.html", part=part, history=history)


@app.route("/sales", methods=["GET", "POST"])
def sales():
    conn = get_db()
    parts = conn.execute(
        "SELECT * FROM parts ORDER BY name ASC"
    ).fetchall()

    if request.method == "POST":
        try:
            part_id = int(request.form["part_id"])
            quantity = int(request.form["quantity"])
        except (ValueError, KeyError):
            conn.close()
            flash("Enter valid sale details.", "danger")
            return redirect(url_for("sales"))

        part = conn.execute(
            "SELECT * FROM parts WHERE id=?", (part_id,)
        ).fetchone()

        if not part:
            conn.close()
            flash("Selected part does not exist.", "danger")
            return redirect(url_for("sales"))

        if quantity <= 0:
            conn.close()
            flash("Quantity must be greater than zero.", "danger")
            return redirect(url_for("sales"))

        if quantity > part["stock_quantity"]:
            conn.close()
            flash(f"Not enough stock. Available: {part['stock_quantity']}.", "danger")
            return redirect(url_for("sales"))

        try:
            unit_price = float(
                request.form.get("unit_price", part["selling_price"]) or part["selling_price"]
            )
        except ValueError:
            conn.close()
            flash("Enter a valid unit selling price.", "danger")
            return redirect(url_for("sales"))

        if unit_price < 0:
            conn.close()
            flash("Unit selling price cannot be negative.", "danger")
            return redirect(url_for("sales"))

        total_amount = unit_price * quantity
        profit = (unit_price - part["buying_price"]) * quantity

        conn.insert(
            """INSERT INTO sales
               (part_id, quantity, unit_price, total_amount, profit, sale_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (part_id, quantity, unit_price, total_amount, profit,
             datetime.now().isoformat())
        )
        conn.execute(
            "UPDATE parts SET stock_quantity=stock_quantity-? WHERE id=?",
            (quantity, part_id)
        )
        conn.commit()
        conn.close()

        flash(
            f"Sale recorded. Total: KSh {total_amount:,.2f}",
            "success"
        )
        return redirect(url_for("sales"))

    history = conn.execute(
        """SELECT sales.*, parts.name AS part_name,
                  parts.engine, parts.specification
           FROM sales JOIN parts ON parts.id=sales.part_id
           ORDER BY sales.id DESC LIMIT 50"""
    ).fetchall()
    conn.close()

    return render_template("sales.html", parts=parts, history=history)


@app.route("/suppliers")
def suppliers():
    search = request.args.get("search", "").strip()
    conn = get_db()

    if search:
        like = f"%{search}%"
        supplier_rows = conn.execute(
            """SELECT * FROM suppliers
               WHERE name LIKE ? OR phone LIKE ? OR company LIKE ?
               ORDER BY name ASC""",
            (like, like, like)
        ).fetchall()
    else:
        supplier_rows = conn.execute(
            "SELECT * FROM suppliers ORDER BY name ASC"
        ).fetchall()

    conn.close()
    return render_template("suppliers.html", suppliers=supplier_rows, search=search)


@app.route("/suppliers/add", methods=["GET", "POST"])
def add_supplier():
    if request.method == "POST":
        name = request.form["name"].strip()
        phone = request.form["phone"].strip()

        if not name or not phone:
            flash("Supplier name and phone number are required.", "danger")
            return redirect(url_for("add_supplier"))

        conn = get_db()
        conn.insert(
            """INSERT INTO suppliers
               (name, phone, company, parts_supplied, location, email, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                phone,
                request.form.get("company", "").strip(),
                request.form.get("parts_supplied", "").strip(),
                request.form.get("location", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("notes", "").strip(),
                datetime.now().isoformat()
            )
        )
        conn.commit()
        conn.close()
        flash("Supplier contact added successfully.", "success")
        return redirect(url_for("suppliers"))

    return render_template("supplier_form.html", supplier=None)


@app.route("/suppliers/edit/<int:supplier_id>", methods=["GET", "POST"])
def edit_supplier(supplier_id):
    conn = get_db()
    supplier = conn.execute(
        "SELECT * FROM suppliers WHERE id=?", (supplier_id,)
    ).fetchone()

    if not supplier:
        conn.close()
        flash("Supplier not found.", "danger")
        return redirect(url_for("suppliers"))

    if request.method == "POST":
        name = request.form["name"].strip()
        phone = request.form["phone"].strip()

        if not name or not phone:
            conn.close()
            flash("Supplier name and phone number are required.", "danger")
            return redirect(url_for("edit_supplier", supplier_id=supplier_id))

        conn.execute(
            """UPDATE suppliers SET
               name=?, phone=?, company=?, parts_supplied=?,
               location=?, email=?, notes=?
               WHERE id=?""",
            (
                name,
                phone,
                request.form.get("company", "").strip(),
                request.form.get("parts_supplied", "").strip(),
                request.form.get("location", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("notes", "").strip(),
                supplier_id
            )
        )
        conn.commit()
        conn.close()
        flash("Supplier contact updated successfully.", "success")
        return redirect(url_for("suppliers"))

    conn.close()
    return render_template("supplier_form.html", supplier=supplier)


@app.post("/suppliers/delete/<int:supplier_id>")
def delete_supplier(supplier_id):
    conn = get_db()
    conn.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
    conn.commit()
    conn.close()
    flash("Supplier contact deleted.", "success")
    return redirect(url_for("suppliers"))


def report_dates(start_date, end_date, preset):
    if preset and preset != "all" and not start_date and not end_date:
        today = datetime.now().date()
        if preset == "today":
            start_date = end_date = today.isoformat()
        elif preset == "yesterday":
            day = today - timedelta(days=1)
            start_date = end_date = day.isoformat()
        elif preset == "this_week":
            start_date = (today - timedelta(days=today.weekday())).isoformat()
            end_date = today.isoformat()
        elif preset == "last_week":
            start = today - timedelta(days=today.weekday() + 7)
            start_date = start.isoformat()
            end_date = (start + timedelta(days=6)).isoformat()
        elif preset == "this_month":
            start_date = today.replace(day=1).isoformat()
            end_date = today.isoformat()
        elif preset == "last_month":
            first = today.replace(day=1)
            last_prev = first - timedelta(days=1)
            start_date = last_prev.replace(day=1).isoformat()
            end_date = last_prev.isoformat()
    return start_date, end_date, preset or "all"


@app.route("/reports")
def reports():
    conn = get_db()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    preset = request.args.get("preset", "all").strip()
    start_date, end_date, preset = report_dates(start_date, end_date, preset)

    where = []
    params = []
    if start_date:
        where.append("substr(sale_date,1,10) >= ?")
        params.append(start_date)
    if end_date:
        where.append("substr(sale_date,1,10) <= ?")
        params.append(end_date)
    condition = (" WHERE " + " AND ".join(where)) if where else ""

    totals = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) AS sales, "
        "COALESCE(SUM(profit),0) AS profit, COUNT(*) AS transactions "
        f"FROM sales{condition}",
        params
    ).fetchone()

    daily = conn.execute(
        f"""SELECT substr(sale_date,1,10) AS day, SUM(total_amount) AS sales,
                  SUM(profit) AS profit, COUNT(*) AS transactions
           FROM sales{condition}
           GROUP BY substr(sale_date,1,10) ORDER BY day DESC""",
        params
    ).fetchall()

    monthly = conn.execute(
        f"""SELECT substr(sale_date,1,7) AS month, SUM(total_amount) AS sales,
                  SUM(profit) AS profit
           FROM sales{condition}
           GROUP BY substr(sale_date,1,7) ORDER BY month DESC""",
        params
    ).fetchall()

    conn.close()
    return render_template(
        "reports.html",
        total_sales=totals["sales"],
        total_profit=totals["profit"],
        total_transactions=totals["transactions"],
        daily=daily,
        monthly=monthly,
        start_date=start_date,
        end_date=end_date,
        preset=preset
    )


@app.route("/data")
def data():
    selected = request.args.get("query", "parts")
    if selected not in SQL_QUERIES:
        selected = "parts"
    conn = get_db()
    rows = conn.execute(SQL_QUERIES[selected]["sql"]).fetchall()
    conn.close()
    columns = list(rows[0].keys()) if rows else []
    return render_template(
        "data.html",
        queries=SQL_QUERIES,
        selected=selected,
        rows=rows,
        columns=columns,
        database_file="PostgreSQL" if using_postgres() else str(DATABASE)
    )


@app.route("/data/excel")
def export_excel():
    conn = get_db()
    parts = conn.execute("SELECT * FROM parts ORDER BY id").fetchall()
    sales = conn.execute(
        """SELECT sales.*, parts.name AS part_name,
                  parts.engine, parts.specification
           FROM sales JOIN parts ON parts.id = sales.part_id
           ORDER BY sales.id"""
    ).fetchall()
    suppliers = conn.execute("SELECT * FROM suppliers ORDER BY id").fetchall()
    history = conn.execute(
        """SELECT price_history.*, parts.name AS part_name
           FROM price_history JOIN parts ON parts.id = price_history.part_id
           ORDER BY price_history.id"""
    ).fetchall()
    conn.close()
    buffer, filename = build_excel(
        parts, sales, suppliers, history, SCHEMA_FILE.read_text(encoding="utf-8")
    )
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/data/database")
def export_database():
    if using_postgres():
        flash("Cloud PostgreSQL cannot be downloaded as a .db file. Use Excel instead.", "danger")
        return redirect(url_for("data"))
    if not DATABASE.exists():
        flash("No database file yet.", "danger")
        return redirect(url_for("data"))
    return send_file(
        DATABASE,
        as_attachment=True,
        download_name="auto_spares.db",
        mimetype="application/vnd.sqlite3"
    )


@app.route("/data/schema.sql")
def schema_file():
    return send_file(SCHEMA_FILE, as_attachment=True, download_name="schema.sql")


def _num(value, default=0, as_int=False):
    if value is None or value == "":
        value = default
    number = float(value)
    return int(number) if as_int else number


@app.post("/data/import")
def import_excel():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename.lower().endswith(".xlsx"):
        flash("Choose an Excel .xlsx file.", "danger")
        return redirect(url_for("data"))

    payload = BytesIO(uploaded.read())
    parts_rows = rows_from_excel(payload, "Parts")
    payload.seek(0)
    supplier_rows = rows_from_excel(payload, "Suppliers")
    added_parts = 0
    added_suppliers = 0
    conn = get_db()
    created_at = datetime.now().isoformat()

    for row in parts_rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        selling_price = _num(row.get("selling_price"))
        part_id = conn.insert(
            """INSERT INTO parts
               (name, category, brand, engine, specification, buying_price, selling_price,
                stock_quantity, low_stock_level, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                str(row.get("category") or "").strip(),
                str(row.get("brand") or "").strip(),
                str(row.get("engine") or "").strip(),
                str(row.get("specification") or "").strip(),
                _num(row.get("buying_price")),
                selling_price,
                _num(row.get("stock_quantity"), as_int=True),
                _num(row.get("low_stock_level") or 5, 5, as_int=True),
                created_at
            )
        )
        conn.insert(
            """INSERT INTO price_history
               (part_id, old_price, new_price, reason, changed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (part_id, None, selling_price, "Imported from Excel", created_at)
        )
        added_parts += 1

    for row in supplier_rows:
        name = str(row.get("name") or "").strip()
        phone = str(row.get("phone") or "").strip()
        if not name or not phone:
            continue
        conn.insert(
            """INSERT INTO suppliers
               (name, phone, company, parts_supplied, location, email, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                phone,
                str(row.get("company") or "").strip(),
                str(row.get("parts_supplied") or "").strip(),
                str(row.get("location") or "").strip(),
                str(row.get("email") or "").strip(),
                str(row.get("notes") or "").strip(),
                created_at
            )
        )
        added_suppliers += 1

    conn.commit()
    conn.close()
    flash(
        f"Imported {added_parts} spare part(s) and {added_suppliers} supplier(s) from Excel.",
        "success"
    )
    return redirect(url_for("data"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
