# Spec: Elasticsearch Expense Search

## Overview
Step 10 adds full-text and faceted search over a user's expenses, backed by
Elasticsearch. SQLite remains the source of truth for expense data; a new
`search/` package indexes each expense into Elasticsearch as soon as the SQS
worker (Step 8) inserts it into `expenses`, and a new protected route lets
users search their own expenses by free text (description) plus filters
(category, amount range, date range) with pagination. This teaches
denormalizing a read model into a secondary search index, keeping it in sync
from an existing async write path, and building bool/filter queries.

## Depends on
- Step 1: Database setup (`expenses` table, `get_db` helper)
- Step 3: Login / Logout (`session["user_id"]` on protected routes)
- Step 7: Add Expense (expense creation form/flow)
- Step 8: SQS Expense Processing (`worker/sqs_worker.py` is where new
  expenses land in SQLite — this is the hook point for indexing into
  Elasticsearch)

## Routes
- `GET /expenses/search` — search form + paginated results — logged-in only

Query params: `q` (free text over description), `category`, `min_amount`,
`max_amount`, `from_date`, `to_date`, `page` (default 1, 20 results per page).
All filters are optional and combine with AND semantics. Results are always
scoped to `session['user_id']` — a user can never search another user's data.

## Database changes
No new tables or columns. `database/queries.py`'s `insert_expense` must be
modified to return the new row's id (`cursor.lastrowid`) instead of nothing,
since the indexing step needs the expense id to use as the Elasticsearch
document `_id`. This is the only change to existing SQLite code.

## Elasticsearch index

Index name: `expenses` (configurable via `EXPENSES_INDEX` env var).

Field type choices (validated by benchmark, see below):
- `user_id`: `keyword` — a pure identifier used only for exact-match `term`
  filtering, never range math. `integer` works too, but `keyword` is the
  conventional choice for IDs.
- `amount`: `scaled_float` (`scaling_factor: 100`) — stores money as an
  integer number of paise internally, avoiding the binary-fraction rounding
  `float`/`double` are prone to for currency.
- `category`: `keyword` with a `.text` multi-field — `keyword` for exact
  filtering/aggregation (low cardinality, 7 fixed values), `.text` (analyzed)
  so a free-text query can also match on category (e.g. searching "food").
- `date`: `date` — unchanged.
- `description`: `text` using a custom analyzer (below), with a `.keyword`
  sub-field (`ignore_above: 256`) reserved for exact-match/sort use cases.
- No nested or flattened fields — the document is flat (no repeating
  sub-objects). If a future step adds itemized line-items per expense, those
  should be `nested` (not `flattened`) so each line item's fields stay
  correlated during matching.

Custom analyzer — `expense_text_analyzer` — lowercase + asciifolding + English
stopwords + **English (Porter2/snowball) stemmer**, not `light_english`. The
light stemmer left `grocery`/`groceries` as distinct tokens (verified by
`_analyze` — see Benchmark below); the full `english` stemmer reduces both to
`groceri`, so a search for "grocery" also matches expenses only ever
described as "groceries".

```json
{
  "settings": {
    "analysis": {
      "filter": {
        "expense_stop": { "type": "stop", "stopwords": "_english_" },
        "expense_stemmer": { "type": "stemmer", "language": "english" }
      },
      "analyzer": {
        "expense_text_analyzer": {
          "type": "custom",
          "tokenizer": "standard",
          "filter": ["lowercase", "asciifolding", "expense_stop", "expense_stemmer"]
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "user_id":  { "type": "keyword" },
      "amount":   { "type": "scaled_float", "scaling_factor": 100 },
      "category": {
        "type": "keyword",
        "fields": { "text": { "type": "text", "analyzer": "expense_text_analyzer" } }
      },
      "date": { "type": "date", "format": "yyyy-MM-dd" },
      "description": {
        "type": "text",
        "analyzer": "expense_text_analyzer",
        "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } }
      }
    }
  }
}
```

Document `_id` = the SQLite `expenses.id` (keeps SQLite and Elasticsearch
1:1 and makes re-indexing idempotent — indexing the same expense twice
overwrites rather than duplicates).

## `search/` package (new)

```
search/
├── __init__.py
├── config.py      # ELASTICSEARCH_URL, EXPENSES_INDEX — same os.environ pattern as worker/config.py
├── es_client.py   # get_es_client(), ensure_index(), index_expense(), search_expenses()
```

