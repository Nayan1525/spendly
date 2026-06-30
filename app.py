from flask import Flask, render_template, request, redirect, url_for, session
from database.db import get_db, init_db, seed_db
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "dev-secret-key"

with app.app_context():
    init_db()
    seed_db()


# ------------------------------------------------------------------ #
# Routes                                                              #
# ------------------------------------------------------------------ #

@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        # --- read form fields ---
        name             = request.form.get("name", "").strip()
        email            = request.form.get("email", "").strip().lower()
        password         = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # --- validate ---
        error = None
        if not name or len(name) < 2 or len(name) > 100:
            error = "Name must be between 2 and 100 characters."
        elif "@" not in email or "." not in email or len(email) > 120:
            error = "Please enter a valid email address."
        elif len(password) < 8 or len(password) > 128:
            error = "Password must be between 8 and 128 characters."
        elif password != confirm_password:
            error = "Passwords do not match."

        if error:
            return render_template("register.html", error=error,
                                   name=name, email=email)

        # --- insert ---
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                (name, email, generate_password_hash(password))
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.close()
            return render_template("register.html",
                                   error="Email already registered.",
                                   name=name, email=email)
        db.close()
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    # Redirect to dashboard if already logged in
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == "POST":
        # --- read form fields ---
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # --- validate against database ---
        error = None
        db = get_db()
        user = db.execute(
            "SELECT id, name, password_hash FROM users WHERE email = ?",
            (email,)
        ).fetchone()
        db.close()

        # Check if user exists and password matches
        if user is None or not check_password_hash(user['password_hash'], password):
            error = "Invalid email or password."

        if error:
            return render_template("login.html", error=error, email=email)

        # --- set session and redirect ---
        session['user_id'] = user['id']
        session['user_name'] = user['name']
        return redirect(url_for('dashboard'))

    return render_template("login.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ------------------------------------------------------------------ #
# Placeholder routes — students will implement these                  #
# ------------------------------------------------------------------ #

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('landing'))


@app.route("/dashboard")
def dashboard():
    # Redirect to login if not logged in
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_name = session.get('user_name', 'User')
    return render_template("dashboard.html", user_name=user_name)


@app.route("/profile")
def profile():
    # Step 5 — protected route, real DB queries
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from datetime import datetime

    user_id = session['user_id']
    today = datetime.today().date()
    today_str = today.strftime('%Y-%m-%d')

    # Step 6 — date filter: resolve active period and date bounds
    period = request.args.get('period', '')
    from_date_param = request.args.get('from_date', '').strip()
    to_date_param = request.args.get('to_date', '').strip()

    active_period = 'this_month'
    from_date_str = today.replace(day=1).strftime('%Y-%m-%d')
    to_date_str = today_str

    if from_date_param or to_date_param:
        try:
            from_date_str = datetime.strptime(from_date_param, '%Y-%m-%d').strftime('%Y-%m-%d') if from_date_param else '2000-01-01'
            to_date_str = datetime.strptime(to_date_param, '%Y-%m-%d').strftime('%Y-%m-%d') if to_date_param else today_str
            active_period = 'custom'
        except ValueError:
            active_period = 'this_month'
            from_date_str = today.replace(day=1).strftime('%Y-%m-%d')
            to_date_str = today_str
    elif period == 'last_3_months':
        active_period = 'last_3_months'
        m, y = today.month - 3, today.year
        if m <= 0:
            m += 12
            y -= 1
        from_date_str = today.replace(year=y, month=m, day=1).strftime('%Y-%m-%d')
    elif period == 'all':
        active_period = 'all'
        from_date_str = '2000-01-01'

    db = get_db()

    # 1. User info
    row = db.execute(
        "SELECT name, email, created_at FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    member_since = datetime.strptime(row['created_at'][:10], "%Y-%m-%d").strftime("%B %Y")
    user = {
        "name": row['name'],
        "email": row['email'],
        "member_since": member_since,
        "initials": "".join(part[0] for part in row['name'].split()[:2]).upper(),
    }

    # 2. Summary stats
    stats_row = db.execute(
        "SELECT SUM(amount) as total, COUNT(*) as count FROM expenses"
        " WHERE user_id = ? AND date >= ? AND date <= ?",
        (user_id, from_date_str, to_date_str)
    ).fetchone()
    total_spent = stats_row['total'] or 0
    transaction_count = stats_row['count'] or 0

    top_cat = db.execute(
        "SELECT category FROM expenses WHERE user_id = ? AND date >= ? AND date <= ?"
        " GROUP BY category ORDER BY SUM(amount) DESC LIMIT 1",
        (user_id, from_date_str, to_date_str)
    ).fetchone()
    stats = {
        "total_spent": f"{total_spent:,.0f}",
        "transaction_count": transaction_count,
        "top_category": top_cat['category'] if top_cat else "—",
    }

    # 3. Recent transactions (newest first, capped at 10)
    txn_rows = db.execute(
        "SELECT date, description, category, amount FROM expenses"
        " WHERE user_id = ? AND date >= ? AND date <= ? ORDER BY date DESC LIMIT 10",
        (user_id, from_date_str, to_date_str)
    ).fetchall()
    transactions = [
        {
            "date": datetime.strptime(t['date'], "%Y-%m-%d").strftime("%d %b %Y"),
            "description": t['description'] or "—",
            "category": t['category'],
            "amount": f"{t['amount']:,.0f}",
        }
        for t in txn_rows
    ]

    # 4. Category breakdown with computed percentages
    cat_rows = db.execute(
        "SELECT category, SUM(amount) as total FROM expenses"
        " WHERE user_id = ? AND date >= ? AND date <= ? GROUP BY category ORDER BY total DESC",
        (user_id, from_date_str, to_date_str)
    ).fetchall()
    categories = [
        {
            "name": c['category'],
            "total": f"{c['total']:,.0f}",
            "percent": round(c['total'] / total_spent * 100) if total_spent > 0 else 0,
        }
        for c in cat_rows
    ]

    db.close()

    return render_template(
        "profile.html",
        user=user,
        stats=stats,
        transactions=transactions,
        categories=categories,
        active_period=active_period,
        from_date=from_date_param,
        to_date=to_date_param,
    )


@app.route("/expenses/add")
def add_expense():
    return "Add expense — coming in Step 7"


@app.route("/expenses/<int:id>/edit")
def edit_expense(id):
    return "Edit expense — coming in Step 8"


@app.route("/expenses/<int:id>/delete")
def delete_expense(id):
    return "Delete expense — coming in Step 9"


if __name__ == "__main__":
    app.run(debug=True, port=5001)
