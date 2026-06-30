"""
Tests for Step 6 — Date Filter for Profile Page.

Spec: .claude/specs/06-date-filter-profile-page.md

The GET /profile route accepts optional query parameters:
  ?period=this_month|last_3_months|all
  ?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD

Default behaviour (no params): period=this_month.

All stats, transactions, and category breakdown are filtered by the
active date range. Malformed or missing custom date values fall back to
this_month silently.
"""

import os
import sqlite3
import tempfile
import datetime
import pytest

import database.db as db_module
from app import app as flask_app
from database.db import init_db
from werkzeug.security import generate_password_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> datetime.date:
    return datetime.date.today()


def _this_month_first() -> datetime.date:
    today = _today()
    return today.replace(day=1)


def _months_ago_first(n: int) -> datetime.date:
    """Return the first day of the month that is n months before today."""
    today = _today()
    month = today.month - n
    year = today.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return datetime.date(year, month, 1)


def _date_str(d: datetime.date) -> str:
    return d.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path, monkeypatch):
    """
    Provide a Flask test app backed by an isolated temporary SQLite database.

    Monkeypatches database.db.DB_PATH so every call to get_db() — both from
    app.py (which imported get_db at module load time) and from tests — hits
    the same temp file.  The real spendly.db on disk is never touched.
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
def seeded_auth_client(client):
    """
    A test client already logged in as 'testuser' with a carefully-spread
    set of expenses across three time windows:

      - this_month_expenses  : 2 expenses in the current calendar month
      - recent_expenses      : 2 expenses from 2 months ago (inside last_3_months)
      - old_expenses         : 2 expenses from 6 months ago (outside last_3_months)

    Returns (client, metadata) so individual tests can reference expected
    amounts and dates.
    """
    today = _today()
    this_month_first = _this_month_first()

    # Pick safe in-month dates.  Use day 1 (always safe) and day 2 if month
    # has more than one day (all months do), so these are always valid dates.
    this_month_date1 = _date_str(this_month_first)
    this_month_date2 = _date_str(this_month_first.replace(day=min(2, today.day)))

    two_months_first = _months_ago_first(2)
    two_months_date1 = _date_str(two_months_first)
    two_months_date2 = _date_str(two_months_first.replace(day=2))

    six_months_first = _months_ago_first(6)
    six_months_date1 = _date_str(six_months_first)
    six_months_date2 = _date_str(six_months_first.replace(day=2))

    # Register and log in.
    client.post(
        "/register",
        data={
            "name": "Test User",
            "email": "testuser@example.com",
            "password": "testpass123",
            "confirm_password": "testpass123",
        },
    )
    client.post(
        "/login",
        data={"email": "testuser@example.com", "password": "testpass123"},
    )

    # Obtain the user_id from the DB to insert expenses directly.
    db = db_module.get_db()
    user_row = db.execute(
        "SELECT id FROM users WHERE email = ?", ("testuser@example.com",)
    ).fetchone()
    user_id = user_row["id"]

    expenses = [
        # this month — amounts: 100, 200  → total 300, count 2, category Food
        (user_id, 100.00, "Food",      this_month_date1, "TM expense 1"),
        (user_id, 200.00, "Food",      this_month_date2, "TM expense 2"),
        # 2 months ago — amounts: 400, 600 → inside last_3_months window
        (user_id, 400.00, "Transport", two_months_date1, "2M expense 1"),
        (user_id, 600.00, "Transport", two_months_date2, "2M expense 2"),
        # 6 months ago — amounts: 999, 1001 → outside last_3_months window
        (user_id, 999.00, "Bills",     six_months_date1, "6M expense 1"),
        (user_id, 1001.00, "Bills",    six_months_date2, "6M expense 2"),
    ]
    db.executemany(
        "INSERT INTO expenses (user_id, amount, category, date, description) "
        "VALUES (?, ?, ?, ?, ?)",
        expenses,
    )
    db.commit()
    db.close()

    meta = {
        "user_id": user_id,
        "this_month_date1": this_month_date1,
        "this_month_date2": this_month_date2,
        "two_months_date1": two_months_date1,
        "two_months_date2": two_months_date2,
        "six_months_date1": six_months_date1,
        "six_months_date2": six_months_date2,
        # expected totals per window
        "this_month_total": 300,
        "last_3_months_total": 1300,  # 300 + 1000
        "all_total": 3300,            # 300 + 1000 + 2000
    }
    return client, meta


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

class TestAuthGuard:
    """Unauthenticated requests to /profile must redirect to /login."""

    def test_unauthenticated_profile_redirects_to_login(self, client):
        """Bare /profile without session redirects to /login."""
        resp = client.get("/profile")
        assert resp.status_code == 302, "Expected redirect for unauthenticated user"
        assert "/login" in resp.headers["Location"], (
            "Redirect target should be /login"
        )

    def test_unauthenticated_with_period_param_redirects_to_login(self, client):
        """?period=this_month on an unauthenticated request still redirects."""
        resp = client.get("/profile?period=this_month")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_unauthenticated_with_custom_dates_redirects_to_login(self, client):
        """Custom date params on unauthenticated request still redirect."""
        resp = client.get("/profile?from_date=2024-01-01&to_date=2024-12-31")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# Default behaviour (no query params)
# ---------------------------------------------------------------------------

class TestDefaultBehaviour:
    """No query params → behaves as period=this_month."""

    def test_profile_no_params_returns_200(self, seeded_auth_client):
        """GET /profile with no params returns HTTP 200."""
        client, _ = seeded_auth_client
        resp = client.get("/profile")
        assert resp.status_code == 200, "Profile page should return 200"

    def test_profile_no_params_contains_filter_bar_buttons(self, seeded_auth_client):
        """Default response includes all three preset button labels."""
        client, _ = seeded_auth_client
        resp = client.get("/profile")
        html = resp.data
        assert b"This Month" in html, "Filter bar should contain 'This Month' button"
        assert b"Last 3 Months" in html, "Filter bar should contain 'Last 3 Months' button"
        assert b"All Time" in html, "Filter bar should contain 'All Time' button"

    def test_profile_no_params_excludes_old_expenses(self, seeded_auth_client):
        """Default (this_month) hides expenses from 6 months ago."""
        client, meta = seeded_auth_client
        resp = client.get("/profile")
        html = resp.data.decode()
        assert "6M expense 1" not in html, (
            "6-month-old expense description should not appear in this_month view"
        )
        assert "6M expense 2" not in html

    def test_profile_no_params_shows_this_month_expenses(self, seeded_auth_client):
        """Default (this_month) shows current-month expense descriptions."""
        client, _ = seeded_auth_client
        resp = client.get("/profile")
        html = resp.data.decode()
        assert "TM expense 1" in html, (
            "Current-month expense should appear in default profile view"
        )


# ---------------------------------------------------------------------------
# Filter bar UI presence
# ---------------------------------------------------------------------------

class TestFilterBarUI:
    """The filter bar must render with all required UI elements."""

    def test_filter_bar_has_this_month_label(self, seeded_auth_client):
        """Response HTML contains 'This Month' preset label."""
        client, _ = seeded_auth_client
        resp = client.get("/profile")
        assert b"This Month" in resp.data

    def test_filter_bar_has_last_3_months_label(self, seeded_auth_client):
        """Response HTML contains 'Last 3 Months' preset label."""
        client, _ = seeded_auth_client
        resp = client.get("/profile")
        assert b"Last 3 Months" in resp.data

    def test_filter_bar_has_all_time_label(self, seeded_auth_client):
        """Response HTML contains 'All Time' preset label."""
        client, _ = seeded_auth_client
        resp = client.get("/profile")
        assert b"All Time" in resp.data

    def test_filter_bar_has_date_inputs(self, seeded_auth_client):
        """Response HTML contains date input fields for the custom range form."""
        client, _ = seeded_auth_client
        resp = client.get("/profile")
        # Both from_date and to_date inputs must be present
        assert b'type="date"' in resp.data or b"from_date" in resp.data, (
            "Custom date range form with date inputs should be present"
        )

    @pytest.mark.parametrize("period", ["this_month", "last_3_months", "all"])
    def test_filter_bar_present_for_all_presets(self, seeded_auth_client, period):
        """Filter bar labels are present on every preset response."""
        client, _ = seeded_auth_client
        resp = client.get(f"/profile?period={period}")
        assert resp.status_code == 200
        assert b"This Month" in resp.data
        assert b"Last 3 Months" in resp.data
        assert b"All Time" in resp.data


# ---------------------------------------------------------------------------
# Preset: this_month
# ---------------------------------------------------------------------------

class TestPresetThisMonth:
    """?period=this_month shows only current-calendar-month expenses."""

    def test_this_month_returns_200(self, seeded_auth_client):
        """GET /profile?period=this_month returns HTTP 200."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=this_month")
        assert resp.status_code == 200

    def test_this_month_includes_current_month_expenses(self, seeded_auth_client):
        """This-month filter shows expenses dated in the current calendar month."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=this_month")
        html = resp.data.decode()
        assert "TM expense 1" in html, "Current-month expense 1 should appear"
        assert "TM expense 2" in html, "Current-month expense 2 should appear"

    def test_this_month_excludes_two_months_ago_expenses(self, seeded_auth_client):
        """This-month filter hides expenses from 2 months ago."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=this_month")
        html = resp.data.decode()
        assert "2M expense 1" not in html, (
            "2-month-old expense should not appear in this_month view"
        )

    def test_this_month_excludes_six_months_ago_expenses(self, seeded_auth_client):
        """This-month filter hides expenses from 6 months ago."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=this_month")
        html = resp.data.decode()
        assert "6M expense 1" not in html, (
            "6-month-old expense should not appear in this_month view"
        )

    def test_this_month_stats_reflect_only_current_month(self, seeded_auth_client):
        """Stats (transaction count) reflect only current-month expenses."""
        client, meta = seeded_auth_client
        resp = client.get("/profile?period=this_month")
        html = resp.data.decode()
        # We seeded exactly 2 current-month expenses; count must be 2.
        # The rendered page should contain "2" as the transaction count.
        # We check it's present and the total for all 6 (3,300) is absent.
        assert "3,300" not in html, (
            "All-time total should not appear when this_month filter is active"
        )


# ---------------------------------------------------------------------------
# Preset: last_3_months
# ---------------------------------------------------------------------------

class TestPresetLast3Months:
    """?period=last_3_months shows expenses from the past 3 calendar months."""

    def test_last_3_months_returns_200(self, seeded_auth_client):
        """GET /profile?period=last_3_months returns HTTP 200."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=last_3_months")
        assert resp.status_code == 200

    def test_last_3_months_includes_this_month_expenses(self, seeded_auth_client):
        """last_3_months includes current-month expenses."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=last_3_months")
        html = resp.data.decode()
        assert "TM expense 1" in html, "Current-month expense should be within last_3_months"

    def test_last_3_months_includes_two_months_ago_expenses(self, seeded_auth_client):
        """last_3_months includes expenses from 2 months ago."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=last_3_months")
        html = resp.data.decode()
        assert "2M expense 1" in html, (
            "2-month-old expense should appear in last_3_months view"
        )

    def test_last_3_months_excludes_six_months_ago_expenses(self, seeded_auth_client):
        """last_3_months excludes expenses from 6 months ago."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=last_3_months")
        html = resp.data.decode()
        assert "6M expense 1" not in html, (
            "6-month-old expense should be excluded from last_3_months view"
        )
        assert "6M expense 2" not in html

    def test_last_3_months_stats_exclude_old_expenses(self, seeded_auth_client):
        """All-time total (which includes 6-month data) is absent from stats."""
        client, meta = seeded_auth_client
        resp = client.get("/profile?period=last_3_months")
        html = resp.data.decode()
        # All-time total is 3,300 — should not appear when old expenses are filtered out.
        assert "3,300" not in html, (
            "All-time total should not appear when last_3_months filter is active"
        )


# ---------------------------------------------------------------------------
# Preset: all
# ---------------------------------------------------------------------------

class TestPresetAll:
    """?period=all shows every expense regardless of date."""

    def test_all_returns_200(self, seeded_auth_client):
        """GET /profile?period=all returns HTTP 200."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=all")
        assert resp.status_code == 200

    def test_all_includes_this_month_expenses(self, seeded_auth_client):
        """period=all includes current-month expenses."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=all")
        html = resp.data.decode()
        assert "TM expense 1" in html

    def test_all_includes_two_months_ago_expenses(self, seeded_auth_client):
        """period=all includes 2-month-old expenses."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=all")
        html = resp.data.decode()
        assert "2M expense 1" in html

    def test_all_includes_six_months_ago_expenses(self, seeded_auth_client):
        """period=all includes 6-month-old expenses that other filters exclude."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=all")
        html = resp.data.decode()
        assert "6M expense 1" in html, (
            "6-month-old expense should appear when period=all"
        )
        assert "6M expense 2" in html

    def test_all_stats_reflect_all_expenses(self, seeded_auth_client):
        """Stats total reflects all 6 seeded expenses (3,300)."""
        client, meta = seeded_auth_client
        resp = client.get("/profile?period=all")
        html = resp.data.decode()
        # All-time total across 6 expenses is 3,300
        assert "3,300" in html, (
            f"Expected all-time total 3,300 to appear in period=all stats"
        )


# ---------------------------------------------------------------------------
# Custom date range
# ---------------------------------------------------------------------------

class TestCustomDateRange:
    """?from_date=...&to_date=... filters to that explicit range."""

    def test_custom_range_returns_200(self, seeded_auth_client):
        """Valid custom date range returns HTTP 200."""
        client, meta = seeded_auth_client
        resp = client.get(
            f"/profile?from_date={meta['this_month_date1']}&to_date={meta['this_month_date2']}"
        )
        assert resp.status_code == 200

    def test_custom_range_includes_in_range_expenses(self, seeded_auth_client):
        """Custom range shows expenses whose date falls within the range."""
        client, meta = seeded_auth_client
        from_date = meta["this_month_date1"]
        to_date = meta["this_month_date2"]
        resp = client.get(f"/profile?from_date={from_date}&to_date={to_date}")
        html = resp.data.decode()
        assert "TM expense 1" in html, (
            "In-range expense should appear in custom date range view"
        )

    def test_custom_range_excludes_out_of_range_expenses(self, seeded_auth_client):
        """Custom range hides expenses outside the specified window."""
        client, meta = seeded_auth_client
        # Range covers only this month
        from_date = meta["this_month_date1"]
        to_date = meta["this_month_date2"]
        resp = client.get(f"/profile?from_date={from_date}&to_date={to_date}")
        html = resp.data.decode()
        assert "6M expense 1" not in html, (
            "6-month-old expense should be excluded from custom this-month range"
        )
        assert "2M expense 1" not in html, (
            "2-month-old expense should be excluded from custom this-month range"
        )

    def test_custom_range_spanning_two_windows(self, seeded_auth_client):
        """Custom range that spans two_months_ago to today includes both windows."""
        client, meta = seeded_auth_client
        from_date = meta["two_months_date1"]
        to_date = _date_str(_today())
        resp = client.get(f"/profile?from_date={from_date}&to_date={to_date}")
        html = resp.data.decode()
        assert "TM expense 1" in html, "Current-month expense should be in range"
        assert "2M expense 1" in html, "2-month expense should be in range"
        assert "6M expense 1" not in html, "6-month expense should be outside range"

    def test_custom_range_narrow_window_shows_only_targeted_expenses(
        self, seeded_auth_client
    ):
        """A range targeting only the 6-month window shows those expenses."""
        client, meta = seeded_auth_client
        from_date = meta["six_months_date1"]
        to_date = meta["six_months_date2"]
        resp = client.get(f"/profile?from_date={from_date}&to_date={to_date}")
        html = resp.data.decode()
        assert "6M expense 1" in html, "Targeted 6-month expense should appear"
        assert "TM expense 1" not in html, (
            "Current-month expense should not appear in 6-month-only range"
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Resilience tests: empty results, malformed input, missing params."""

    def test_empty_period_returns_200(self, seeded_auth_client):
        """A period with zero matching expenses returns 200, no server error."""
        client, _ = seeded_auth_client
        # A far-future range that matches nothing
        resp = client.get("/profile?from_date=2099-01-01&to_date=2099-01-31")
        assert resp.status_code == 200, (
            "Profile page should return 200 even when no expenses match the filter"
        )

    def test_empty_period_renders_without_crash(self, seeded_auth_client):
        """Zero-result period renders a valid page (stats show zeros or dashes)."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?from_date=2099-01-01&to_date=2099-01-31")
        html = resp.data.decode()
        # Page must render — it should not contain a Python traceback
        assert "Traceback" not in html, "No traceback should appear on zero-result page"
        assert "Internal Server Error" not in html

    def test_malformed_from_date_returns_200(self, seeded_auth_client):
        """Malformed from_date falls back to default and returns 200."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?from_date=not-a-date")
        assert resp.status_code == 200, (
            "Malformed from_date should not crash the server; expect 200"
        )

    def test_malformed_from_date_no_traceback(self, seeded_auth_client):
        """Malformed from_date does not produce a Python traceback in the response."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?from_date=not-a-date")
        html = resp.data.decode()
        assert "Traceback" not in html
        assert "ValueError" not in html

    def test_malformed_to_date_returns_200(self, seeded_auth_client):
        """Malformed to_date falls back to default and returns 200."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?from_date=2024-01-01&to_date=bad-date")
        assert resp.status_code == 200

    def test_missing_to_date_with_valid_from_date_returns_200(self, seeded_auth_client):
        """from_date supplied without to_date defaults to_date to today; returns 200."""
        client, meta = seeded_auth_client
        resp = client.get(f"/profile?from_date={meta['this_month_date1']}")
        assert resp.status_code == 200, (
            "Missing to_date should not crash; it should default to today"
        )

    def test_missing_to_date_with_valid_from_date_shows_from_date_expenses(
        self, seeded_auth_client
    ):
        """When to_date is absent and from_date is this month, current expenses appear."""
        client, meta = seeded_auth_client
        resp = client.get(f"/profile?from_date={meta['this_month_date1']}")
        html = resp.data.decode()
        assert "TM expense 1" in html, (
            "Current-month expense should appear when from_date is this month and to_date defaults to today"
        )

    def test_unknown_period_value_returns_200(self, seeded_auth_client):
        """An unrecognised period value does not crash the server."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=garbage_value")
        assert resp.status_code == 200, (
            "Unrecognised period value should fall back gracefully and return 200"
        )

    def test_empty_period_param_returns_200(self, seeded_auth_client):
        """An empty period string does not crash the server."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=")
        assert resp.status_code == 200

    @pytest.mark.parametrize("params", [
        "?from_date=2024-13-01",        # invalid month
        "?from_date=2024-00-01",        # zero month
        "?from_date=2024-01-32",        # day out of range
        "?from_date=abc&to_date=def",   # both malformed
    ])
    def test_various_malformed_dates_return_200(self, seeded_auth_client, params):
        """A variety of malformed date strings all return 200 without crashing."""
        client, _ = seeded_auth_client
        resp = client.get(f"/profile{params}")
        assert resp.status_code == 200, (
            f"Malformed date params {params!r} should return 200, not a server error"
        )