### `search/config.py`
```python
import os

ELASTICSEARCH_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
EXPENSES_INDEX    = os.environ.get("EXPENSES_INDEX", "expenses")
```

### `search/es_client.py`
```python
def get_es_client() -> Elasticsearch: ...

def ensure_index() -> None:
    """Create the expenses index with the mapping above if it doesn't exist.
    Called once at Flask app startup, same place init_db()/seed_db() are called."""

def index_expense(expense_id: int, user_id: int, amount: float,
                   category: str, date: str, description: str | None) -> None:
    """Index (or overwrite) one expense document, _id=expense_id."""

def search_expenses(user_id: int, q: str = "", category: str = "",
                     min_amount: float | None = None, max_amount: float | None = None,
                     from_date: str = "", to_date: str = "",
                     page: int = 1, size: int = 20) -> dict:
    """Build a bool query filtered to user_id, return
    {"hits": [...], "total": int, "page": int, "size": int}."""
```

`search_expenses` query shape — a `function_score` wrapping a `bool` query:
- `filter: [{"term": {"user_id": user_id}}]` — always present, non-negotiable
- `q` (if present) → `{"multi_match": {"query": q, "fields": ["description^3", "category.text"], "type": "best_fields", "fuzziness": "AUTO"}}` in `must` (else `match_all`)
- `category` (if present) → `{"term": {"category": category}}` in `filter`
- `min_amount`/`max_amount` (if present) → `{"range": {"amount": {"gte": ..., "lte": ...}}}` in `filter`
- `from_date`/`to_date` (if present) → `{"range": {"date": {"gte": ..., "lte": ...}}}` in `filter`
- Wrapped in `function_score` with one `gauss` decay function on `date`
  (`origin: "now"`, `scale: "180d"`, `decay: 0.3`, `weight: 4`),
  `score_mode: "sum"`, `boost_mode: "sum"` — recency is **added** to the text
  relevance score as a bounded nudge (0–4 points), not multiplied against it.
  An earlier `multiply`/`multiply` version was tried and rejected — see
  Benchmark below, it let recency completely invert relevance ranking.
- `from = (page - 1) * size`, `size = size` — no explicit `sort`; the combined
  function_score `_score` determines order

## Wiring indexing into the SQS worker

`worker/sqs_worker.py`'s `process_message()` calls `insert_expense(...)`,
which now returns the new expense id. Immediately after the insert succeeds,
call `index_expense(expense_id, ...)`.

Elasticsearch indexing failures must **not** cause the SQS message to be
retried or fail — the SQLite insert already succeeded and is the source of
truth, and `insert_expense` is not idempotent (retrying would create a
duplicate row). Wrap the `index_expense` call in its own try/except, log a
warning on failure (e.g. `print(f"Failed to index expense {expense_id}: {exc}",
file=sys.stderr)`), and still delete the SQS message as a success.

## Benchmark: naive vs optimized design

`search/benchmark_baseline.py` re-implements the *original* naive design
(plain `text` field, standard analyzer, single-field `match`, hard
`sort: [{"date": "desc"}]`, no `function_score`) purely so
`tests/test_search_benchmark.py` can measure the delta against
`search/es_client.py`. It is not imported by the app.

Run: `pytest tests/test_search_benchmark.py -s` (skips if Elasticsearch isn't
reachable). Seeds ~240 synthetic expenses (3 truly-relevant "grocery
shopping" docs dated ~130 days ago, 30 recent "shopping"-but-not-"grocery"
decoys, 3 "groceries"-only-plural docs, 200 noise docs) into two throwaway
indices and runs three checks:

| Check | Naive | Optimized | Verdict |
|---|---|---|---|
| Recall of "grocery" query against docs that only ever say "groceries" | 0/3 | 3/3 | Custom analyzer's `english` stemmer fixes a real recall gap |
| Relevant docs in top 5 for "grocery shopping" (30 recent decoys vs 3 old-but-relevant docs) | 0/5 | 3/5 | `function_score` ranking beats naive's hard date-sort, which ignored text relevance entirely |
| Mean query latency at ~240 docs (5 representative queries × 30 iterations) | 3.5 ms | 4.7 ms | +1.2 ms (+37%) overhead from `function_score` + multi_match — real but small at this scale; not tested at production data volumes |

