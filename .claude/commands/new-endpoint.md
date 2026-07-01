---
description: Scaffold a new API endpoint (Pydantic model, route, service, test) for FastAPI or Flask
argument-hint: "<resource-name> [http-method] e.g. /new-endpoint expense-category POST"
allowed-tools: Read, Write, Glob, Grep, Bash(git status)
---

You are scaffolding a new API endpoint. This command is
framework-agnostic — it works for both FastAPI and Flask
projects by detecting which one the current repo uses and
generating idiomatic code for that framework.

User input: $ARGUMENTS

## Step 1 — Check working directory is clean

Run `git status`. If there are uncommitted changes, warn
the user that new files will be added on top of them, but
proceed (this command only creates new files, it does not
modify existing ones without confirmation — see Step 6).

## Step 2 — Parse the arguments

From $ARGUMENTS extract:

1. `resource_name` — required. The thing the endpoint
   operates on, e.g. `expense-category`, `user_profile`.
   If missing, stop and say:
   "Please provide a resource name. Usage: /new-endpoint
   <resource-name> [http-method] e.g. /new-endpoint
   expense-category POST"

2. `http_method` — optional, defaults to `GET`. One of
   GET, POST, PUT, PATCH, DELETE (case-insensitive).

Derive these naming variants from `resource_name`:
- `snake_name` — snake_case, singular where natural
  (e.g. `expense_category`)
- `PascalName` — PascalCase for class names
  (e.g. `ExpenseCategory`)
- `kebab-path` — kebab-case, pluralized for the URL path
  (e.g. `expense-categories`)

## Step 3 — Detect the framework

Check, in order:
1. `requirements.txt` / `pyproject.toml` for `fastapi` or
   `flask` as a dependency.
2. Look for a FastAPI entrypoint (`from fastapi import
   FastAPI`) vs a Flask entrypoint (`from flask import
   Flask`) in likely files (`main.py`, `app.py`,
   `wsgi.py`, `asgi.py`).

If both are present, or neither is found, stop and ask the
user which framework to target. Do not guess.

## Step 4 — Detect existing project conventions

Use Glob/Grep to find where things already live. Look for:
- An existing models/schemas directory (e.g. `models/`,
  `schemas/`, `app/schemas/`)
- An existing routes/routers directory (e.g. `routes/`,
  `routers/`, `app/api/`) — for Flask, also check whether
  the project uses Blueprints already
- An existing services directory (e.g. `services/`,
  `app/services/`)
- An existing tests directory (e.g. `tests/`)

If a category has no existing convention, create it under
a sensible default (`app/schemas/`, `app/routers/` or
`app/routes/`, `app/services/`, `tests/`) but tell the user
what default you chose.

If files already exist for this exact resource (e.g.
`schemas/expense_category.py` already present), stop and
ask the user whether to overwrite, rename, or abort. Never
overwrite silently.

## Step 5 — Generate the files

### 5a. Pydantic model
Create a schema file with:
- A `<PascalName>Base` model with the core fields
  (infer 2-4 plausible fields from the resource name, and
  mark them clearly as placeholders to adjust)
- A `<PascalName>Create` model (request body for
  POST/PUT/PATCH) extending `Base`
- A `<PascalName>Response` model extending `Base` plus
  `id: int` and `created_at: datetime`, with
  `model_config = ConfigDict(from_attributes=True)`

### 5b. Service layer
Create a service file with a function that implements the
business logic for `http_method`, e.g.
`def create_expense_category(data: ExpenseCategoryCreate) -> ExpenseCategoryResponse:`.
Body should be a clear `# TODO:` stub describing what
persistence call belongs there — do not invent a fake
database call, since the real data layer is project
specific.

### 5c. Route
**FastAPI** — an `APIRouter()` in the routes/routers file,
one endpoint decorated with `@router.<method>("/<kebab-path>")`,
typed with the Pydantic request/response models, that
calls the service function. Note in a comment that this
router must be included in the app with
`app.include_router(...)` if not already wired.

**Flask** — a `Blueprint` (reuse an existing one if the
project already has a convention, otherwise create
`<snake_name>_bp`), one `@bp.route("/<kebab-path>",
methods=["<HTTP_METHOD>"])` view function that validates
`request.get_json()` against the `Create` Pydantic model
(catch `pydantic.ValidationError` and return 400 with the
error detail), calls the service function, and returns
`jsonify(...)`. Note in a comment that this blueprint must
be registered with `app.register_blueprint(...)` if not
already wired.

### 5d. Test
Create a pytest test file that:
- Uses `TestClient` (FastAPI) or the Flask test client
  fixture already used elsewhere in the project's
  `tests/` directory (check an existing test file for the
  fixture pattern before inventing a new one)
- Covers: happy path (valid request → expected status
  code + response shape) and one validation-error case
  (missing/invalid field → 400/422)

## Step 6 — Report to the user

Print a summary in this exact format:

```
Framework:  <FastAPI|Flask>
Resource:   <resource_name>
Method:     <HTTP_METHOD>

Created:
  <path to model file>
  <path to service file>
  <path to route file>
  <path to test file>

Wiring needed:
  <one line telling the user exactly where to add
   include_router(...) or register_blueprint(...), with
   the file and line context, if it isn't already wired>
```

Then remind the user: "Review the placeholder fields in
the Pydantic model and the TODO in the service function —
they're inferred from the resource name and need to match
your real data."

Do not run the test file automatically — let the user
choose when to run it.
