---
description: Writes and runs tests for a doc-processor feature. Pass the spec name as argument e.g. /test-doc-processor 09-document-processing-service
allowed-tools: Bash(python -m pytest)
---

Run the full testing pipeline for the doc-processor feature specified
in $ARGUMENTS.

If no argument is provided, stop immediately and say:
"Please provide a spec name. Usage: /test-doc-processor
<spec-name> e.g. /test-doc-processor 09-document-processing-service"

If `.claude/specs/$ARGUMENTS.md` does not exist, stop
immediately and say:
"Spec file not found at .claude/specs/$ARGUMENTS.md.
Please check the spec name and try again."

---

## Step 1: Write Tests

Invoke the **spendly-test-writer** subagent with the
following context:

- Spec file to base tests on:
  `.claude/specs/$ARGUMENTS.md`
- Source files to read for structure:
  - `doc-processor/app/__init__.py`
  - `doc-processor/app/config.py`
  - `doc-processor/app/db.py`
  - `doc-processor/app/models/document.py`
  - `doc-processor/app/services/consumer.py`
  - `doc-processor/app/services/s3_client.py`
  - `doc-processor/app/services/extractor.py`
  - `doc-processor/app/routes/health.py`
  - `doc-processor/app/routes/documents.py`
  - `doc-processor/tests/conftest.py` (for existing fixture patterns)
- Output test file to create:
  `doc-processor/tests/test_$ARGUMENTS.py`
- Instruction: Write tests based on what the spec says
  the feature SHOULD do. Do NOT derive test logic from
  reading the implementation. Cover:
  - All routes (happy path + error cases)
  - Consumer message processing (valid, invalid JSON, schema errors, S3 errors, retry logic)
  - Text extractor (PDF, plain text, unsupported types)
  - DB side effects (rows created, status transitions)
  Use moto's `@mock_aws` for SQS and S3 mocking — NOT unittest.mock.patch on boto3.
  Follow the fixture patterns already established in `doc-processor/tests/conftest.py`.

Wait for spendly-test-writer to fully complete and
confirm the test file has been written before
proceeding to Step 2.

---

## Step 2: Run Tests

Once spendly-test-writer has finished, invoke the
**spendly-test-runner** subagent with the following
context:

- Test file to execute:
  `doc-processor/tests/test_$ARGUMENTS.py`
- Spec file for context:
  `.claude/specs/$ARGUMENTS.md`
- Source files to analyze against when diagnosing failures:
  - `doc-processor/app/` (all files)
  - `doc-processor/tests/conftest.py`
- Run command (must be run from inside doc-processor/):
  `cd doc-processor && python -m pytest tests/test_$ARGUMENTS.py -v`
- Instruction: Run ONLY the specified test file. Do
  NOT run the full test suite. Analyze any failures by
  cross-referencing the test code, the spec, and the
  source files. Classify each failure as a bug in the
  implementation or a test written against incorrect
  assumptions.

---

## Handoff Rules

- Do NOT start Step 2 until Step 1 is fully complete
- Do NOT attempt to fix any code regardless of what
  the test results show
- Do NOT run any tests beyond `doc-processor/tests/test_$ARGUMENTS.py`
- If spendly-test-writer reports it could not write
  the test file, stop and report the reason — do NOT
  proceed to Step 2

---

## Final Output

After both subagents complete, produce a combined
summary:

### Testing Pipeline Report — $ARGUMENTS

**Step 1 — Tests Written**
- List each test written with a one-line description
  of which spec requirement it validates

**Step 2 — Test Results**
- Mirror the spendly-test-runner's structured report

**Verdict**
One of:
- ✅ Ready for code review — all tests pass
- ❌ Needs fixes — list the failing tests and their root causes