Two iteration notes worth keeping (found by actually running this, not
guessed): the `light_english` stemmer does **not** collapse
`grocery`/`groceries` to the same token (verified with `_analyze`) — must be
`english`. And an initial `function_score` using `score_mode`/`boost_mode:
"multiply"` let the recency decay function overwhelm text relevance for
anything more than ~90 days old, inverting the ranking so recent-but-barely-
relevant docs beat old-but-highly-relevant ones; switching to `sum`/`sum`
with a bounded `weight: 4` makes recency a tie-breaker instead of a
relevance-overriding multiplier.

## App startup

`app.py` calls `ensure_index()` alongside the existing `init_db()` /
`seed_db()` calls inside the `with app.app_context():` block.

## Templates
- **Create:** `templates/search_expenses.html` — extends `base.html`; search
  input, filter controls (category dropdown reusing `EXPENSE_CATEGORIES`,
  amount range, date range), results table (date, category, description,
  amount — same ₹ formatting as `profile.html`: `f"{amount:,.0f}"`), and
  Prev/Next pagination links that preserve all active filters in the query
  string.
- **Modify:** `templates/base.html` — add a "Search" nav link to
  `/expenses/search`, visible only when logged in (same pattern as other
  logged-in-only nav links).

## Files to change
| File | Change |
|---|---|
| `app.py` | Add `GET /expenses/search` route; call `ensure_index()` at startup |
| `database/queries.py` | `insert_expense` returns `cursor.lastrowid` |
| `worker/sqs_worker.py` | Call `index_expense()` after a successful insert; never let indexing errors trigger retry/DLQ |
| `templates/base.html` | Add "Search" nav link for logged-in users |
| `requirements.txt` | Add `elasticsearch` client |

## Files to create
| File | Purpose |
|---|---|
| `search/__init__.py` | Package marker |
| `search/config.py` | `ELASTICSEARCH_URL`, `EXPENSES_INDEX` env vars |
| `search/es_client.py` | `get_es_client`, `ensure_index`, `index_expense`, `search_expenses`, `build_query` — production code, done and benchmark-validated |
| `search/benchmark_baseline.py` | Naive pre-optimization mapping/query, kept only for the benchmark comparison — not imported by the app |
| `tests/test_search_benchmark.py` | pytest benchmark comparing naive vs optimized (recall, ranking, latency) — skips if Elasticsearch isn't reachable |
| `templates/search_expenses.html` | Search form + results + pagination |
| `docker-compose.yml` | Single-node Elasticsearch for local dev (security disabled, matches this being a training project) — done |
| `.env.example` | Documents `ELASTICSEARCH_URL`, `EXPENSES_INDEX` alongside existing SQS/AWS vars |

## New dependencies
```
elasticsearch==8.14.0
```

## Rules for implementation
- No SQLAlchemy or ORMs
- Parameterised queries only
- Passwords hashed with werkzeug
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- `ELASTICSEARCH_URL` and `EXPENSES_INDEX` must come from environment
  variables, never hardcoded — follow the same `os.environ.get(...)` pattern
  already used in `worker/config.py`
- Every Elasticsearch query for expense search must filter on
  `session['user_id']` — never trust a user-supplied `user_id`
- Elasticsearch is a read-optimized secondary index only — SQLite is always
  the source of truth; nothing should read expense totals/stats from
  Elasticsearch (Step 4/5/6 profile stats stay on SQL)
- Indexing failures must never cause an SQS message to retry or DLQ — see
  "Wiring indexing into the SQS worker" above

## Definition of done
- [ ] `docker-compose up elasticsearch` starts a single-node Elasticsearch reachable at `http://localhost:9200`
- [ ] On app startup, the `expenses` index exists (verify with `curl localhost:9200/expenses`)
- [ ] Adding a new expense (Step 7 form → SQS → worker) results in a matching document appearing in the `expenses` Elasticsearch index within a few seconds
- [ ] `GET /expenses/search` redirects to `/login` when not logged in
- [ ] `GET /expenses/search?q=<word from a description>` returns only that user's matching expenses
- [ ] `GET /expenses/search?category=Food` returns only Food expenses for the logged-in user
- [ ] `GET /expenses/search?min_amount=100&max_amount=500` filters by amount range correctly
- [ ] `GET /expenses/search?from_date=2026-06-01&to_date=2026-06-30` filters by date range correctly
- [ ] A second user never sees the first user's expenses in search results, even with an identical query
- [ ] Pagination (`page=2`) returns the next 20 results and preserves active filters in the Prev/Next links
- [ ] Killing Elasticsearch does not break adding new expenses (worker logs a warning and still processes the SQLite insert + SQS delete)
