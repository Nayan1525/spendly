from flask import Flask, render_template, request, redirect, url_for, session
from database.db import get_db, init_db, seed_db
import sqlite3
from werkzeug.security import generate_password_hash

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


@app.route("/login")
def login():
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
    return "Logout — coming in Step 3"


@app.route("/profile")
def profile():
    return "Profile page — coming in Step 4"


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
