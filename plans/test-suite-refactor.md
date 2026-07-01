# Plan: Test Suite Refactor

Source: test architecture review of `tests/` and `doc-processor/tests/`, 2026-07-01.
No code has been changed yet — this plan is the implementation roadmap.

## Current state (verified by running both suites)

- `tests/`: 183 passed, 6 failed
- `doc-processor/tests/`: 113 passed

## Priority 0 — Fix what's broken

### P0.1 — Repair or retire `tests/test_07-add-expense.py`

`TestPostAddExpense` still asserts the pre-Step-8 contract (synchronous DB
insert, 302 redirect to `/profile`). The route now publishes to SQS via
`publish_expense`, which isn't mocked in this file, so 6 tests fail with
`RuntimeError: SQS_QUEUE_URL environment variable is not set`.

- Delete `TestGetAddExpense` and `TestPostAddExpense` from `test_07-add-expense.py`
  — `test_08-sqs-expense-processing.py` already covers `/expenses/add` correctly
  for the current implementation.
- Keep `TestInsertExpense` (lines 114-211) — still valid, exercises
  `database/queries.insert_expense` directly, still used by the worker.
- Rename the file to `test_07-insert-expense.py` (or fold the class into
  `test_08`) once it only contains the DB-helper unit tests, to stop implying
  it covers the route.

## Priority 1 — Close coverage gaps

### P1.1 — Add tests for Steps 2-5 (registration, login/logout, profile)

No dedicated test file exists for `/register`, `/login`, `/logout`, or the
base `/profile` route + backend queries, despite specs existing for all of
them. Add:

- `tests/test_02-registration.py` — valid registration, duplicate email
  (`IntegrityError` path in `app.py`), name/email/password length validation,
  password mismatch, password hashing (never stored in plaintext).
- `tests/test_03-login-logout.py` — valid login, wrong password, unknown
  email, session set on success, `/logout` clears session, already-logged-in
  redirect from `/login` to `/dashboard`.
- `tests/test_05-profile-backend.py` — user info, stats, transactions,
  category breakdown against real DB rows (can likely reuse fixtures from
  P2.1 instead of writing new ones).

### P1.2 — Add tests for Step 10 (Elasticsearch search)

`search/es_client.py` has no route wiring in `app.py` and no functional test
— only `tests/test_search_benchmark.py`, which is explicitly a benchmark,
skips without a live ES instance, and isn't part of the enforced suite.

- Once the search route is implemented, add `tests/test_10-expense-search.py`
  covering: auth guard, free-text query, category/amount/date filters,
  pagination, empty results, malformed filter params — same shape as
  `test_06-date-filter-profile-page.py`.
- These tests need a real or mocked ES backend decision — see P1.3.

### P1.3 — Decide how search tests reach Elasticsearch

Options, pick one before writing P1.2:
- Spin up ES via `docker-compose` in CI and run against it (matches how
  `test_search_benchmark.py` already works).
