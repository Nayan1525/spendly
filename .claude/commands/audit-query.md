---
description: Analyze a database query for performance issues (full scans, missing indexes, N+1s, unbounded results) in this Flask+SQLite project
argument-hint: "<file:line | route path | raw SQL> e.g. /audit-query app.py:213"
allowed-tools: Read, Grep, Glob, Bash(sqlite3:*), Bash(git status)
---

You are auditing a single database query for performance in
**Spendly**, a Flask + raw-`sqlite3` app (no ORM). The database
file is `spendly.db` at the repo root (gitignored, created by
`init_db()` in `database/db.py`).

User input: $ARGUMENTS

## Step 1 — Resolve the target query

$ARGUMENTS may be:
1. `file:line` (e.g. `app.py:213`) — read that file and find the
   `db.execute(...)` / `db.executemany(...)` call at or nearest
   after that line.
2. A route path or view function name (e.g. `/expenses` or
   `dashboard`) — Grep `app.py` and `routes/` for the matching
   `@app.route` / `@bp.route` decorator, then read the queries
   inside that view.
3. Raw SQL pasted directly — use it as-is.

If nothing matches, stop and ask the user to point at a specific
file:line, route, or paste the SQL.

If the call site builds the query across multiple lines or uses
string formatting/concatenation to insert a table or column name,
reconstruct the full statement as it would execute, noting any
placeholders (`?`) and what values are bound to them at the call
site.

## Step 2 — Load schema context

Read the `CREATE TABLE` statements in `database/db.py`
(`init_db`) to know each involved table's columns, primary keys,
and `UNIQUE`/`REFERENCES` constraints. Note there are currently
**no explicit indexes beyond what PRIMARY KEY/UNIQUE create
automatically** — anything else is a full table scan unless you
find a `CREATE INDEX` elsewhere (Grep for it to be sure).

## Step 3 — Get the real query plan when possible

If `spendly.db` exists at the repo root, run it through SQLite
directly:

```bash
sqlite3 spendly.db "EXPLAIN QUERY PLAN <query-with-placeholders-substituted>"
```

Substitute `?` placeholders with a representative literal (reuse
a value from the call site if one is visible, otherwise a
plausible sample) purely to make the statement valid — do not
claim these are real user values in your report.

If `spendly.db` doesn't exist (fresh checkout, never run), say so
and fall back to static analysis using the schema from Step 2 —
do not fabricate a query plan.

## Step 4 — Check for these specific issues

- **SCAN instead of SEARCH** in the query plan output — means
  SQLite is reading every row of a table instead of using an
  index.
- **Missing index on a filter/join column** — especially
  `expenses.user_id`, `expenses.date`, `expenses.category`, or
  any column in a `WHERE`/`ORDER BY`/`JOIN` clause that isn't
  already a `PRIMARY KEY`/`UNIQUE`.
- **`SELECT *`** where only a few columns are actually used by
  the caller.
- **Unbounded result sets** — a query with no `LIMIT` whose rows
  are rendered into a template list or loop that could grow
  without bound (e.g. all transactions, all expenses).
- **N+1 pattern** — a query executed inside a loop over the rows
  of another query, where a single `JOIN` or `IN (...)` could
  replace many round trips. Grep the surrounding function for a
  `for` loop containing another `db.execute`.
- **Redundant work** — the same query (or an equivalent one)
  run more than once in the same request/view when it could be
  computed once and reused.

Do not flag SQL-injection or parameterization concerns here —
that's the security reviewer's job, not this command's. If you
notice one in passing, mention it in one line and point to
`/security-review`, nothing more.

## Step 5 — Report

Print in this exact format:

```
Query:      <file:line or route>
Tables:     <tables involved>
Query plan: <EXPLAIN QUERY PLAN output, or "not available (no spendly.db)">

Findings:
  [HIGH|MEDIUM|LOW] <issue> — <one-line why it hurts performance>
  ...

Recommended fixes:
  - <concrete fix, e.g. CREATE INDEX idx_expenses_user_id ON expenses(user_id);>
  - <or a query rewrite, e.g. select only needed columns / add LIMIT / replace loop+query with one IN (...) query>
```

If there are zero findings, say so plainly instead of inventing
minor nitpicks.

Do not edit any files or run `CREATE INDEX` against `spendly.db`
yourself — this command only reports. If the user asks you to
apply a fix afterward, do that as a separate explicit step.
