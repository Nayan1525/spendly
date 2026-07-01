# Spec: Document Processing Service

## Overview
A standalone microservice (`doc-processor/`) that sits alongside the Spendly Flask
app. It listens to an AWS SQS queue, downloads documents from S3, extracts text and
metadata, and stores results in a dedicated SQLite database. A small Flask API lets
you query those results. The whole thing runs in Docker. This spec deliberately reuses
patterns already established in Spendly (Flask, SQLite, `get_db()`, boto3, Pydantic)
so you can focus on the new concepts: background workers, S3 integration, Docker
networking, and structured logging.

## What you will learn
- How a background worker (SQS consumer) runs alongside a Flask request-handler
- How to model retry logic with visibility-timeout manipulation
- How to stream files from S3 without loading them fully into memory
- How structured logging (structlog) differs from print/logging
- How to wire services together with docker-compose
- How to mock AWS services locally with moto in pytest

---

## Depends on
- Step 08: SQS pattern (producer/consumer/DLQ) already established in this project
- Familiarity with Flask routes and SQLite from Steps 1–7

---

## Project structure

```
doc-processor/
├── app/
│   ├── __init__.py          # Flask app factory (create_app)
│   ├── config.py            # pydantic-settings BaseSettings — all env vars here
│   ├── logging_config.py    # structlog setup
│   ├── db.py                # get_db(), init_db() — same pattern as Spendly
│   ├── models/
│   │   └── document.py      # Pydantic schema for SQS message + DB row dict helpers
│   ├── services/
│   │   ├── s3_client.py     # download_document(bucket, key) → bytes
│   │   ├── extractor.py     # extract(content, mime_type) → dict
│   │   └── consumer.py      # SQS polling loop — run as a thread
│   └── routes/
│       ├── health.py        # GET /health, GET /ready
│       └── documents.py     # GET /documents, GET /documents/<document_id>
├── tests/
│   ├── conftest.py
│   ├── test_routes.py
│   ├── test_consumer.py
│   └── test_extractor.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Routes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | none | Always 200 `{"status":"ok"}` |
| GET | `/ready` | none | 200 if DB reachable + consumer running; 503 otherwise |
| GET | `/documents` | none | List documents with optional filters + pagination |
| GET | `/documents/<document_id>` | none | Fetch single document by UUID; 404 if not found |

### `GET /documents` query params
- `status` — filter by status (pending/processing/completed/failed)
- `requested_by` — filter by submitter identifier
- `limit` — default 20, max 100
- `skip` — default 0 (offset-based pagination)

Response shape:
```json
{
  "items": [...],
  "total": 42,
  "limit": 20,
  "skip": 0
}
```

---

## Database changes

New SQLite database file: `doc-processor/documents.db` (separate from `spendly.db`).

```sql
CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id    TEXT    UNIQUE NOT NULL,   -- UUID from SQS message
    bucket         TEXT    NOT NULL,
    key            TEXT    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'pending',
                                              -- pending|processing|completed|failed
    requested_by   TEXT,
    requested_at   TEXT,
    processed_at   TEXT,
    mime_type      TEXT,
    file_size_bytes INTEGER,
    page_count     INTEGER,                   -- NULL for non-PDFs
    word_count     INTEGER,
    text_preview   TEXT,                      -- first 500 chars
    error_message  TEXT,
    created_at     TEXT    DEFAULT (datetime('now')),
    updated_at     TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_status       ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_requested_by ON documents(requested_by);
```

`get_db()` and `init_db()` follow the exact same pattern as `database/db.py` in
Spendly. `init_db()` is called once at app startup.

---

## SQS message schema (Pydantic)

```python
class DocumentMessage(BaseModel):
    document_id:  str            # UUID4 string
    bucket:       str
    key:          str
    requested_by: str
    requested_at: str            # ISO-8601, e.g. "2026-06-30T10:00:00Z"
