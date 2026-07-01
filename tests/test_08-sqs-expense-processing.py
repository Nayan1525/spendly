"""
Tests for Step 8 — SQS Expense Processing.

Spec: .claude/specs/08-sqs-expense-processing.md

Covers:
- Route: POST /expenses/add renders expense_queued.html on success (not a redirect)
- Route: POST /expenses/add calls publish_expense (mocked — never real AWS)
- Route: GET/POST /expenses/add auth guards redirect unauthenticated users to /login
- Route: POST /expenses/add validation failures re-render the form and do NOT call publish_expense
- Worker: process_message calls insert_expense for valid JSON bodies
- Worker: process_message raises ValueError on missing required keys
- Worker: process_message raises json.JSONDecodeError on non-JSON input
- Publisher: publish_expense raises RuntimeError when SQS_QUEUE_URL is not set
- Publisher: publish_expense sends correctly structured JSON via boto3 send_message
- Template: expense_queued.html contains the required success message and navigation links
"""

import json
import pytest
from unittest.mock import patch, MagicMock

import database.db as db_module
from app import app as flask_app
from database.db import init_db, get_db
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

    publish_expense is patched at the app.py import site so the test client
    never calls real AWS code.
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
def auth_client(client):
    """
    A test client already logged in as a freshly registered user.
    Returns (client, user_id) so route tests can inspect the DB.
    """
    client.post(
        "/register",
        data={
            "name": "SQS Test User",
            "email": "sqstest@example.com",
            "password": "testpass123",
            "confirm_password": "testpass123",
        },
    )
    client.post(
        "/login",
        data={"email": "sqstest@example.com", "password": "testpass123"},
    )
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM users WHERE email = ?", ("sqstest@example.com",)
    ).fetchone()
    user_id = row["id"]
    conn.close()
    return client, user_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_EXPENSE_FORM = {
    "amount": "250.0",
    "category": "Food",
    "date": "2026-06-30",
    "description": "Lunch at canteen",
}


# ---------------------------------------------------------------------------
# Route tests — Auth guards
# ---------------------------------------------------------------------------

class TestAuthGuards:
    """Unauthenticated requests to /expenses/add must redirect to /login."""

    def test_get_unauthenticated_redirects_to_login(self, client):
        """GET /expenses/add without a session redirects to /login with 302."""
        resp = client.get("/expenses/add")
        assert resp.status_code == 302, (
            "Unauthenticated GET /expenses/add should redirect (302)"
        )
        assert "/login" in resp.headers["Location"], (
            "Redirect target must be /login"
        )

    def test_post_unauthenticated_redirects_to_login(self, client):
        """POST /expenses/add without a session redirects to /login with 302."""
        resp = client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        assert resp.status_code == 302, (
            "Unauthenticated POST /expenses/add should redirect (302)"
        )
        assert "/login" in resp.headers["Location"], (
            "Redirect target must be /login"
        )

    def test_get_unauthenticated_does_not_render_form(self, client):
        """GET /expenses/add without a session must not render the expense form."""
        resp = client.get("/expenses/add")
        # After redirect the body is minimal — not a form page
        assert b'name="amount"' not in resp.data, (
            "Expense form must not be served to unauthenticated users"
        )


# ---------------------------------------------------------------------------
# Route tests — GET /expenses/add (authenticated)
# ---------------------------------------------------------------------------

class TestGetAddExpenseForm:
    """An authenticated GET renders the blank expense form."""

    def test_get_authenticated_returns_200(self, auth_client):
        """GET /expenses/add when logged in returns HTTP 200."""
        client, _ = auth_client
        resp = client.get("/expenses/add")
        assert resp.status_code == 200

    def test_get_authenticated_contains_all_required_fields(self, auth_client):
        """The rendered form has amount, category, date, and description inputs."""
        client, _ = auth_client
        html = client.get("/expenses/add").data.decode().lower()
        assert '<form' in html and 'method="post"' in html
        for field in ('name="amount"', 'name="date"', 'name="description"'):
            assert field in html, f"Form should contain an input with {field}"
        assert "<select" in html, "Form should contain a <select for category"

    @pytest.mark.parametrize("category", [
        "Food", "Transport", "Bills", "Health", "Entertainment", "Shopping", "Other",
    ])
    def test_get_authenticated_select_contains_all_categories(self, auth_client, category):
        """Every fixed category must appear as a dropdown option."""
        client, _ = auth_client
        resp = client.get("/expenses/add")
        assert category.encode() in resp.data


# ---------------------------------------------------------------------------
# Route tests — POST /expenses/add success path
# ---------------------------------------------------------------------------