- Or mock the `elasticsearch` client the way `worker/sqs_publisher` mocks
  `boto3` (see P3.1 for why the raw-mock approach is already inconsistent —
  don't add a third style).

## Priority 2 — Remove duplication

### P2.1 — Add `tests/conftest.py`

Extract the identical `app(tmp_path, monkeypatch)` / `client(app)` fixtures
(currently duplicated verbatim in `test_06`, `test_07`, `test_08`) into a
shared `tests/conftest.py`, mirroring the pattern already proven in
`doc-processor/tests/conftest.py`. Delete the per-file copies once callers
are confirmed to still pass.

### P2.2 — Consolidate `auth_client` variants

`test_07` and `test_08` each define their own `auth_client` fixture, differing
only by seed email. Move a single parametrizable version into
`tests/conftest.py` (accept a `label` or generate a unique email per test via
`uuid4`).

### P2.3 — Deduplicate `doc-processor/tests/test_09-document-processing-service.py`

This file re-implements everything already in `test_routes.py`,
`test_extractor.py`, and `test_consumer.py`, and redefines `tmp_db`, `app`,
`aws_credentials` instead of using `conftest.py`. Delete `test_09` entirely —
diff its assertions against the other three files first to confirm no unique
case is lost (skim showed none), then remove.

## Priority 3 — Consistency and precision fixes

### P3.1 — Standardize AWS mocking on `moto`

`worker/sqs_publisher` tests patch `boto3` directly with `MagicMock`
(`tests/test_08-sqs-expense-processing.py:1024-1260`), while
`doc-processor` tests use `moto.mock_aws()` against an emulated backend.
Migrate the publisher tests to `moto` for consistency and because it catches
real serialization/parameter bugs a `MagicMock` can't.

### P3.2 — Replace mocked worker DB assertions with real DB assertions

`TestProcessMessageHappyPath` in `test_08-sqs-expense-processing.py`
(lines 586-770) mocks `insert_expense`/`get_db` and asserts on positional
`call_args` indices — brittle to argument-order refactors that don't change
behavior. Rewrite each to call `process_message` against the real temp DB
(already available via the `app` fixture) and assert on the persisted row,
matching the pattern in `doc-processor/tests/test_consumer.py`.

### P3.3 — Parametrize the 7 `test_process_message_passes_*` tests

Collapse `test_process_message_passes_user_id_to_insert_expense`,
`..._amount_...`, `..._category_...`, `..._date_...`, `..._description_...`
(lines 612-720) into one `@pytest.mark.parametrize`d test over
`(json_key, expected_value)` once P3.2 lands (it will change what's being
asserted from call-args to DB-row fields).

### P3.4 — Remove duplicate non-parametrized validation tests

Both `test_07-add-expense.py` and `test_08-sqs-expense-processing.py` keep
individual tests (`test_post_missing_amount_returns_200`,
`test_post_amount_zero_returns_200`, etc.) that assert exactly what their
existing `@pytest.mark.parametrize`d sweep already covers. Delete the
individual versions, keep the parametrized sweep as the single source of
truth for the "invalid field → 200, no side effect" contract.

### P3.5 — Fix misleading test name

`doc-processor/tests/test_consumer.py:81` —
`test_description_none_stored_correctly` asserts `row["error_message"] is
None`. Rename to `test_error_message_is_null_on_success`.

### P3.6 — Add wrong-type coverage for SQS message validation

`TestProcessMessageMalformed` in `test_08-sqs-expense-processing.py` only
covers missing keys. Add cases for wrong-typed values that reach the
`ExpenseMessage` Pydantic model directly (e.g. `"amount": "not-a-number"`,
`"date": 12345`) to confirm `ValidationError` → `ValueError` still holds at
the type level, not just the presence level.

### P3.7 — Delete stray `tests/test_api/`

Empty directory containing only stale `__pycache__` from a deleted/renamed
test layout. Delete it; confirm `.gitignore` covers `__pycache__` so it
doesn't reappear from local runs.

## Suggested execution order

1. P0.1 (unblocks a clean `pytest` run today)
2. P2.1, P2.2 (shared fixtures — do this before adding P1.1/P1.2 so new files
   use the shared conftest instead of adding a fourth copy of the same fixtures)
3. P1.1, P1.2/P1.3 (close coverage gaps using the now-shared fixtures)
4. P2.3, P3.1-P3.7 (cleanup, can happen in any order / in parallel)

## Definition of done

- `pytest` from repo root exits 0 with no failures.
- `tests/conftest.py` exists; `app`/`client`/`auth_client` are defined once.
- Every route in `app.py` (`/register`, `/login`, `/logout`, `/profile`,
  `/expenses/add`, and the search route once implemented) has at least one
  test file exercising its auth guard, happy path, and validation/error path.
- No test file contains a fixture also defined in `conftest.py`.
- No two tests assert the identical contract for the identical input (i.e.
  every parametrized sweep has no non-parametrized sibling).
- `doc-processor/tests/test_09-document-processing-service.py` no longer
  exists, or contains only cases not covered elsewhere.
