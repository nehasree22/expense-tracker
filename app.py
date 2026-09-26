from flask import Flask, render_template, request, redirect, session, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from datetime import date, datetime
from calendar import monthrange
from functools import wraps

app = Flask(__name__)
app.secret_key = "dev-secret-key-change-this-if-you-ever-deploy-it"

DATABASE = "database/expense.db"

PAYMENT_METHODS = ["Cash", "Card", "UPI", "Bank Transfer"]

DEFAULT_CATEGORIES = [
    ("Food & Dining", "F"),
    ("Groceries", "G"),
    ("Transport", "T"),
    ("Shopping", "S"),
    ("Bills & Utilities", "B"),
    ("Entertainment", "E"),
    ("Health", "H"),
    ("Other", "O"),
]


# =========================================
# DATABASE
# =========================================

def get_db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_database():
    connection = get_db_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            expense_date TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            mark TEXT NOT NULL
        )
    """)

    existing = connection.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    if existing == 0:
        connection.executemany(
            "INSERT INTO categories (name, mark) VALUES (?, ?)",
            DEFAULT_CATEGORIES
        )

    connection.commit()
    connection.close()


def get_categories():
    connection = get_db_connection()
    rows = connection.execute("SELECT * FROM categories ORDER BY name ASC").fetchall()
    connection.close()
    return [dict(r) for r in rows]


def category_mark_map():
    return {c["name"]: c["mark"] for c in get_categories()}


# =========================================
# AUTH
# =========================================

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        if not session.get("is_admin"):
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        confirm = request.form.get("confirm_password", "")

        error = None
        if not username or not password:
            error = "Username and password are both required."
        elif len(password) < 4:
            error = "Password should be at least 4 characters."
        elif password != confirm:
            error = "Passwords don't match."

        if not error:
            connection = get_db_connection()
            existing = connection.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()

            if existing:
                error = "That username is already taken."
            else:
                # The very first account created on this install becomes admin automatically.
                user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                is_admin = 1 if user_count == 0 else 0

                connection.execute(
                    "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                    (username, generate_password_hash(password), is_admin, datetime.now().isoformat())
                )
                connection.commit()
                connection.close()
                return redirect(url_for("login", registered="1"))

            connection.close()

        return render_template("register.html", error=error, username=username)

    return render_template("register.html", error=None, username="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    just_registered = request.args.get("registered") == "1"
    next_page = request.args.get("next") or request.form.get("next") or url_for("dashboard")

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        connection = get_db_connection()
        user = connection.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        connection.close()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["is_admin"] = bool(user["is_admin"])
            return redirect(next_page)

        return render_template("login.html", error="Incorrect username or password.", username=username, next=next_page)

    return render_template("login.html", error=None, username="", just_registered=just_registered, next=next_page)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================
# DASHBOARD (overview)
# =========================================

@app.route("/")
@login_required
def dashboard():
    connection = get_db_connection()
    expenses = connection.execute("""
        SELECT * FROM expenses WHERE user_id = ? ORDER BY expense_date DESC, id DESC
    """, (session["user_id"],)).fetchall()
    connection.close()

    marks = category_mark_map()
    total = sum(e["amount"] for e in expenses)

    current_month = date.today().strftime("%Y-%m")
    this_month_total = sum(e["amount"] for e in expenses if e["expense_date"].startswith(current_month))

    breakdown = build_breakdown(expenses, marks)
    top_category = breakdown[0]["name"] if breakdown else "—"

    recent = [attach_mark(e, marks) for e in expenses[:6]]

    return render_template(
        "dashboard.html",
        expenses=recent,
        total=total,
        this_month_total=this_month_total,
        top_category=top_category,
        category_breakdown=breakdown,
        transaction_count=len(expenses),
        showing_recent=len(expenses) > 6,
        today=date.today().isoformat(),
        categories=get_categories(),
        payment_methods=PAYMENT_METHODS
    )


# =========================================
# TRANSACTIONS (full ledger — search, filter, sort, edit, delete)
# =========================================

@app.route("/transactions")
@login_required
def transactions():
    q = request.args.get("q", "").strip()
    category_filter = request.args.get("category", "")
    payment_filter = request.args.get("payment_method", "")
    sort = request.args.get("sort", "date_desc")

    sql = "SELECT * FROM expenses WHERE user_id = ?"
    params = [session["user_id"]]

    if q:
        sql += " AND title LIKE ?"
        params.append(f"%{q}%")

    if category_filter:
        sql += " AND category = ?"
        params.append(category_filter)

    if payment_filter:
        sql += " AND payment_method = ?"
        params.append(payment_filter)

    sort_map = {
        "date_desc": "expense_date DESC, id DESC",
        "date_asc": "expense_date ASC, id ASC",
        "amount_desc": "amount DESC",
        "amount_asc": "amount ASC",
    }
    sql += " ORDER BY " + sort_map.get(sort, sort_map["date_desc"])

    connection = get_db_connection()
    rows = connection.execute(sql, params).fetchall()
    connection.close()

    marks = category_mark_map()
    expense_rows = [attach_mark(e, marks) for e in rows]
    filtered_total = sum(e["amount"] for e in rows)

    return render_template(
        "transactions.html",
        expenses=expense_rows,
        filtered_total=filtered_total,
        transaction_count=len(expense_rows),
        categories=get_categories(),
        payment_methods=PAYMENT_METHODS,
        today=date.today().isoformat(),
        q=q,
        category_filter=category_filter,
        payment_filter=payment_filter,
        sort=sort
    )


@app.route("/add", methods=["POST"])
@login_required
def add_expense():
    connection = get_db_connection()
    connection.execute("""
        INSERT INTO expenses (user_id, title, amount, category, payment_method, expense_date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        session["user_id"],
        request.form["title"],
        request.form["amount"],
        request.form["category"],
        request.form["payment_method"],
        request.form["expense_date"]
    ))
    connection.commit()
    connection.close()
    return redirect(request.form.get("next") or "/")


