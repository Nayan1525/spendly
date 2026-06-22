# Spec: Backend Routes for Profile Page

## Overview

Step 5 replaces the hardcoded context data in the `/profile` route with real database queries. The profile page UI was built in Step 4 with static, hardcoded dicts — this step wires it to the actual `users` and `expenses` tables so that each logged-in user sees their own name, email, member-since date, total spending, transaction history, and per-category breakdown. No new tables or routes are needed; only the data flowing into the existing template changes.

## Depends on

- Step 1: Database setup (users and expenses tables must exist)
- Step 2: Registration (real user accounts must exist)
- Step 3: Login and Logout (session must carry `user_id`; `/profile` must be a protected route)
- Step 4: Profile page UI (template must already exist with the correct variable names)

## Routes

No new routes. The existing `GET /profile` route is modified in-place.

## Database changes

No database changes. The existing `users` and `expenses` tables are sufficient.

## Templates

- **Modify:** `templates/profile.html` — update the `user.member_since` display to format the raw `created_at` datetime from the database (e.g. "June 2026" from "2026-06-01 00:00:00"). All other template variable names remain the same.

## Files to change

- `app.py` — replace all hardcoded dicts/lists in the `profile()` view function with real queries using `get_db()`

## Files to create

None.

## New dependencies

No new dependencies.

## Rules for implementation

- No SQLAlchemy or ORMs — use raw sqlite3 via `get_db()`
- Parameterised queries only — never string-format SQL
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- Authentication guard remains: check `session.get("user_id")`; if absent, `redirect(url_for("login"))`
- Always close the DB connection (`db.close()`) after queries
- Format `created_at` (stored as `"YYYY-MM-DD HH:MM:SS"`) into a human-readable month/year string in Python, not in the template
- Category percent bars must be computed as `round(category_total / grand_total * 100)` — never hardcoded
- If the user has zero expenses, all stats must show safe zero-values (no division by zero)

## Queries required

1. **User info** — fetch `name`, `email`, `created_at` from `users` where `id = session['user_id']`
2. **Transactions** — fetch all `expenses` rows for `user_id`, ordered by `date DESC`, limit 10
3. **Summary stats** — compute from expenses for that user:
   - `total_spent` — `SUM(amount)`
   - `transaction_count` — `COUNT(*)`
   - `top_category` — category with the highest `SUM(amount)`
4. **Category breakdown** — `SELECT category, SUM(amount) as total FROM expenses WHERE user_id = ? GROUP BY category ORDER BY total DESC`

## Definition of done

- [ ] Visiting `/profile` without being logged in redirects to `/login`
- [ ] Visiting `/profile` while logged in returns HTTP 200
- [ ] The user info card shows the logged-in user's actual name and email (not hardcoded "demo@spendly.com")
- [ ] The member-since date reflects the user's real `created_at` from the database
- [ ] Total spent stat matches the real sum of that user's expenses
- [ ] Transaction count stat matches the real count of that user's expenses
- [ ] Top category stat matches the category with the highest spend for that user
- [ ] The transaction table shows real rows from the expenses table (not hardcoded rows)
- [ ] The category breakdown shows real per-category totals from the database
- [ ] Bar widths in the category breakdown are computed from real data (not hardcoded `.w-*` classes)
- [ ] A user with zero expenses sees zeros/dashes with no server error (no division by zero)
- [ ] All SQL uses parameterised queries — no string formatting
