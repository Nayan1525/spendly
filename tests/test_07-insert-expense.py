"""
Tests for Step 7 — Add Expense.

Spec: .claude/specs/07-add-expense.md

Covers:
- Unit tests for insert_expense() in database/queries.py

Route-level tests for GET/POST /expenses/add used to live in this file, but
they asserted the pre-Step-8 contract (synchronous insert, 302 redirect to
/profile). Step 8 changed the route to publish to SQS and render
expense_queued.html instead, so those tests were removed — the route is now
fully covered by tests/test_08-sqs-expense-processing.py.
"""

import pytest
import database.db as db_module
from app import app as flask_app
from database.db import init_db, get_db
from database.queries import insert_expense
from werkzeug.security import generate_password_hash


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path, monkeypatch):
    """
    Flask test app backed by an isolated temporary SQLite database.

    Monkeypatches database.db.DB_PATH so every call to get_db() — from
    app.py and from test helpers — hits the same temp file. The real
    spendly.db is never touched.
    """
    db_file = str(tmp_path / "test_spendly.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_file)

    flask_app.config.update({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "WTF_CSRF_ENABLED": False,
    })

    with flask_app.app_context():
        init_db()
        yield flask_app


@pytest.fixture()
def db(app):
    """
    Open a direct DB connection to the test database, yield it, then close.
    Used exclusively by unit tests that call insert_expense() directly.
    """
    conn = get_db()
    yield conn
    conn.close()


@pytest.fixture()
def test_user_id(app):
    """
    Insert a test user directly into the DB and return their id.
    Used by unit tests that need a valid user_id for foreign-key constraints.
    """
    conn = get_db()
    conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("Unit Test User", "unittest@example.com", generate_password_hash("pass1234")),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM users WHERE email = ?", ("unittest@example.com",)
    ).fetchone()
    conn.close()
    return row["id"]


# ---------------------------------------------------------------------------
# Unit tests — insert_expense()
# ---------------------------------------------------------------------------

class TestInsertExpense:
    """Direct unit tests for the insert_expense() DB helper."""

    def test_insert_expense_valid_inputs_row_exists(self, db, test_user_id):
        """
        insert_expense with valid inputs persists exactly one row in the DB,
        and that row has the exact field values that were passed in.
        """
        insert_expense(
            db,
            test_user_id,
            amount=50.0,
            category="Food",
            date="2026-03-20",
            description="Lunch",
        )

        row = db.execute(
            "SELECT * FROM expenses WHERE user_id = ?", (test_user_id,)
        ).fetchone()

        assert row is not None, "insert_expense should create a row in the expenses table"
        assert row["user_id"] == test_user_id, "user_id should match the supplied value"
        assert row["amount"] == 50.0, "amount should be 50.0"
        assert row["category"] == "Food", "category should be 'Food'"
        assert row["date"] == "2026-03-20", "date should be '2026-03-20'"
        assert row["description"] == "Lunch", "description should be 'Lunch'"

    def test_insert_expense_description_none_stored_as_null(self, db, test_user_id):
        """
        Passing description=None to insert_expense stores NULL in the DB column,
        not the string 'None'.
        """
        insert_expense(
            db,
            test_user_id,
            amount=120.0,
            category="Transport",
            date="2026-03-21",
            description=None,
        )

        row = db.execute(
            "SELECT description FROM expenses WHERE user_id = ? AND date = ?",
            (test_user_id, "2026-03-21"),
        ).fetchone()

        assert row is not None, "Row should exist in the expenses table"
        assert row["description"] is None, (
            "description should be stored as NULL when None is passed"
        )

    def test_insert_expense_creates_exactly_one_row(self, db, test_user_id):
        """
        A single call to insert_expense creates exactly one new row.
        """
        before = db.execute(
            "SELECT COUNT(*) as cnt FROM expenses WHERE user_id = ?", (test_user_id,)
        ).fetchone()["cnt"]

        insert_expense(
            db,
            test_user_id,
            amount=75.5,
            category="Bills",
            date="2026-04-01",
            description="Electricity",
        )

        after = db.execute(
            "SELECT COUNT(*) as cnt FROM expenses WHERE user_id = ?", (test_user_id,)
        ).fetchone()["cnt"]

        assert after == before + 1, "Exactly one row should be inserted per call"

    def test_insert_expense_preserves_amount_precision(self, db, test_user_id):
        """
        insert_expense stores floating-point amounts without silent rounding
        (within normal floating-point tolerance).
        """
        insert_expense(
            db,
            test_user_id,
            amount=99.99,
            category="Shopping",
            date="2026-05-10",
            description="Shoes",
        )

        row = db.execute(
            "SELECT amount FROM expenses WHERE user_id = ? AND date = ?",
            (test_user_id, "2026-05-10"),
        ).fetchone()

        assert row is not None
        assert abs(row["amount"] - 99.99) < 0.001, (
            "Amount 99.99 should be stored with floating-point fidelity"
        )