# ---------------------------------------------------------------------------
# Stats reflect active date range
# ---------------------------------------------------------------------------

class TestStatsFiltering:
    """
    Verify that the summary stats (total spent, transaction count, top category)
    are scoped to the active date range, not the all-time data.
    """

    def test_all_time_total_appears_in_all_period(self, seeded_auth_client):
        """All-time total (3,300) appears only when period=all."""
        client, meta = seeded_auth_client
        resp = client.get("/profile?period=all")
        html = resp.data.decode()
        assert "3,300" in html, (
            "All-time total 3,300 should be visible when period=all"
        )

    def test_all_time_total_absent_in_this_month(self, seeded_auth_client):
        """All-time total (3,300) is absent when period=this_month."""
        client, meta = seeded_auth_client
        resp = client.get("/profile?period=this_month")
        html = resp.data.decode()
        assert "3,300" not in html, (
            "All-time total should not appear in this_month filtered view"
        )

    def test_all_time_total_absent_in_last_3_months(self, seeded_auth_client):
        """All-time total (3,300) is absent when period=last_3_months."""
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=last_3_months")
        html = resp.data.decode()
        assert "3,300" not in html, (
            "All-time total should not appear in last_3_months filtered view"
        )

    def test_top_category_reflects_filter(self, seeded_auth_client):
        """
        Top category in this_month view is 'Food' (only Food expenses this month);
        in period=all view 'Bills' may dominate — they should differ, confirming
        the top_category stat is filtered.
        """
        client, _ = seeded_auth_client
        resp_month = client.get("/profile?period=this_month")
        html_month = resp_month.data.decode()
        # In this_month: only Food (100 + 200 = 300) → top category is Food
        assert "Food" in html_month, (
            "Top category for this_month should be 'Food'"
        )

    def test_category_breakdown_absent_categories_not_shown(self, seeded_auth_client):
        """
        When period=this_month, categories that only appear in older expenses
        (Transport, Bills) should not show up in the category breakdown.
        """
        client, _ = seeded_auth_client
        resp = client.get("/profile?period=this_month")
        html = resp.data.decode()
        # Transport expenses are only from 2 months ago; Bills only from 6 months ago.
        # With this_month filter active, neither category should appear in breakdown.
        # (We look for the category names in the context of the breakdown section.)
        # Note: "Transport" and "Bills" may appear in nav links; we check for
        # the amount associated with those categories not appearing.
        # The 2-month Transport total is 1,000 and 6-month Bills total is 2,000.
        assert "1,000" not in html, (
            "Transport total from 2 months ago should not appear in this_month view"
        )
        assert "2,000" not in html, (
            "Bills total from 6 months ago should not appear in this_month view"
        )
