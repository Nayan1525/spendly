# Spendly — Claude Code Commands

Custom slash commands for this repo, defined in `.claude/commands/`.
Run any of them from inside a Claude Code session in this project.

---

## Feature workflow

### `/create-spec`
Creates a spec file for the next Spendly curriculum step and switches
you onto a new feature branch. Reads `CLAUDE.md`, `app.py`,
`database/db.py`, and every existing spec to avoid duplicating work,
then writes `.claude/specs/<NN>-<slug>.md` following the project's
spec template (Overview, Routes, DB changes, Templates, Rules, Definition
of done).

**Usage**
```
/create-spec 11 recurring-expenses
/create-spec 2 registration
```
Requires a clean working directory — commit or stash first.

### `/new-endpoint`
Scaffolds a new API endpoint: a Pydantic model, a service function, a
route, and a pytest test file. Detects whether the target app is
FastAPI or Flask and reuses whatever `models/routes/services` convention
already exists in that app; asks before overwriting anything or
guessing which app to target.

**Usage**
```
/new-endpoint expense-category POST
/new-endpoint user-profile GET
```
Only scaffolds files — it never wires the new route into the app for
you (it tells you exactly which line to add).

---

## Testing

### `/test-feature`
Runs the full test pipeline for a main-app Spendly feature: writes
spec-based tests via **spendly-test-writer**, then runs them via
**spendly-test-runner**. Tests are written from what the spec says the
feature *should* do, not from reading the implementation.

**Usage**
```
/test-feature 07-add-expense
/test-feature 05-backend-connection
```
Argument must match an existing file in `.claude/specs/`.

### `/test-doc-processor`
Same pipeline as `/test-feature`, but scoped to the `doc-processor/`
microservice — writes tests into `doc-processor/tests/`, mocks AWS with
moto (`@mock_aws`), and runs pytest from inside `doc-processor/`.

**Usage**
```
/test-doc-processor 09-document-processing-service
```

---

## Code review

### `/review-pr`
Reviews a real PR (or your current branch) against the team's Flask
conventions from `CLAUDE.md` — file organization, naming, parameterized
queries, session auth, password hashing, etc. Runs
**spendly-quality-reviewer** and **spendly-security-reviewer** in
parallel over the diff and combines their output into one report.

**Usage**
```
/review-pr 12              # review PR #12 via gh, print the report
/review-pr 12 --comment    # same, then posts the report as a PR comment
/review-pr                 # review the current branch's diff vs main, locally, no gh needed
```
`--comment` always shows you the report and asks before posting —
it's a visible action on the PR.

---

## Data seeding

### `/seed-user`
Inserts one realistic dummy Indian user (random name, email, and a
werkzeug-hashed password) into the database, regenerating on email
collision.

**Usage**
```
/seed-user
```

### `/seed-expense`
Seeds realistic dummy expenses for a specific existing user, spread
across a chosen number of past months with category-weighted amounts
in ₹. Inserted as a single all-or-nothing transaction.

**Usage**
```
/seed-expense <user_id> <count> <months>
/seed-expense 1 50 6
```

---

## Adding a new command

Drop a new `.md` file in `.claude/commands/`, following the pattern
above: YAML frontmatter (`description`, `argument-hint`,
`allowed-tools`), then numbered steps, then a usage-examples section.
Update this file when you do.