class TestPostAddExpenseSuccess:
    """Valid POST /expenses/add publishes to SQS and renders expense_queued.html."""

    def test_valid_post_returns_200_not_redirect(self, auth_client):
        """
        POST /expenses/add with valid data returns HTTP 200 (renders a page),
        NOT a 302 redirect to /profile as it did in Step 7.
        """
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-001"
            resp = client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        assert resp.status_code == 200, (
            "Successful POST should render expense_queued.html (200), not redirect"
        )

    def test_valid_post_does_not_redirect_to_profile(self, auth_client):
        """
        POST /expenses/add with valid data must NOT redirect to /profile.
        Step 8 changed the success path from redirect to render.
        """
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-002"
            resp = client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        assert resp.status_code != 302, (
            "Step 8 success path renders a page — it must not be a redirect"
        )

    def test_valid_post_renders_expense_queued_template(self, auth_client):
        """
        The response body contains hallmark text from expense_queued.html,
        confirming the correct template was rendered.
        """
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-003"
            resp = client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        html = resp.data.decode()
        assert "queued" in html.lower(), (
            "Response body should contain 'queued' — from expense_queued.html"
        )

    def test_valid_post_response_contains_success_banner(self, auth_client):
        """
        The rendered expense_queued.html must include the required success
        message: 'Your expense has been queued for processing.'
        """
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-004"
            resp = client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        html = resp.data.decode()
        assert "queued for processing" in html, (
            "expense_queued.html must contain the success banner text "
            "'queued for processing'"
        )

    def test_valid_post_calls_publish_expense_once(self, auth_client):
        """
        publish_expense is called exactly once when the form data is valid.
        """
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-005"
            client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        mock_pub.assert_called_once(), (
            "publish_expense should be called exactly once on a valid POST"
        )

    def test_valid_post_calls_publish_expense_with_correct_user_id(self, auth_client):
        """
        publish_expense is called with the authenticated user's id as the
        first positional argument.
        """
        client, user_id = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-006"
            client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        args = mock_pub.call_args[0]
        assert args[0] == user_id, (
            f"publish_expense first arg should be user_id={user_id}, got {args[0]}"
        )

    def test_valid_post_calls_publish_expense_with_correct_amount(self, auth_client):
        """publish_expense receives the float-converted amount."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-007"
            client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        args = mock_pub.call_args[0]
        assert args[1] == 250.0, (
            f"publish_expense second arg (amount) should be 250.0, got {args[1]}"
        )

    def test_valid_post_calls_publish_expense_with_correct_category(self, auth_client):
        """publish_expense receives the correct category string."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-008"
            client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        args = mock_pub.call_args[0]
        assert args[2] == "Food", (
            f"publish_expense third arg (category) should be 'Food', got {args[2]}"
        )

    def test_valid_post_calls_publish_expense_with_correct_date(self, auth_client):
        """publish_expense receives the date string in YYYY-MM-DD format."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-009"
            client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        args = mock_pub.call_args[0]
        assert args[3] == "2026-06-30", (
            f"publish_expense fourth arg (date) should be '2026-06-30', got {args[3]}"
        )

    def test_valid_post_calls_publish_expense_with_description(self, auth_client):
        """publish_expense receives the description string when provided."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-010"
            client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        args = mock_pub.call_args[0]
        assert args[4] == "Lunch at canteen", (
            f"publish_expense fifth arg (description) should be 'Lunch at canteen', got {args[4]}"
        )

    def test_valid_post_empty_description_passes_none_to_publish(self, auth_client):
        """
        When description is an empty string, publish_expense receives None
        (the route strips whitespace and converts blank to None).
        """
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-011"
            client.post(
                "/expenses/add",
                data={
                    "amount": "100.0",
                    "category": "Transport",
                    "date": "2026-06-15",
                    "description": "",
                },
            )
        args = mock_pub.call_args[0]
        assert args[4] is None, (
            "Empty description should be passed as None to publish_expense"
        )


# ---------------------------------------------------------------------------
# Route tests — POST /expenses/add validation failures
# ---------------------------------------------------------------------------

