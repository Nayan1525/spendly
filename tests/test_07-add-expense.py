"""
Tests for Step 7 — Add Expense.

Spec: .claude/specs/07-add-expense.md

Covers:
- Unit tests for insert_expense() in database/queries.py
- Route tests for GET /expenses/add (auth guard, form rendering)
- Route tests for POST /expenses/add (auth guard, validation, DB side-effects)
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
def client(app):
    return app.test_client()


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


@pytest.fixture()
def auth_client(client):
    """
    A test client that is already logged in as a freshly registered user.
    Returns (client, user_id) so route tests can inspect the DB.
    """
    client.post(
        "/register",
        data={
            "name": "Route Test User",
            "email": "routetest@example.com",
            "password": "testpass123",
            "confirm_password": "testpass123",
        },
    )
    client.post(
        "/login",
        data={"email": "routetest@example.com", "password": "testpass123"},
    )
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM users WHERE email = ?", ("routetest@example.com",)
    ).fetchone()
    user_id = row["id"]
    conn.close()
    return client, user_id


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


# ---------------------------------------------------------------------------
# Route tests — GET /expenses/add
# ---------------------------------------------------------------------------

class TestGetAddExpense:
    """Tests for GET /expenses/add."""

    def test_get_unauthenticated_redirects_to_login(self, client):
        """GET /expenses/add without a session redirects to /login with 302."""
        resp = client.get("/expenses/add")
        assert resp.status_code == 302, (
            "Unauthenticated GET should redirect (302)"
        )
        assert "/login" in resp.headers["Location"], (
            "Redirect target should be /login"
        )

    def test_get_authenticated_returns_200(self, auth_client):
        """GET /expenses/add when logged in returns HTTP 200."""
        client, _ = auth_client
        resp = client.get("/expenses/add")
        assert resp.status_code == 200, (
            "Authenticated GET /expenses/add should return 200"
        )

    def test_get_authenticated_contains_form_with_post_method(self, auth_client):
        """
        The rendered page contains a <form element whose method attribute
        is POST (case-insensitive).
        """
        client, _ = auth_client
        resp = client.get("/expenses/add")
        html = resp.data.decode().lower()
        assert "<form" in html, "Response should contain a <form element"
        # method="post" or method="POST"
        assert 'method="post"' in html, (
            "Form must use method=POST as required by the spec"
        )

    def test_get_authenticated_contains_select_element(self, auth_client):
        """The rendered page contains a <select element for the category dropdown."""
        client, _ = auth_client
        resp = client.get("/expenses/add")
        assert b"<select" in resp.data, (
            "Response should contain a <select element for category"
        )

    @pytest.mark.parametrize("category", [
        "Food",
        "Transport",
        "Bills",
        "Health",
        "Entertainment",
        "Shopping",
        "Other",
    ])
    def test_get_authenticated_select_contains_all_7_categories(
        self, auth_client, category
    ):
        """
        All 7 fixed categories must appear as options in the category dropdown.
        """
        client, _ = auth_client
        resp = client.get("/expenses/add")
        assert category.encode() in resp.data, (
            f"Category '{category}' should be present as a dropdown option"
        )

    def test_get_authenticated_contains_amount_input(self, auth_client):
        """The form includes an amount input field."""
        client, _ = auth_client
        resp = client.get("/expenses/add")
        html = resp.data.decode()
        assert 'name="amount"' in html, (
            "Form should contain an input with name='amount'"
        )

    def test_get_authenticated_contains_date_input(self, auth_client):
        """The form includes a date input field."""
        client, _ = auth_client
        resp = client.get("/expenses/add")
        html = resp.data.decode()
        assert 'name="date"' in html, (
            "Form should contain an input with name='date'"
        )

    def test_get_authenticated_contains_description_input(self, auth_client):
        """The form includes a description input field."""
        client, _ = auth_client
        resp = client.get("/expenses/add")
        html = resp.data.decode()
        assert 'name="description"' in html, (
            "Form should contain an input/textarea with name='description'"
        )


# ---------------------------------------------------------------------------
# Route tests — POST /expenses/add
# ---------------------------------------------------------------------------

class TestPostAddExpense:
    """Tests for POST /expenses/add."""

    # --- Auth guard ---

    def test_post_unauthenticated_redirects_to_login(self, client):
        """POST /expenses/add without a session redirects to /login with 302."""
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        assert resp.status_code == 302, (
            "Unauthenticated POST should redirect (302)"
        )
        assert "/login" in resp.headers["Location"], (
            "Redirect target should be /login"
        )

    # --- Happy path: valid data ---

    def test_post_valid_data_redirects_to_profile(self, auth_client):
        """
        POST /expenses/add with valid data redirects to /profile (302).
        """
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        assert resp.status_code == 302, (
            "Valid POST should redirect (302)"
        )
        assert "/profile" in resp.headers["Location"], (
            "Successful submission should redirect to /profile"
        )

    def test_post_valid_data_row_exists_in_db(self, auth_client):
        """
        After a successful POST, the new expense row exists in the DB with
        the correct field values for the authenticated user.
        """
        client, user_id = auth_client
        client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM expenses WHERE user_id = ? AND date = ?",
            (user_id, "2026-03-20"),
        ).fetchone()
        conn.close()

        assert row is not None, "A new expense row should exist in the DB after valid POST"
        assert float(row["amount"]) == 50.0, "amount should be 50.0"
        assert row["category"] == "Food", "category should be 'Food'"
        assert row["date"] == "2026-03-20", "date should be '2026-03-20'"
        assert row["description"] == "Lunch", "description should be 'Lunch'"

    # --- No description: optional field ---

    def test_post_no_description_redirects_to_profile(self, auth_client):
        """
        POST /expenses/add without a description (optional field) still
        succeeds and redirects to /profile.
        """
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "120.0",
                "category": "Transport",
                "date": "2026-03-22",
                "description": "",
            },
        )
        assert resp.status_code == 302, (
            "POST without description should redirect (302)"
        )
        assert "/profile" in resp.headers["Location"], (
            "POST without description should redirect to /profile"
        )

    def test_post_no_description_stores_null_in_db(self, auth_client):
        """
        When description is omitted (empty string), the DB row has
        description = NULL, not an empty string or the string 'None'.
        """
        client, user_id = auth_client
        client.post(
            "/expenses/add",
            data={
                "amount": "120.0",
                "category": "Transport",
                "date": "2026-03-22",
                "description": "",
            },
        )

        conn = get_db()
        row = conn.execute(
            "SELECT description FROM expenses WHERE user_id = ? AND date = ?",
            (user_id, "2026-03-22"),
        ).fetchone()
        conn.close()

        assert row is not None, "Row should exist in the DB"
        assert row["description"] is None, (
            "description column should be NULL when form field is empty"
        )

    def test_post_whitespace_only_description_stores_null_in_db(self, auth_client):
        """
        A description consisting entirely of whitespace is stripped and stored
        as NULL (spec: strip whitespace; store None if blank).
        """
        client, user_id = auth_client
        client.post(
            "/expenses/add",
            data={
                "amount": "80.0",
                "category": "Other",
                "date": "2026-03-23",
                "description": "   ",
            },
        )

        conn = get_db()
        row = conn.execute(
            "SELECT description FROM expenses WHERE user_id = ? AND date = ?",
            (user_id, "2026-03-23"),
        ).fetchone()
        conn.close()

        assert row is not None, "Row should exist in the DB"
        assert row["description"] is None, (
            "Whitespace-only description should be stored as NULL"
        )

    # --- Validation: missing amount ---

    def test_post_missing_amount_returns_200(self, auth_client):
        """
        POST with an empty amount field re-renders the form (200), not a redirect.
        """
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        assert resp.status_code == 200, (
            "Missing amount should re-render form (200), not redirect"
        )

    def test_post_missing_amount_shows_error_message(self, auth_client):
        """
        POST with an empty amount field renders an error message in the response.
        """
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        html = resp.data.decode()
        # The spec requires an error message on validation failure.
        # We don't prescribe the exact wording — just that something error-like appears.
        assert any(
            word in html.lower()
            for word in ["error", "invalid", "required", "valid", "must"]
        ), "An error message should be present in the response when amount is missing"

    # --- Validation: amount = 0 ---

    def test_post_amount_zero_returns_200(self, auth_client):
        """
        POST with amount=0 re-renders the form (200). Amount must be > 0.
        """
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "0",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        assert resp.status_code == 200, (
            "amount=0 should re-render form (200), not redirect"
        )

    def test_post_amount_zero_shows_error_message(self, auth_client):
        """POST with amount=0 renders an error message."""
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "0",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        html = resp.data.decode()
        assert any(
            word in html.lower()
            for word in ["error", "invalid", "greater", "must", "positive"]
        ), "An error message should be present when amount is 0"

    def test_post_amount_zero_does_not_insert_row(self, auth_client):
        """No expense row should be inserted when amount=0."""
        client, user_id = auth_client
        client.post(
            "/expenses/add",
            data={
                "amount": "0",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Should not be inserted",
            },
        )
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM expenses WHERE user_id = ?", (user_id,)
        ).fetchone()["cnt"]
        conn.close()
        assert count == 0, "No row should be inserted when amount validation fails"

    # --- Validation: non-numeric amount ---

    def test_post_non_numeric_amount_returns_200(self, auth_client):
        """POST with a non-numeric amount re-renders the form (200)."""
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "abc",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        assert resp.status_code == 200, (
            "Non-numeric amount should re-render form (200), not redirect"
        )

    def test_post_non_numeric_amount_shows_error_message(self, auth_client):
        """POST with a non-numeric amount renders an error message."""
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "abc",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        html = resp.data.decode()
        assert any(
            word in html.lower()
            for word in ["error", "invalid", "number", "numeric", "valid", "must"]
        ), "An error message should be present when amount is non-numeric"

    def test_post_non_numeric_amount_does_not_insert_row(self, auth_client):
        """No expense row should be inserted when amount is non-numeric."""
        client, user_id = auth_client
        client.post(
            "/expenses/add",
            data={
                "amount": "not-a-number",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Should not be inserted",
            },
        )
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM expenses WHERE user_id = ?", (user_id,)
        ).fetchone()["cnt"]
        conn.close()
        assert count == 0, "No row should be inserted when amount is non-numeric"

    # --- Validation: invalid category ---

    def test_post_invalid_category_returns_200(self, auth_client):
        """POST with a category not in the fixed list re-renders the form (200)."""
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "NotACategory",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        assert resp.status_code == 200, (
            "Invalid category should re-render form (200), not redirect"
        )

    def test_post_invalid_category_shows_error_message(self, auth_client):
        """POST with an invalid category renders an error message."""
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "NotACategory",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        html = resp.data.decode()
        assert any(
            word in html.lower()
            for word in ["error", "invalid", "category", "valid", "select"]
        ), "An error message should be present when category is invalid"

    def test_post_invalid_category_does_not_insert_row(self, auth_client):
        """No expense row should be inserted when category is invalid."""
        client, user_id = auth_client
        client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "FakeCategory",
                "date": "2026-03-20",
                "description": "Should not be inserted",
            },
        )
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM expenses WHERE user_id = ?", (user_id,)
        ).fetchone()["cnt"]
        conn.close()
        assert count == 0, "No row should be inserted when category is invalid"

    def test_post_empty_category_returns_200(self, auth_client):
        """POST with an empty category string re-renders the form (200)."""
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        assert resp.status_code == 200, (
            "Empty category should re-render form (200), not redirect"
        )

    # --- Validation: invalid date ---

    def test_post_invalid_date_returns_200(self, auth_client):
        """POST with a non-YYYY-MM-DD date string re-renders the form (200)."""
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "Food",
                "date": "not-a-date",
                "description": "Lunch",
            },
        )
        assert resp.status_code == 200, (
            "Invalid date should re-render form (200), not redirect"
        )

    def test_post_invalid_date_shows_error_message(self, auth_client):
        """POST with an invalid date string renders an error message."""
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "Food",
                "date": "not-a-date",
                "description": "Lunch",
            },
        )
        html = resp.data.decode()
        assert any(
            word in html.lower()
            for word in ["error", "invalid", "date", "format", "valid", "must"]
        ), "An error message should be present when date is invalid"

    def test_post_invalid_date_does_not_insert_row(self, auth_client):
        """No expense row should be inserted when the date is invalid."""
        client, user_id = auth_client
        client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "Food",
                "date": "32-13-2026",
                "description": "Should not be inserted",
            },
        )
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM expenses WHERE user_id = ?", (user_id,)
        ).fetchone()["cnt"]
        conn.close()
        assert count == 0, "No row should be inserted when date is invalid"

    # --- Parametrized validation sweep ---

    @pytest.mark.parametrize("field,value,label", [
        ("amount",   "",             "empty amount"),
        ("amount",   "0",            "zero amount"),
        ("amount",   "-10",          "negative amount"),
        ("amount",   "abc",          "non-numeric amount"),
        ("category", "Invalid",      "invalid category"),
        ("category", "",             "empty category"),
        ("date",     "not-a-date",   "non-date string"),
        ("date",     "2026-13-01",   "month out of range"),
        ("date",     "2026-01-32",   "day out of range"),
        ("date",     "20260101",     "date without hyphens"),
        ("date",     "",             "empty date"),
    ])
    def test_post_invalid_field_returns_200(self, auth_client, field, value, label):
        """
        Parametrized: each invalid field value causes the form to be
        re-rendered (200) rather than redirected.
        """
        client, _ = auth_client
        data = {
            "amount":      "50.0",
            "category":    "Food",
            "date":        "2026-03-20",
            "description": "Lunch",
        }
        data[field] = value
        resp = client.post("/expenses/add", data=data)
        assert resp.status_code == 200, (
            f"Expected 200 (form re-render) for invalid input: {label!r}, got {resp.status_code}"
        )

    # --- Previous values are re-populated on error ---

    def test_post_error_repopulates_submitted_values(self, auth_client):
        """
        When validation fails, the form is re-rendered with the previously
        submitted values pre-filled so the user does not lose their input.
        """
        client, _ = auth_client
        resp = client.post(
            "/expenses/add",
            data={
                "amount":      "0",         # invalid — triggers error
                "category":    "Health",
                "date":        "2026-04-10",
                "description": "Doctor visit",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # The previously submitted description should appear in the re-rendered form.
        assert "Doctor visit" in html, (
            "Previously submitted description should be pre-filled on error re-render"
        )

    # --- DB isolation: multiple inserts belong to correct user ---

    def test_post_expense_belongs_to_logged_in_user(self, auth_client):
        """
        The inserted expense is associated with the authenticated user's id,
        not another user's id.
        """
        client, user_id = auth_client
        client.post(
            "/expenses/add",
            data={
                "amount":      "200.0",
                "category":    "Shopping",
                "date":        "2026-05-01",
                "description": "Ownership check",
            },
        )

        conn = get_db()
        row = conn.execute(
            "SELECT user_id FROM expenses WHERE description = ?",
            ("Ownership check",),
        ).fetchone()
        conn.close()

        assert row is not None, "Expense row should exist"
        assert row["user_id"] == user_id, (
            "Inserted expense should be owned by the authenticated user"
        )
