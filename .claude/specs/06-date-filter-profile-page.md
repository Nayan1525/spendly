# Spec: Date Filter for Profile Page

## Overview

Step 6 adds a date range filter to the `/profile` page so users can slice their expense data by time period. Currently the profile page shows all-time totals and the last 10 transactions with no way to narrow the view. This step adds preset quick-filters ("This Month", "Last 3 Months", "All Time") and a custom date range picker. Selecting a filter re-queries the database and refreshes the stats, transaction table, and category breakdown — all within the same `GET /profile` route using query parameters.

## Depends on

- Step 1: Database Setup — expenses table with a `date` TEXT column (YYYY-MM-DD)
- Step 4: Profile Page UI — `profile.html` template must exist with the correct section structure
- Step 5: Backend Routes for Profile Page — real DB queries must already be wired up in `profile()`

## Routes

No new routes. `GET /profile` is extended to accept optional query parameters:
- `?from_date=YYYY-MM-DD` — filter expenses on or after this date
- `?to_date=YYYY-MM-DD` — filter expenses on or before this date
- `?period=this_month|last_3_months|all` — preset shortcut that auto-calculates `from_date`/`to_date`

When no parameters are present, default to `period=this_month`.

## Database changes

No database changes. The existing `expenses` table already stores `date` as TEXT in `YYYY-MM-DD` format, which SQLite compares correctly with string operators `>=` and `<=`.

## Templates

- **Modify:** `templates/profile.html` — add a filter bar above the stats row containing:
  1. **Preset buttons** — "This Month", "Last 3 Months", "All Time"; the active preset is visually highlighted using a CSS class
  2. **Custom date range inputs** — two `<input type="date">` fields (From / To) with a "Apply" submit button
  3. The filter bar submits as a `GET` form to `/profile`
  4. Stats row, transaction table, and category breakdown already receive their data from the route — no template logic changes needed beyond rendering the new `active_period` and `from_date`/`to_date` values passed from the route

## Files to change

- `app.py` — update the `profile()` view function:
  - Read `period`, `from_date`, `to_date` from `request.args`
  - Compute actual date bounds in Python using `datetime`
  - Add `WHERE date >= ? AND date <= ?` clauses to all four existing queries (user info query is unaffected)
  - Pass `active_period`, `from_date`, and `to_date` back to the template so the filter UI reflects the active selection
- `templates/profile.html` — add the filter bar section described above

## Files to create

None.

## New dependencies

No new dependencies. Uses Python's built-in `datetime` module (already imported in `profile()`).

## Rules for implementation

- No SQLAlchemy or ORMs
- Parameterised queries only — never string-format SQL; date bounds must be passed as query parameters `(?, ?)`
- Passwords hashed with werkzeug (no auth changes)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- Authentication guard remains: check `session.get("user_id")`; redirect to `/login` if absent
- Always close the DB connection after queries
- Default period is `this_month` when no query params are supplied
- `period=all` means no date lower bound; use a far-past date like `"2000-01-01"` as the lower bound rather than building a conditional query
- Custom `from_date`/`to_date` values from the form must be validated: if either is missing or malformed, fall back to the `this_month` default silently (no error page)
- `to_date` defaults to today's date when only `from_date` is provided via custom input

## Date bound calculation

Compute these in Python inside `profile()`:

| `period` value   | `from_date`                    | `to_date`  |
|------------------|--------------------------------|------------|
| `this_month`     | First day of current month     | Today      |
| `last_3_months`  | First day 3 months ago         | Today      |
| `all`            | `"2000-01-01"` (sentinel)      | Today      |
| *(custom)*        | Value from `request.args`      | Value from `request.args` (or today) |

Use `date.today()` and `date.replace(day=1)` / `timedelta` arithmetic. No external libraries.

## Definition of done

- [ ] Visiting `/profile` with no query params defaults to "This Month" filter
- [ ] The filter bar is visible on the profile page with three preset buttons and a custom date range input
- [ ] Clicking "This Month" reloads the page showing only expenses from the current calendar month
- [ ] Clicking "Last 3 Months" reloads the page showing only expenses from the past three months
- [ ] Clicking "All Time" reloads the page showing all expenses with no date restriction
- [ ] The active preset button is visually distinguished from the others (e.g. filled vs outline)
- [ ] Submitting the custom date range form filters expenses to that range
- [ ] Total spent stat reflects only expenses within the active date range
- [ ] Transaction count stat reflects only expenses within the active date range
- [ ] Top category stat reflects only expenses within the active date range
- [ ] Transaction table rows are filtered to the active date range (still capped at 10 rows)
- [ ] Category breakdown reflects only expenses within the active date range
- [ ] A period with zero expenses shows zeros/dashes without a server error
- [ ] All SQL uses parameterised queries — no string formatting
- [ ] Malformed or missing custom date values fall back to "This Month" without an error page