@app.route("/edit/<int:expense_id>", methods=["POST"])
@login_required
def edit_expense(expense_id):
    connection = get_db_connection()
    connection.execute("""
        UPDATE expenses
        SET title = ?, amount = ?, category = ?, payment_method = ?, expense_date = ?
        WHERE id = ? AND user_id = ?
    """, (
        request.form["title"],
        request.form["amount"],
        request.form["category"],
        request.form["payment_method"],
        request.form["expense_date"],
        expense_id,
        session["user_id"]
    ))
    connection.commit()
    connection.close()
    return redirect(request.form.get("next") or "/")


@app.route("/delete/<int:expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    connection = get_db_connection()
    connection.execute(
        "DELETE FROM expenses WHERE id = ? AND user_id = ?",
        (expense_id, session["user_id"])
    )
    connection.commit()
    connection.close()
    return redirect(request.form.get("next") or "/")


# =========================================
# CATEGORIES (shared list, per-user totals)
# =========================================

@app.route("/categories")
@login_required
def categories_page():
    connection = get_db_connection()
    expenses = connection.execute(
        "SELECT * FROM expenses WHERE user_id = ?", (session["user_id"],)
    ).fetchall()
    cats = connection.execute("SELECT * FROM categories ORDER BY name ASC").fetchall()
    connection.close()

    total = sum(e["amount"] for e in expenses)
    spend_by_category = {}
    for e in expenses:
        spend_by_category[e["category"]] = spend_by_category.get(e["category"], 0) + e["amount"]

    category_rows = []
    for c in cats:
        amount = spend_by_category.get(c["name"], 0)
        percent = round((amount / total) * 100, 1) if total > 0 else 0
        category_rows.append({
            "id": c["id"],
            "name": c["name"],
            "mark": c["mark"],
            "amount": amount,
            "percent": percent
        })
    category_rows.sort(key=lambda c: c["amount"], reverse=True)

    return render_template("categories.html", category_rows=category_rows)


@app.route("/categories/add", methods=["POST"])
@login_required
def add_category():
    name = request.form["name"].strip()
    mark = (request.form.get("mark") or name[:1]).strip().upper()[:2]

    if name:
        connection = get_db_connection()
        try:
            connection.execute(
                "INSERT INTO categories (name, mark) VALUES (?, ?)",
                (name, mark or "•")
            )
            connection.commit()
        except sqlite3.IntegrityError:
            pass
        connection.close()

    return redirect("/categories")


@app.route("/categories/delete/<int:category_id>", methods=["POST"])
@login_required
def delete_category(category_id):
    connection = get_db_connection()
    connection.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    connection.commit()
    connection.close()
    return redirect("/categories")


# =========================================
# REPORTS (monthly trend + breakdown)
# =========================================

@app.route("/reports")
@login_required
def reports():
    connection = get_db_connection()
    expenses = connection.execute(
        "SELECT * FROM expenses WHERE user_id = ?", (session["user_id"],)
    ).fetchall()
    connection.close()

    marks = category_mark_map()
    available_months = sorted({e["expense_date"][:7] for e in expenses}, reverse=True)

    requested_month = request.args.get("month")
    if requested_month and requested_month in available_months:
        selected_month = requested_month
    elif available_months:
        current = date.today().strftime("%Y-%m")
        selected_month = current if current in available_months else available_months[0]
    else:
        selected_month = date.today().strftime("%Y-%m")

    month_expenses = [e for e in expenses if e["expense_date"].startswith(selected_month)]
    month_total = sum(e["amount"] for e in month_expenses)
    month_count = len(month_expenses)

    year, month_num = map(int, selected_month.split("-"))
    days_in_month = monthrange(year, month_num)[1]
    today = date.today()
    if selected_month == today.strftime("%Y-%m"):
        elapsed_days = today.day
    else:
        elapsed_days = days_in_month
    avg_per_day = month_total / elapsed_days if elapsed_days else 0

    month_breakdown = build_breakdown(month_expenses, marks)

    # ---- Last 6 months trend (including months with zero spend) ----
    trend = []
    cursor_year, cursor_month = year, month_num
    trend_months = []
    for _ in range(6):
        trend_months.append(f"{cursor_year:04d}-{cursor_month:02d}")
        cursor_month -= 1
        if cursor_month == 0:
            cursor_month = 12
            cursor_year -= 1
    trend_months.reverse()

    monthly_totals = {}
    for e in expenses:
        key = e["expense_date"][:7]
        monthly_totals[key] = monthly_totals.get(key, 0) + e["amount"]

    max_trend_value = max([monthly_totals.get(m, 0) for m in trend_months] + [1])
    for m in trend_months:
        amount = monthly_totals.get(m, 0)
        label_year, label_month = m.split("-")
        label = datetime(int(label_year), int(label_month), 1).strftime("%b")
        trend.append({
            "month": m,
            "label": label,
            "amount": amount,
            "height_percent": round((amount / max_trend_value) * 100, 1) if max_trend_value else 0
        })

    return render_template(
        "reports.html",
        selected_month=selected_month,
        month_label=datetime(year, month_num, 1).strftime("%B %Y"),
        available_months=available_months,
        month_total=month_total,
        month_count=month_count,
        avg_per_day=avg_per_day,
        month_breakdown=month_breakdown,
        trend=trend
    )


# =========================================
# ADMIN (visible only to the account marked is_admin)
# =========================================

@app.route("/admin")
@admin_required
def admin_page():
    connection = get_db_connection()
    users = connection.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()

    user_rows = []
    for u in users:
        stats = connection.execute("""
            SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total
            FROM expenses WHERE user_id = ?
        """, (u["id"],)).fetchone()

        user_rows.append({
            "id": u["id"],
            "username": u["username"],
            "is_admin": bool(u["is_admin"]),
            "created_at": u["created_at"][:10],
            "expense_count": stats["count"],
            "expense_total": stats["total"]
        })

    platform_total = connection.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses").fetchone()[0]
    platform_expense_count = connection.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
    connection.close()

    return render_template(
        "admin.html",
        user_rows=user_rows,
        user_count=len(user_rows),
        platform_total=platform_total,
        platform_expense_count=platform_expense_count
    )


@app.route("/admin/delete/<int:user_id>", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    if user_id == session["user_id"]:
        # Don't let an admin delete their own currently-signed-in account by accident.
        return redirect(url_for("admin_page"))

    connection = get_db_connection()
    connection.execute("DELETE FROM expenses WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    connection.commit()
    connection.close()

    return redirect(url_for("admin_page"))


@app.route("/admin/toggle-admin/<int:user_id>", methods=["POST"])
@admin_required
def admin_toggle_admin(user_id):
    if user_id == session["user_id"]:
        # Don't let an admin strip their own admin rights out from under themselves.
        return redirect(url_for("admin_page"))

    connection = get_db_connection()
    user = connection.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if user:
        new_value = 0 if user["is_admin"] else 1
        connection.execute("UPDATE users SET is_admin = ? WHERE id = ?", (new_value, user_id))
        connection.commit()
    connection.close()

    return redirect(url_for("admin_page"))


# =========================================
# HELPERS
# =========================================

def attach_mark(expense_row, marks):
    e = dict(expense_row)
    e["mark"] = marks.get(e["category"], "•")
    return e


def build_breakdown(expenses, marks):
    totals = {}
    for e in expenses:
        totals[e["category"]] = totals.get(e["category"], 0) + e["amount"]

    total = sum(totals.values())
    breakdown = []
    for category, amount in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        percent = round((amount / total) * 100, 1) if total > 0 else 0
        breakdown.append({
            "name": category,
            "amount": amount,
            "percent": percent,
            "mark": marks.get(category, "•")
        })
    return breakdown


@app.context_processor
def inject_user():
    return {
        "current_username": session.get("username"),
        "current_is_admin": session.get("is_admin", False)
    }


if __name__ == "__main__":
    create_database()
    app.run(debug=True)
