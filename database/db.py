import sqlite3
import os
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "spendly.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            email         TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            amount      REAL    NOT NULL,
            category    TEXT    NOT NULL,
            date        TEXT    NOT NULL,
            description TEXT,
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS expense_categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            description TEXT,
            color       TEXT,
            created_at  TEXT    DEFAULT (datetime('now'))
        );
    """)
    db.commit()
    db.close()


def seed_db():
    db = get_db()

    count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count > 0:
        db.close()
        return

    db.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("Demo User", "demo@spendly.com", generate_password_hash("demo123")),
    )
    db.commit()

    user_id = db.execute("SELECT id FROM users WHERE email = ?", ("demo@spendly.com",)).fetchone()[0]

    expenses = [
        (user_id, 320.00,  "Food",          "2026-06-01", "Grocery run"),
        (user_id, 150.00,  "Transport",     "2026-06-02", "Auto rickshaw"),
        (user_id, 1200.00, "Bills",         "2026-06-03", "Electricity bill"),
        (user_id, 500.00,  "Health",        "2026-06-05", "Pharmacy"),
        (user_id, 800.00,  "Entertainment", "2026-06-08", "Movie tickets"),
        (user_id, 2500.00, "Shopping",      "2026-06-10", "New shoes"),
        (user_id, 90.00,   "Other",         "2026-06-12", "Miscellaneous"),
        (user_id, 450.00,  "Food",          "2026-06-15", "Restaurant dinner"),
    ]

    db.executemany(
        "INSERT INTO expenses (user_id, amount, category, date, description) VALUES (?, ?, ?, ?, ?)",
        expenses,
    )
    db.commit()
    db.close()