class TestPostAddExpenseValidation:
    """
    Validation failures must re-render the form (200) and must NOT call
    publish_expense — no message should enter the queue for bad input.
    """

    def test_missing_amount_returns_200(self, auth_client):
        """Empty amount field re-renders form (200)."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            resp = client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "amount": ""},
            )
        assert resp.status_code == 200, "Missing amount must re-render form (200)"

    def test_missing_amount_does_not_call_publish_expense(self, auth_client):
        """publish_expense is not called when amount is missing."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            client.post("/expenses/add", data={**VALID_EXPENSE_FORM, "amount": ""})
        mock_pub.assert_not_called(), (
            "publish_expense must not be called when amount validation fails"
        )

    def test_zero_amount_returns_200(self, auth_client):
        """Amount of zero re-renders form (200)."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            resp = client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "amount": "0"},
            )
        assert resp.status_code == 200, "Zero amount must re-render form (200)"

    def test_zero_amount_does_not_call_publish_expense(self, auth_client):
        """publish_expense is not called when amount is zero."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            client.post("/expenses/add", data={**VALID_EXPENSE_FORM, "amount": "0"})
        mock_pub.assert_not_called()

    def test_negative_amount_returns_200(self, auth_client):
        """Negative amount re-renders form (200)."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            resp = client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "amount": "-50"},
            )
        assert resp.status_code == 200, "Negative amount must re-render form (200)"

    def test_negative_amount_does_not_call_publish_expense(self, auth_client):
        """publish_expense is not called when amount is negative."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            client.post("/expenses/add", data={**VALID_EXPENSE_FORM, "amount": "-50"})
        mock_pub.assert_not_called()

    def test_non_numeric_amount_returns_200(self, auth_client):
        """Non-numeric amount re-renders form (200)."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            resp = client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "amount": "abc"},
            )
        assert resp.status_code == 200, "Non-numeric amount must re-render form (200)"

    def test_non_numeric_amount_does_not_call_publish_expense(self, auth_client):
        """publish_expense is not called when amount is non-numeric."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            client.post("/expenses/add", data={**VALID_EXPENSE_FORM, "amount": "abc"})
        mock_pub.assert_not_called()

    def test_invalid_category_returns_200(self, auth_client):
        """A category not in the fixed list re-renders form (200)."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            resp = client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "category": "NotACategory"},
            )
        assert resp.status_code == 200, "Invalid category must re-render form (200)"

    def test_invalid_category_does_not_call_publish_expense(self, auth_client):
        """publish_expense is not called when category is invalid."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "category": "NotACategory"},
            )
        mock_pub.assert_not_called()

    def test_empty_category_returns_200(self, auth_client):
        """Empty category string re-renders form (200)."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            resp = client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "category": ""},
            )
        assert resp.status_code == 200, "Empty category must re-render form (200)"

    def test_empty_category_does_not_call_publish_expense(self, auth_client):
        """publish_expense is not called when category is empty."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            client.post("/expenses/add", data={**VALID_EXPENSE_FORM, "category": ""})
        mock_pub.assert_not_called()

    def test_invalid_date_returns_200(self, auth_client):
        """A date string that is not YYYY-MM-DD re-renders form (200)."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            resp = client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "date": "not-a-date"},
            )
        assert resp.status_code == 200, "Invalid date must re-render form (200)"

    def test_invalid_date_does_not_call_publish_expense(self, auth_client):
        """publish_expense is not called when date format is wrong."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "date": "not-a-date"},
            )
        mock_pub.assert_not_called()

    def test_empty_date_returns_200(self, auth_client):
        """Empty date field re-renders form (200)."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            resp = client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "date": ""},
            )
        assert resp.status_code == 200, "Empty date must re-render form (200)"

    def test_empty_date_does_not_call_publish_expense(self, auth_client):
        """publish_expense is not called when date is empty."""
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            client.post("/expenses/add", data={**VALID_EXPENSE_FORM, "date": ""})
        mock_pub.assert_not_called()

    def test_validation_error_response_contains_error_text(self, auth_client):
        """A validation failure re-renders the form with an error message."""
        client, _ = auth_client
        with patch("app.publish_expense"):
            resp = client.post(
                "/expenses/add",
                data={**VALID_EXPENSE_FORM, "amount": "0"},
            )
        html = resp.data.decode()
        assert any(
            word in html.lower()
            for word in ["error", "invalid", "greater", "must", "positive"]
        ), "Validation failure must render an error message in the form"

    def test_validation_error_repopulates_submitted_values(self, auth_client):
        """
        When validation fails, previously submitted values are pre-populated
        so the user does not lose their input.
        """
        client, _ = auth_client
        with patch("app.publish_expense"):
            resp = client.post(
                "/expenses/add",
                data={
                    "amount": "0",           # invalid — triggers re-render
                    "category": "Health",
                    "date": "2026-06-01",
                    "description": "Doctor visit SQS test",
                },
            )
        html = resp.data.decode()
        assert "Doctor visit SQS test" in html, (
            "Previously submitted description should be pre-filled on error re-render"
        )

    @pytest.mark.parametrize("field,value,label", [
        ("amount",   "",              "empty amount"),
        ("amount",   "0",             "zero amount"),
        ("amount",   "-1",            "negative amount"),
        ("amount",   "abc",           "non-numeric amount"),
        ("category", "FakeCategory",  "invalid category"),
        ("category", "",              "empty category"),
        ("date",     "not-a-date",    "non-date string"),
        ("date",     "2026-13-01",    "month out of range"),
        ("date",     "2026-01-32",    "day out of range"),
        ("date",     "20260630",      "date without hyphens"),
        ("date",     "",              "empty date"),
    ])
    def test_invalid_field_returns_200_and_no_publish(
        self, auth_client, field, value, label
    ):
        """
        Parametrized sweep: each invalid field returns 200 and does not call
        publish_expense.
        """
        client, _ = auth_client
        form_data = {**VALID_EXPENSE_FORM, field: value}
        with patch("app.publish_expense") as mock_pub:
            resp = client.post("/expenses/add", data=form_data)
        assert resp.status_code == 200, (
            f"Expected 200 (form re-render) for invalid input {label!r}, "
            f"got {resp.status_code}"
        )
        mock_pub.assert_not_called(), (
            f"publish_expense must not be called for invalid input {label!r}"
        )


# ---------------------------------------------------------------------------
# Template: expense_queued.html content
# ---------------------------------------------------------------------------

class TestExpenseQueuedTemplate:
    """
    Verify that expense_queued.html contains all required elements as
    specified in the Definition of Done.
    """

    def _get_queued_page(self, auth_client):
        client, _ = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "msg-id-tpl"
            resp = client.post("/expenses/add", data=VALID_EXPENSE_FORM)
        return resp

    def test_queued_page_contains_success_message(self, auth_client):
        """expense_queued.html contains the required success/queued message."""
        resp = self._get_queued_page(auth_client)
        html = resp.data.decode()
        assert "queued" in html.lower(), (
            "expense_queued.html must contain a 'queued' success message"
        )

    def test_queued_page_contains_link_to_profile(self, auth_client):
        """expense_queued.html contains a link back to /profile."""
        resp = self._get_queued_page(auth_client)
        html = resp.data.decode()
        assert "/profile" in html, (
            "expense_queued.html must include a link to /profile"
        )

    def test_queued_page_contains_link_to_add_expense(self, auth_client):
        """expense_queued.html contains a link to add another expense."""
        resp = self._get_queued_page(auth_client)
        html = resp.data.decode()
        assert "/expenses/add" in html, (
            "expense_queued.html must include a link to /expenses/add"
        )

    def test_queued_page_extends_base_html(self, auth_client):
        """
        expense_queued.html extends base.html — so the response contains
        shared navigation/structural elements from the base template.
        """
        resp = self._get_queued_page(auth_client)
        html = resp.data.decode()
        # base.html always includes the app name or a recognisable nav element.
        # We check for common base landmarks without prescribing exact copy.
        assert "<html" in html.lower() or "<!doctype" in html.lower(), (
            "Response should be a full HTML document (base.html extended)"
        )

    def test_queued_page_status_200(self, auth_client):
        """expense_queued.html is served as HTTP 200."""
        resp = self._get_queued_page(auth_client)
        assert resp.status_code == 200, (
            "expense_queued.html must be rendered with status 200"
        )


# ---------------------------------------------------------------------------
# Worker: process_message — happy path
# ---------------------------------------------------------------------------

class TestProcessMessageHappyPath:
    """process_message correctly calls insert_expense for valid message bodies."""

    def test_process_message_valid_body_calls_insert_expense(self, app):
        """
        A valid JSON body with all required keys causes insert_expense to be
        called with the parsed values.
        """
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":     1,
            "amount":      250.0,
            "category":    "Food",
            "date":        "2026-06-30",
            "description": "Lunch at canteen",
        })

        with patch("worker.sqs_worker.insert_expense") as mock_insert, \
             patch("worker.sqs_worker.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db

            process_message(body)

        mock_insert.assert_called_once(), (
            "insert_expense should be called exactly once for a valid message"
        )

    def test_process_message_passes_user_id_to_insert_expense(self, app):
        """process_message forwards user_id from the JSON payload."""
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":     42,
            "amount":      100.0,
            "category":    "Transport",
            "date":        "2026-06-15",
            "description": None,
        })

        with patch("worker.sqs_worker.insert_expense") as mock_insert, \
             patch("worker.sqs_worker.get_db") as mock_get_db:
            mock_get_db.return_value = MagicMock()
            process_message(body)

        call_args = mock_insert.call_args[0]
        assert call_args[1] == 42, (
            f"insert_expense should receive user_id=42, got {call_args[1]}"
        )

    def test_process_message_passes_amount_to_insert_expense(self, app):
        """process_message forwards the amount from the JSON payload."""
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":     1,
            "amount":      750.5,
            "category":    "Bills",
            "date":        "2026-06-20",
            "description": "Electricity",
        })

        with patch("worker.sqs_worker.insert_expense") as mock_insert, \
             patch("worker.sqs_worker.get_db") as mock_get_db:
            mock_get_db.return_value = MagicMock()
            process_message(body)

        call_args = mock_insert.call_args[0]
        assert call_args[2] == 750.5, (
            f"insert_expense should receive amount=750.5, got {call_args[2]}"
        )

    def test_process_message_passes_category_to_insert_expense(self, app):
        """process_message forwards the category from the JSON payload."""
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":     1,
            "amount":      300.0,
            "category":    "Health",
            "date":        "2026-06-10",
            "description": "Pharmacy",
        })

        with patch("worker.sqs_worker.insert_expense") as mock_insert, \
             patch("worker.sqs_worker.get_db") as mock_get_db:
            mock_get_db.return_value = MagicMock()
            process_message(body)

        call_args = mock_insert.call_args[0]
        assert call_args[3] == "Health", (
            f"insert_expense should receive category='Health', got {call_args[3]}"
        )

    def test_process_message_passes_date_to_insert_expense(self, app):
        """process_message forwards the date from the JSON payload."""
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":     1,
            "amount":      200.0,
            "category":    "Shopping",
            "date":        "2026-05-01",
            "description": "New shoes",
        })

        with patch("worker.sqs_worker.insert_expense") as mock_insert, \
             patch("worker.sqs_worker.get_db") as mock_get_db:
            mock_get_db.return_value = MagicMock()
            process_message(body)

        call_args = mock_insert.call_args[0]
        assert call_args[4] == "2026-05-01", (
            f"insert_expense should receive date='2026-05-01', got {call_args[4]}"
        )

    def test_process_message_passes_description_when_present(self, app):
        """process_message passes the description field when it is in the payload."""
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":     1,
            "amount":      50.0,
            "category":    "Other",
            "date":        "2026-06-01",
            "description": "Miscellaneous item",
        })

        with patch("worker.sqs_worker.insert_expense") as mock_insert, \
             patch("worker.sqs_worker.get_db") as mock_get_db:
            mock_get_db.return_value = MagicMock()
            process_message(body)

        call_args = mock_insert.call_args[0]
        assert call_args[5] == "Miscellaneous item", (
            f"insert_expense should receive description='Miscellaneous item', got {call_args[5]}"
        )

    def test_process_message_passes_none_when_description_absent(self, app):
        """
        When description is omitted from the JSON payload, process_message
        passes None to insert_expense (via dict.get default).
        """
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":  1,
            "amount":   80.0,
            "category": "Entertainment",
            "date":     "2026-06-25",
            # description intentionally omitted
        })

        with patch("worker.sqs_worker.insert_expense") as mock_insert, \
             patch("worker.sqs_worker.get_db") as mock_get_db:
            mock_get_db.return_value = MagicMock()
            process_message(body)

        call_args = mock_insert.call_args[0]
        assert call_args[5] is None, (
            "insert_expense should receive description=None when key is absent in payload"
        )

    def test_process_message_closes_db_connection(self, app):
        """
        process_message closes the DB connection in a finally block — even
        on success — to avoid connection leaks.
        """
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":     1,
            "amount":      99.0,
            "category":    "Food",
            "date":        "2026-06-30",
            "description": "Coffee",
        })

        with patch("worker.sqs_worker.insert_expense"), \
             patch("worker.sqs_worker.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            process_message(body)

        mock_db.close.assert_called_once(), (
            "DB connection must be closed after processing a message"
        )


# ---------------------------------------------------------------------------
# Worker: process_message — malformed messages
# ---------------------------------------------------------------------------

class TestProcessMessageMalformed:
    """
    Malformed messages must raise catchable exceptions so the worker can log
    and skip them without crashing the polling loop.
    """

    def test_missing_user_id_raises_value_error(self, app):
        """JSON missing 'user_id' causes process_message to raise ValueError."""
        from worker.sqs_worker import process_message

        body = json.dumps({
            # user_id intentionally missing
            "amount":      100.0,
            "category":    "Food",
            "date":        "2026-06-30",
            "description": "Missing user_id",
        })

        with pytest.raises(ValueError, match="user_id"), \
             patch("worker.sqs_worker.get_db"):
            process_message(body)

    def test_missing_amount_raises_value_error(self, app):
        """JSON missing 'amount' causes process_message to raise ValueError."""
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":     1,
            # amount intentionally missing
            "category":    "Food",
            "date":        "2026-06-30",
        })

        with pytest.raises(ValueError, match="amount"), \
             patch("worker.sqs_worker.get_db"):
            process_message(body)

    def test_missing_category_raises_value_error(self, app):
        """JSON missing 'category' causes process_message to raise ValueError."""
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":  1,
            "amount":   100.0,
            # category intentionally missing
            "date":     "2026-06-30",
        })

        with pytest.raises(ValueError, match="category"), \
             patch("worker.sqs_worker.get_db"):
            process_message(body)

    def test_missing_date_raises_value_error(self, app):
        """JSON missing 'date' causes process_message to raise ValueError."""
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id":  1,
            "amount":   100.0,
            "category": "Food",
            # date intentionally missing
        })

        with pytest.raises(ValueError, match="date"), \
             patch("worker.sqs_worker.get_db"):
            process_message(body)

    def test_invalid_json_raises_json_decode_error(self, app):
        """Non-JSON body causes process_message to raise json.JSONDecodeError."""
        import json as json_module
        from worker.sqs_worker import process_message

        with pytest.raises(json_module.JSONDecodeError):
            process_message("this is not json at all {{{")

    def test_empty_string_body_raises_json_decode_error(self, app):
        """An empty string body raises json.JSONDecodeError."""
        import json as json_module
        from worker.sqs_worker import process_message

        with pytest.raises(json_module.JSONDecodeError):
            process_message("")

    def test_value_error_is_catchable_not_system_exit(self, app):
        """
        ValueError from a missing key is a normal catchable exception — it
        must not be SystemExit or BaseException, so the worker loop survives.
        """
        from worker.sqs_worker import process_message

        body = json.dumps({"amount": 50.0})  # missing user_id, category, date

        caught = None
        try:
            with patch("worker.sqs_worker.get_db"):
                process_message(body)
        except ValueError as exc:
            caught = exc
        except SystemExit:
            pytest.fail("process_message raised SystemExit — worker would crash")
        except BaseException as exc:
            pytest.fail(
                f"process_message raised {type(exc).__name__} instead of ValueError"
            )

        assert caught is not None, "ValueError should have been raised for missing keys"

    def test_json_decode_error_is_catchable(self, app):
        """
        json.JSONDecodeError from a garbled body is a normal catchable exception.
        """
        import json as json_module
        from worker.sqs_worker import process_message

        caught = None
        try:
            process_message("}{not valid}")
        except json_module.JSONDecodeError as exc:
            caught = exc
        except SystemExit:
            pytest.fail("process_message raised SystemExit — worker would crash")
        except BaseException as exc:
            # json.JSONDecodeError is a subclass of ValueError, so this should
            # not be reached for any other exception type.
            if not isinstance(exc, json_module.JSONDecodeError):
                pytest.fail(
                    f"Unexpected exception type {type(exc).__name__}"
                )

        assert caught is not None, (
            "json.JSONDecodeError should have been raised for invalid JSON"
        )

    def test_missing_all_required_keys_raises_value_error(self, app):
        """JSON with no required keys at all raises ValueError."""
        from worker.sqs_worker import process_message

        body = json.dumps({"description": "only description present"})

        with pytest.raises(ValueError), \
             patch("worker.sqs_worker.get_db"):
            process_message(body)

    def test_malformed_message_does_not_call_insert_expense(self, app):
        """
        When a message is missing required keys, insert_expense must NOT be
        called — data integrity is preserved.
        """
        from worker.sqs_worker import process_message

        body = json.dumps({
            "user_id": 1,
            # amount, category, date missing
        })

        with patch("worker.sqs_worker.insert_expense") as mock_insert, \
             patch("worker.sqs_worker.get_db"):
            try:
                process_message(body)
            except ValueError:
                pass

        mock_insert.assert_not_called(), (
            "insert_expense must not be called for a message with missing required keys"
        )


# ---------------------------------------------------------------------------
# Publisher: publish_expense — missing SQS_QUEUE_URL
# ---------------------------------------------------------------------------

class TestPublishExpenseMissingQueueUrl:
    """publish_expense raises RuntimeError when SQS_QUEUE_URL is not set."""

    def test_missing_sqs_queue_url_raises_runtime_error(self):
        """
        If SQS_QUEUE_URL is None (env var not set), publish_expense raises
        RuntimeError with a clear message instead of a cryptic boto3 traceback.
        """
        import worker.sqs_publisher as publisher_module

        with patch.object(publisher_module, "SQS_QUEUE_URL", None):
            with pytest.raises(RuntimeError):
                publisher_module.publish_expense(
                    user_id=1,
                    amount=100.0,
                    category="Food",
                    date="2026-06-30",
                    description="Test",
                )

    def test_empty_sqs_queue_url_raises_runtime_error(self):
        """
        An empty string SQS_QUEUE_URL (e.g. SQS_QUEUE_URL='') also raises
        RuntimeError — falsy check covers both None and empty string.
        """
        import worker.sqs_publisher as publisher_module

        with patch.object(publisher_module, "SQS_QUEUE_URL", ""):
            with pytest.raises(RuntimeError):
                publisher_module.publish_expense(
                    user_id=1,
                    amount=100.0,
                    category="Food",
                    date="2026-06-30",
                    description="Test",
                )

    def test_runtime_error_message_mentions_sqs_queue_url(self):
        """
        The RuntimeError message should name SQS_QUEUE_URL so the operator
        knows which variable to set.
        """
        import worker.sqs_publisher as publisher_module

        with patch.object(publisher_module, "SQS_QUEUE_URL", None):
            with pytest.raises(RuntimeError, match="SQS_QUEUE_URL"):
                publisher_module.publish_expense(
                    user_id=1,
                    amount=50.0,
                    category="Other",
                    date="2026-06-01",
                    description=None,
                )

    def test_missing_queue_url_does_not_call_boto3(self):
        """
        When SQS_QUEUE_URL is missing, boto3.client must never be called —
        the guard check must be evaluated before any AWS SDK interaction.
        """
        import worker.sqs_publisher as publisher_module

        with patch.object(publisher_module, "SQS_QUEUE_URL", None), \
             patch("worker.sqs_publisher.boto3") as mock_boto3:
            try:
                publisher_module.publish_expense(1, 50.0, "Food", "2026-06-30", None)
            except RuntimeError:
                pass
        mock_boto3.client.assert_not_called(), (
            "boto3.client must not be invoked when SQS_QUEUE_URL is absent"
        )


# ---------------------------------------------------------------------------
# Publisher: publish_expense — correct payload construction
# ---------------------------------------------------------------------------

class TestPublishExpensePayload:
    """publish_expense sends a correctly structured JSON message via boto3."""

    QUEUE_URL = "https://sqs.ap-south-1.amazonaws.com/123456789012/spendly-expenses"

    def _call_publish(self, mock_sqs_client, **kwargs):
        """Helper: patch boto3 and SQS_QUEUE_URL, then call publish_expense."""
        import worker.sqs_publisher as publisher_module

        mock_boto3_client = MagicMock()
        mock_boto3_client.send_message.return_value = {"MessageId": "mock-msg-id"}

        with patch.object(publisher_module, "SQS_QUEUE_URL", self.QUEUE_URL), \
             patch("worker.sqs_publisher.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_boto3_client
            result = publisher_module.publish_expense(**kwargs)

        return result, mock_boto3_client

    def test_send_message_is_called_once(self):
        """boto3 SQS client's send_message is called exactly once per publish."""
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=1,
            amount=250.0,
            category="Food",
            date="2026-06-30",
            description="Lunch",
        )
        mock_client.send_message.assert_called_once(), (
            "send_message should be called exactly once"
        )

    def test_send_message_uses_correct_queue_url(self):
        """send_message is called with the QueueUrl from SQS_QUEUE_URL env var."""
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=1,
            amount=250.0,
            category="Food",
            date="2026-06-30",
            description="Lunch",
        )
        call_kwargs = mock_client.send_message.call_args[1]
        assert call_kwargs["QueueUrl"] == self.QUEUE_URL, (
            f"send_message QueueUrl should be {self.QUEUE_URL!r}, "
            f"got {call_kwargs.get('QueueUrl')!r}"
        )

    def test_message_body_is_valid_json(self):
        """The MessageBody passed to send_message is valid JSON."""
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=1,
            amount=250.0,
            category="Food",
            date="2026-06-30",
            description="Lunch",
        )
        call_kwargs = mock_client.send_message.call_args[1]
        raw_body = call_kwargs["MessageBody"]
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            pytest.fail(f"MessageBody is not valid JSON: {raw_body!r}")
        assert isinstance(parsed, dict), "Parsed MessageBody should be a JSON object"

    def test_message_body_contains_user_id(self):
        """The MessageBody JSON contains the correct user_id."""
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=7,
            amount=100.0,
            category="Transport",
            date="2026-06-15",
            description="Auto",
        )
        call_kwargs = mock_client.send_message.call_args[1]
        payload = json.loads(call_kwargs["MessageBody"])
        assert payload["user_id"] == 7, (
            f"MessageBody user_id should be 7, got {payload.get('user_id')}"
        )

    def test_message_body_contains_amount(self):
        """The MessageBody JSON contains the correct amount."""
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=1,
            amount=999.99,
            category="Health",
            date="2026-06-10",
            description="Medicine",
        )
        call_kwargs = mock_client.send_message.call_args[1]
        payload = json.loads(call_kwargs["MessageBody"])
        assert abs(payload["amount"] - 999.99) < 0.001, (
            f"MessageBody amount should be ~999.99, got {payload.get('amount')}"
        )

    def test_message_body_contains_category(self):
        """The MessageBody JSON contains the correct category."""
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=1,
            amount=500.0,
            category="Entertainment",
            date="2026-06-08",
            description="Movie",
        )
        call_kwargs = mock_client.send_message.call_args[1]
        payload = json.loads(call_kwargs["MessageBody"])
        assert payload["category"] == "Entertainment", (
            f"MessageBody category should be 'Entertainment', got {payload.get('category')}"
        )

    def test_message_body_contains_date(self):
        """The MessageBody JSON contains the correct date string."""
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=1,
            amount=200.0,
            category="Shopping",
            date="2026-05-20",
            description="Clothes",
        )
        call_kwargs = mock_client.send_message.call_args[1]
        payload = json.loads(call_kwargs["MessageBody"])
        assert payload["date"] == "2026-05-20", (
            f"MessageBody date should be '2026-05-20', got {payload.get('date')}"
        )

    def test_message_body_contains_description(self):
        """The MessageBody JSON contains the description when provided."""
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=1,
            amount=75.0,
            category="Food",
            date="2026-06-30",
            description="Dinner with family",
        )
        call_kwargs = mock_client.send_message.call_args[1]
        payload = json.loads(call_kwargs["MessageBody"])
        assert payload["description"] == "Dinner with family", (
            f"MessageBody description should be 'Dinner with family', got {payload.get('description')}"
        )

    def test_message_body_contains_description_none(self):
        """The MessageBody JSON contains description key even when value is None."""
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=1,
            amount=75.0,
            category="Food",
            date="2026-06-30",
            description=None,
        )
        call_kwargs = mock_client.send_message.call_args[1]
        payload = json.loads(call_kwargs["MessageBody"])
        assert "description" in payload, (
            "MessageBody should always include the 'description' key"
        )
        assert payload["description"] is None, (
            "MessageBody description should be null when None is passed"
        )

    def test_message_body_contains_all_five_fields(self):
        """
        The MessageBody JSON object contains exactly the five required fields:
        user_id, amount, category, date, description.
        """
        _, mock_client = self._call_publish(
            mock_sqs_client=None,
            user_id=3,
            amount=350.0,
            category="Bills",
            date="2026-06-03",
            description="Electricity bill",
        )
        call_kwargs = mock_client.send_message.call_args[1]
        payload = json.loads(call_kwargs["MessageBody"])
        required_keys = {"user_id", "amount", "category", "date", "description"}
        missing = required_keys - payload.keys()
        assert not missing, (
            f"MessageBody is missing required keys: {missing}"
        )

    def test_publish_expense_returns_message_id(self):
        """publish_expense returns the MessageId string from the SQS response."""
        import worker.sqs_publisher as publisher_module

        mock_boto3_client = MagicMock()
        mock_boto3_client.send_message.return_value = {"MessageId": "returned-msg-id"}

        with patch.object(publisher_module, "SQS_QUEUE_URL", self.QUEUE_URL), \
             patch("worker.sqs_publisher.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_boto3_client
            result = publisher_module.publish_expense(
                user_id=1,
                amount=100.0,
                category="Food",
                date="2026-06-30",
                description="Test",
            )

        assert result == "returned-msg-id", (
            f"publish_expense should return the MessageId, got {result!r}"
        )

    def test_boto3_client_created_with_correct_region(self):
        """
        boto3.client is instantiated as 'sqs' and the region_name comes from
        the AWS_REGION config — not hardcoded.
        """
        import worker.sqs_publisher as publisher_module

        mock_boto3_client = MagicMock()
        mock_boto3_client.send_message.return_value = {"MessageId": "region-check-id"}

        with patch.object(publisher_module, "SQS_QUEUE_URL", self.QUEUE_URL), \
             patch("worker.sqs_publisher.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_boto3_client
            publisher_module.publish_expense(
                user_id=1,
                amount=50.0,
                category="Other",
                date="2026-06-01",
                description=None,
            )

        mock_boto3.client.assert_called_once()
        call_args = mock_boto3.client.call_args
        # First positional arg should be "sqs"
        assert call_args[0][0] == "sqs", (
            "boto3.client should be called with 'sqs' as the service name"
        )


# ---------------------------------------------------------------------------
# End-to-end: POST /expenses/add -> published message -> worker -> DB row
# ---------------------------------------------------------------------------

class TestEndToEndPipeline:
    """
    Every other test in this file mocks either the SQS boundary
    (route tests, via patch("app.publish_expense")) or the DB boundary
    (worker tests, via patch("worker.sqs_worker.insert_expense"/"get_db")).
    That leaves no test proving the two halves actually connect: that the
    message the route would publish is one the worker can consume into a
    row owned by the right user.

    These tests still mock the network hop (no real SQS) but run the real
    ExpenseMessage validation, process_message, and insert_expense against
    the temp SQLite DB from the `app` fixture — the same DB the route wrote
    the user into via `auth_client`.
    """

    def _submit_and_process(self, auth_client, form_data):
        """POST the form, capture what would have been published, then run
        that exact message through the real worker pipeline."""
        from worker.schemas import ExpenseMessage
        from worker.sqs_worker import process_message

        client, user_id = auth_client
        with patch("app.publish_expense") as mock_pub:
            mock_pub.return_value = "fake-message-id-e2e"
            resp = client.post("/expenses/add", data=form_data)

        published_user_id, amount, category, date, description = mock_pub.call_args[0]
        message = ExpenseMessage(
            user_id=published_user_id,
            amount=amount,
            category=category,
            date=date,
            description=description,
        )
        process_message(message.model_dump_json())
        return resp, user_id

    def test_submitted_expense_lands_in_db_owned_by_submitting_user(self, auth_client):
        """The row the worker inserts belongs to the user who submitted the form."""
        _, user_id = self._submit_and_process(auth_client, VALID_EXPENSE_FORM)

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM expenses WHERE user_id = ? AND date = ?",
            (user_id, VALID_EXPENSE_FORM["date"]),
        ).fetchone()
        conn.close()

        assert row is not None, "Row should exist in the DB after the full pipeline runs"
        assert row["user_id"] == user_id, (
            "Expense must be owned by the user who submitted it, not another user"
        )

    def test_submitted_expense_fields_match_form_input(self, auth_client):
        """Amount, category, and description survive the full round trip unchanged."""
        _, user_id = self._submit_and_process(auth_client, VALID_EXPENSE_FORM)

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM expenses WHERE user_id = ? AND date = ?",
            (user_id, VALID_EXPENSE_FORM["date"]),
        ).fetchone()
        conn.close()

        assert row["amount"] == float(VALID_EXPENSE_FORM["amount"])
        assert row["category"] == VALID_EXPENSE_FORM["category"]
        assert row["description"] == VALID_EXPENSE_FORM["description"]

    def test_submitted_expense_with_no_description_stores_null_end_to_end(self, auth_client):
        """An empty description form field ends up as NULL on the real row, not 'None'."""
        form = {**VALID_EXPENSE_FORM, "description": ""}
        _, user_id = self._submit_and_process(auth_client, form)

        conn = get_db()
        row = conn.execute(
            "SELECT description FROM expenses WHERE user_id = ? AND date = ?",
            (user_id, VALID_EXPENSE_FORM["date"]),
        ).fetchone()
        conn.close()

        assert row["description"] is None, (
            "Empty description should be stored as NULL after the full pipeline runs"
        )

    def test_two_users_submitting_do_not_cross_contaminate_rows(self, auth_client):
        """A second user's submission must not be attributed to the first user."""
        client, first_user_id = auth_client
        self._submit_and_process(auth_client, VALID_EXPENSE_FORM)

        client.get("/logout")
        client.post(
            "/register",
            data={
                "name": "Second User",
                "email": "second-e2e@example.com",
                "password": "testpass123",
                "confirm_password": "testpass123",
            },
        )
        client.post(
            "/login",
            data={"email": "second-e2e@example.com", "password": "testpass123"},
        )
        conn = get_db()
        second_user_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("second-e2e@example.com",)
        ).fetchone()["id"]
        conn.close()

        second_form = {**VALID_EXPENSE_FORM, "description": "Second user's lunch"}
        self._submit_and_process((client, second_user_id), second_form)

        conn = get_db()
        second_row = conn.execute(
            "SELECT user_id FROM expenses WHERE description = ?",
            ("Second user's lunch",),
        ).fetchone()
        conn.close()

        assert second_row is not None, "Second user's expense should be in the DB"
        assert second_row["user_id"] != first_user_id, (
            "Second user's expense must not be attributed to the first user"
        )