```

Validation rules:
- `document_id`: non-empty string (UUID format not strictly enforced — just non-empty)
- `bucket` and `key`: non-empty strings
- All fields required

Invalid messages (JSON parse error or Pydantic validation failure) are **deleted
immediately** from the queue — they must never retry and must never reach the DLQ.
Log them at ERROR level with the raw body.

---

## SQS consumer (`services/consumer.py`)

Run as a **daemon thread** started inside Flask's app factory (not `asyncio` — Flask
is sync, so a thread is simpler and correct here).

```
consumer thread
│
└── while running:
    ├── receive_message(MaxNumberOfMessages=10, WaitTimeSeconds=20,
    │                   AttributeNames=["ApproximateReceiveCount"])
    ├── for each message:
    │   ├── parse + validate (Pydantic)  ← ValidationError → delete, continue
    │   ├── upsert DB row status=processing
    │   ├── download from S3            ← S3DownloadError → visibility backoff
    │   ├── extract text+metadata       ← any error → visibility backoff
    │   ├── update DB row status=completed
    │   └── delete message from SQS
    └── on transient error:
        └── change_message_visibility(VisibilityTimeout=backoff_seconds(attempt))
            where backoff_seconds(n) = min(2**n * 5, 300)
            if attempt >= MAX_RETRIES (3): log ERROR, leave for DLQ
```

The thread must check a `threading.Event` stop flag so it shuts down cleanly when
Flask stops.

---

## S3 client (`services/s3_client.py`)

```python
def download_document(bucket: str, key: str) -> bytes:
    ...
```

- Use `boto3.client("s3")` — synchronous, no `aioboto3` needed (we're not async)
- Raise `S3DownloadError(message)` on `ClientError` or any boto3 exception
- `S3DownloadError` is treated as transient → triggers retry with backoff

---

## Text extractor (`services/extractor.py`)

```python
def extract(content: bytes, filename: str) -> dict:
    ...
```

Returns a dict with keys: `mime_type`, `file_size_bytes`, `page_count`,
`word_count`, `text_preview`.

| Input type | Behaviour |
|---|---|
| PDF (application/pdf) | Use `pypdf` to extract text from all pages; set `page_count` |
| Plain text / markdown | Decode UTF-8; `page_count=None` |
| Any other type | `text_preview="[unsupported type]"`, `word_count=0`, `page_count=None` |

MIME type detection: use `python-magic` on the raw bytes (not the filename).
`word_count` = `len(text.split())` after stripping whitespace.
`text_preview` = first 500 characters of extracted text.

---

## Structured logging (`logging_config.py`)

Use `structlog`. Configure once at app startup.

- `ENV=production` → JSON renderer (one JSON object per line, machine-readable)
- `ENV=development` → `ConsoleRenderer` with colours (human-readable)

Every log call from the consumer must include `document_id` as a bound variable.
Every log call from the routes must include `request_id` (generate a UUID per request
in a `@app.before_request` hook and store in `flask.g`).

Key log events to emit (with their level):
| Event | Level |
|---|---|
| Message received from SQS | INFO |
| Download started / completed | INFO |
| Extraction completed | INFO |
| Message processing failed (transient) | WARNING |
| Message invalid (schema error) | ERROR |
| Message exceeded MAX_RETRIES | ERROR |
| Consumer thread started / stopped | INFO |

---

## Config (`config.py`)

Use `pydantic-settings` `BaseSettings`. All values from environment variables
(with `.env` file support for local dev).

```python
class Settings(BaseSettings):
    MONGO_URI: str = ""           # unused — kept for reference parity
    SQS_QUEUE_URL: str
    SQS_DLQ_URL: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = "test"
    AWS_SECRET_ACCESS_KEY: str = "test"
    S3_ENDPOINT_URL: str = ""     # set to http://localstack:4566 in docker-compose
    LOG_LEVEL: str = "INFO"
    ENV: str = "development"
    DB_PATH: str = "documents.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
```

A single `settings = Settings()` instance is imported wherever needed.

---

## Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y libmagic1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5002
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s \
    CMD curl -f http://localhost:5002/ready || exit 1
CMD ["python", "-m", "flask", "--app", "app", "run", "--host", "0.0.0.0", "--port", "5002"]
```

---

## docker-compose.yml

Services:
- `doc-processor` — the Flask app (built from Dockerfile), port 5002
- `localstack` — `localstack/localstack:latest`, provides SQS + S3 locally
- `init-aws` — a one-shot service that creates the SQS queue and S3 bucket in
  localstack using the AWS CLI (`amazon/aws-cli` image), depends on localstack

