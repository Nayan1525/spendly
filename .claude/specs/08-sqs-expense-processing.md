# Spec: SQS Expense Processing

## Overview
Step 8 introduces asynchronous expense processing by inserting an AWS SQS
queue between form submission and the database write. Rather than writing the
new expense to SQLite immediately inside the request, `POST /expenses/add`
validates the form data, validates the payload against a Pydantic schema, and
publishes it to an SQS queue. A long-running worker (`worker/sqs_worker.py`)
polls the queue, validates incoming messages with the same Pydantic schema,
inserts the expense with retry logic and visibility-timeout management, and
routes permanently-failed messages to a dead-letter queue handler
(`worker/dlq_handler.py`) that logs them and fires an alert.

This step teaches decoupled async architecture, defensive schema validation,
and operational resilience patterns (retry, backoff, DLQ).

## Depends on
- Step 1: Database setup (`expenses` table and `get_db` helper exist)
- Step 3: Login / Logout (`session["user_id"]` available on protected routes)
- Step 7: Add Expense (form, validation logic, and `insert_expense` helper exist)

## Routes
- `POST /expenses/add` — validate form data, validate Pydantic schema, publish to SQS, render confirmation — logged-in only
- `GET /expenses/add` — unchanged; renders the add-expense form — logged-in only

No new HTTP routes.

## Database changes
No new tables or columns. The `expenses` table is unchanged.

## Templates
- **Already created:** `templates/expense_queued.html` — confirmation page (done)
- **No further template changes needed**

## Pydantic message schema — `worker/schemas.py`

Define one model used by both producer and consumer:

```python
from pydantic import BaseModel, field_validator
from typing import Optional

VALID_CATEGORIES = ["Food", "Transport", "Bills", "Health",
                    "Entertainment", "Shopping", "Other"]

class ExpenseMessage(BaseModel):
    user_id:     int
    amount:      float
    category:    str
    date:        str          # YYYY-MM-DD
    description: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("amount must be greater than 0")
        return v

    @field_validator("category")
    @classmethod
    def category_valid(cls, v):
        if v not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of {VALID_CATEGORIES}")
        return v

    @field_validator("date")
    @classmethod
    def date_format(cls, v):
        from datetime import datetime
        datetime.strptime(v, "%Y-%m-%d")
        return v
```

## Producer — `worker/sqs_publisher.py` (modify)

- Accept either keyword args or an `ExpenseMessage` instance
- Validate input via `ExpenseMessage(**kwargs)` before touching boto3
- Serialise with `model.model_dump_json()` (not plain `json.dumps`)
- If `SQS_QUEUE_URL` is not set, raise `RuntimeError` immediately
- Return `MessageId` on success; propagate exceptions on failure

## Consumer — `worker/sqs_worker.py` (modify)

### Retry logic and visibility timeout management
- Each `receive_message` call requests the `ApproximateReceiveCount` attribute
- On processing failure (non-schema error, e.g. DB error):
  - If `ApproximateReceiveCount < MAX_RETRIES`: extend visibility timeout with
    `change_message_visibility(VisibilityTimeout = backoff_seconds(attempt))`
    where `backoff_seconds(n) = min(2 ** n * 5, 300)` (5 s, 10 s, 20 s … max 300 s)
  - If `ApproximateReceiveCount >= MAX_RETRIES`: log as permanently failed,
    do NOT delete (SQS will route it to the DLQ automatically after
    `maxReceiveCount` is exhausted), call `send_alert(message)` from
    `worker/dlq_handler.py`
- On schema validation failure (`ValidationError`): log and skip immediately —
  these are not retried because retrying won't fix bad data
- On success: delete message immediately

### Flow per message
```
receive message
  → parse ApproximateReceiveCount (attempt number)
  → validate body with ExpenseMessage.model_validate_json(body)
      ✗ ValidationError  → log "schema error", skip (do not delete)
      ✓
  → insert_expense(...)
      ✗ Exception + attempt < MAX_RETRIES  → change_message_visibility (backoff)
      ✗ Exception + attempt >= MAX_RETRIES → log + send_alert (let SQS DLQ it)
      ✓ → delete_message
```

## DLQ handler — `worker/dlq_handler.py` (create)

Two responsibilities:

