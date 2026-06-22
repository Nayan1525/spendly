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
    # Authentication guard — protected route
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # --- hardcoded context (real DB queries arrive in Step 5) ---
    user = {
        "name": session.get('user_name', 'Demo User'),
        "email": "demo@spendly.com",
        "member_since": "January 2026",
    }
    user["initials"] = "".join(part[0] for part in user["name"].split()[:2]).upper()

    stats = {
        "total_spent": "18,240",
        "transaction_count": 8,
        "top_category": "Bills",
    }

    transactions = [
        {"date": "18 Jun 2026", "description": "Groceries — BigBasket", "category": "Food",          "amount": "2,150"},
        {"date": "16 Jun 2026", "description": "Metro card recharge",   "category": "Transport",     "amount": "500"},
        {"date": "14 Jun 2026", "description": "Electricity bill",      "category": "Bills",         "amount": "3,400"},
        {"date": "12 Jun 2026", "description": "Pharmacy",              "category": "Health",        "amount": "780"},
        {"date": "10 Jun 2026", "description": "Movie night",           "category": "Entertainment", "amount": "640"},
        {"date": "06 Jun 2026", "description": "New headphones",        "category": "Shopping",      "amount": "2,499"},
    ]

    categories = [
        {"name": "Bills",         "total": "6,800", "percent": 38},
        {"name": "Food",          "total": "4,320", "percent": 24},
        {"name": "Shopping",      "total": "2,499", "percent": 14},
        {"name": "Transport",     "total": "1,800", "percent": 10},
        {"name": "Health",        "total": "1,540", "percent": 8},
        {"name": "Entertainment", "total": "1,281", "percent": 6},
    ]

    return render_template(
        "profile.html",
        user=user,
        stats=stats,
        transactions=transactions,
        categories=categories,
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