Environment for `doc-processor`:
```
SQS_QUEUE_URL=http://localstack:4566/000000000000/doc-queue
S3_ENDPOINT_URL=http://localstack:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_REGION=us-east-1
ENV=development
```

`localstack` healthcheck: `curl -f http://localhost:4566/_localstack/health`.

`doc-processor` depends on `localstack` being healthy.

---

## requirements.txt (new, inside doc-processor/)

```
flask==3.1.3
pydantic==2.7.4
pydantic-settings==2.3.4
boto3==1.34.144
structlog==24.2.0
pypdf==4.3.1
python-magic==0.4.27
pytest==8.3.5
pytest-flask==1.3.0
moto[sqs,s3]==5.0.12
```

---

## Tests

### `tests/conftest.py`
- `app` fixture: `create_app()` with `TESTING=True`, in-memory SQLite
  (`DB_PATH=":memory:"`), monkeypatched `SQS_QUEUE_URL`
- `client` fixture: Flask test client
- `mock_sqs` fixture: `@mock_aws` decorator from moto — creates a real mock
  SQS queue and returns its URL
- `mock_s3` fixture: `@mock_aws` — creates a mock S3 bucket, uploads a sample
  PDF and a sample `.txt` file, returns bucket name

### `tests/test_extractor.py`
- Extracts text from a real minimal PDF (use `pypdf` to create one in the fixture)
- Extracts text from a plain text bytes object
- Returns `[unsupported type]` for binary blob with no recognisable MIME
- `word_count` is correct for known input
- `text_preview` truncates at 500 chars

### `tests/test_consumer.py`
- `process_message()` with valid body: DB row updated to `completed`, message
  deleted from mock SQS
- `process_message()` with invalid JSON: message deleted, no DB row created
- `process_message()` with Pydantic validation error: message deleted, no DB row
- `process_message()` with S3DownloadError: `change_message_visibility` called
  with correct backoff, message NOT deleted
- `process_message()` exceeds MAX_RETRIES: ERROR logged, message left for DLQ

### `tests/test_routes.py`
- `GET /health` → 200 `{"status":"ok"}`
- `GET /ready` → 200 when DB is up, consumer thread is running
- `GET /ready` → 503 when consumer thread is stopped
- `GET /documents` → 200 empty list when no rows
- `GET /documents` → returns rows after insert
- `GET /documents?status=completed` → filters correctly
- `GET /documents/<document_id>` → 200 with correct document
- `GET /documents/<document_id>` → 404 for unknown ID
- Pagination: `?limit=2&skip=1` returns correct slice

---

## New dependencies (additions to doc-processor/requirements.txt only)

`structlog`, `pydantic-settings`, `pypdf`, `python-magic`, `moto[sqs,s3]`, `aioboto3`
is NOT needed (synchronous boto3 is sufficient with Flask threads).

No changes to the top-level Spendly `requirements.txt`.

---

## Rules for implementation

- No SQLAlchemy or ORMs — raw `sqlite3` via `get_db()` only
- Parameterised queries only — never f-string SQL
- No `asyncio` — Flask is synchronous; the consumer runs in a `threading.Thread`
- All config from environment variables via `pydantic-settings` — no hardcoded URLs
- `structlog` for all logging — no bare `print()` in production paths
- All templates (if any) extend `base.html` — not applicable here (API only)
- CSS variables rule — not applicable here (API only)
- The `doc-processor/` service must be runnable independently of the Spendly app
- The Spendly `app.py` must not import anything from `doc-processor/`

---

## Definition of done

- [ ] `docker-compose up` starts localstack and doc-processor without errors
- [ ] `GET http://localhost:5002/health` returns `{"status":"ok"}`
- [ ] `GET http://localhost:5002/ready` returns 200 (DB connected, consumer running)
- [ ] Sending a valid SQS message to the localstack queue causes a row to appear
  in SQLite with `status=completed` within ~5 seconds
- [ ] `GET http://localhost:5002/documents` returns that row
- [ ] `GET http://localhost:5002/documents/<document_id>` returns the full row
- [ ] Sending a malformed SQS message produces an ERROR log and no DB row
- [ ] `pytest tests/` passes with all tests green (moto-backed SQS/S3 tests)
- [ ] Structured logs appear as JSON when `ENV=production`
- [ ] Consumer thread stops cleanly when Flask shuts down (no hanging process)