### 1. `send_alert(message: dict)`
Called inline by the worker when a message has exhausted retries:
- Logs the full message body and error to stderr with a `[ALERT]` prefix
- If `ALERT_SNS_TOPIC_ARN` env var is set, publishes a notification to SNS
  (`boto3.client("sns").publish(...)`) — otherwise logs only

### 2. `run_dlq_handler()` — standalone polling loop
- Polls `DLQ_QUEUE_URL` (env var) with long-polling
- For each message: log full body + metadata, call `send_alert`, delete from DLQ
- Runnable as `python worker/dlq_handler.py`
- Exits gracefully on `KeyboardInterrupt`

## Configuration — `worker/config.py` (modify)

Add new env vars:

```python
DLQ_QUEUE_URL     = os.environ.get("DLQ_QUEUE_URL")
MAX_RETRIES       = int(os.environ.get("MAX_RETRIES", "3"))
ALERT_SNS_TOPIC_ARN = os.environ.get("ALERT_SNS_TOPIC_ARN")   # optional
```

## Files to change
| File | Change |
|---|---|
| `worker/config.py` | Add `DLQ_QUEUE_URL`, `MAX_RETRIES`, `ALERT_SNS_TOPIC_ARN` |
| `worker/sqs_publisher.py` | Validate with `ExpenseMessage` before publishing; use `model_dump_json()` |
| `worker/sqs_worker.py` | Add retry logic, visibility timeout extension, DLQ escalation |
| `requirements.txt` | Add `pydantic` |

## Files to create
| File | Purpose |
|---|---|
| `worker/schemas.py` | `ExpenseMessage` Pydantic model shared by producer and consumer |
| `worker/dlq_handler.py` | `send_alert()` + standalone DLQ polling loop |

## New dependencies
```
pydantic
```
(`boto3` already added in the initial implementation)

## Rules for implementation
- No SQLAlchemy or ORMs
- Parameterised queries only — no string-format values in SQL
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`; no inline styles
- `SQS_QUEUE_URL` and `DLQ_QUEUE_URL` must come from environment variables
- AWS credentials must come from env vars or boto3 credential chain — never hardcoded
- Pydantic `ExpenseMessage` must be the single source of truth for message shape —
  never duplicate field definitions between producer and consumer
- The worker must call `change_message_visibility` **before** giving up on a message,
  not after — giving up means letting the SQS visibility timeout expire naturally
- `send_alert` must never raise — wrap in try/except so an alert failure doesn't
  mask the original processing error
- `MAX_RETRIES` default is 3; must be configurable via env var

## Environment variables
| Variable | Purpose | Required |
|---|---|---|
| `SQS_QUEUE_URL` | Main queue URL | Yes |
| `DLQ_QUEUE_URL` | Dead-letter queue URL | For DLQ handler only |
| `AWS_REGION` | AWS region | Yes |
| `AWS_ACCESS_KEY_ID` | AWS access key | Yes (or IAM role) |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | Yes (or IAM role) |
| `MAX_RETRIES` | Max processing attempts before DLQ escalation | No (default 3) |
| `ALERT_SNS_TOPIC_ARN` | SNS topic for failure alerts | No (log-only if absent) |

## Definition of done
- [ ] `worker/schemas.py` defines `ExpenseMessage` with validators for amount, category, and date
- [ ] `publish_expense` validates input with `ExpenseMessage` before calling boto3 — invalid data raises `ValidationError` before any network call
- [ ] `publish_expense` serialises via `model_dump_json()`, not a hand-rolled dict
- [ ] `process_message` validates incoming JSON with `ExpenseMessage.model_validate_json()` — schema errors are logged and skipped (not retried)
- [ ] Worker reads `ApproximateReceiveCount` on every received message
- [ ] On DB error with attempts < `MAX_RETRIES`: `change_message_visibility` is called with exponential backoff delay
- [ ] On DB error with attempts >= `MAX_RETRIES`: `send_alert` is called; message is NOT deleted (SQS DLQs it naturally)
- [ ] `send_alert` logs `[ALERT]` to stderr; publishes to SNS if `ALERT_SNS_TOPIC_ARN` is set
- [ ] `python worker/dlq_handler.py` polls `DLQ_QUEUE_URL`, logs, alerts, and deletes each message
- [ ] `pydantic` is in `requirements.txt`
- [ ] `MAX_RETRIES` is read from env var with default 3
- [ ] All existing tests (82) still pass
- [ ] No AWS credentials appear in any source file
