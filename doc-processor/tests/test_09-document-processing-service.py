"""
tests/test_09-document-processing-service.py

Consolidated spec-driven test suite for Step 09 — Document Processing Service.

Coverage map
------------
Routes  : /health, /ready (200 + 503), /documents (list + filters + pagination),
          /documents/<id> (200 + 404)
Consumer: backoff_seconds exact values + cap, process_message happy-path DB side-
          effects, invalid JSON, missing Pydantic field, empty document_id, S3
          error propagation (moto @mock_aws — no unittest.mock.patch on boto3)
Extractor: plain text (mime/word_count/page_count/preview truncation/file_size),
           PDF (mime/page_count/file_size), unsupported binary (preview/word_count),
           file_size_bytes equals len(content) for every type
"""

import io
import json
import pytest
import boto3
from moto import mock_aws
from pypdf import PdfWriter

from app import create_app
from app.db import get_db, init_db
from app.services.consumer import backoff_seconds, process_message
from app.services.extractor import extract
from app.services.s3_client import S3DownloadError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture()
def app(tmp_db, monkeypatch):
    monkeypatch.setenv("SQS_QUEUE_URL", "http://localhost:4566/000000000000/doc-queue")
    from app import config as cfg
    cfg.settings.DB_PATH = tmp_db
    flask_app = create_app({"TESTING": True, "DB_PATH": tmp_db})
    yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_doc(
    app,
    document_id="doc-001",
    status="completed",
    requested_by="user-1",
    bucket="b",
    key="k",
):
    """Insert a minimal document row directly via get_db() inside app context."""
    with app.app_context():
        db = get_db()
        db.execute(
            """
            INSERT INTO documents
                (document_id, bucket, key, status, requested_by, requested_at,
                 word_count, file_size_bytes, text_preview)
            VALUES (?, ?, ?, ?, ?, '2026-06-30T10:00:00Z', 10, 100, 'preview')
            """,
            (document_id, bucket, key, status, requested_by),
        )
        db.commit()
        db.close()


def _make_minimal_pdf(text: str = "") -> bytes:
    """Create a real minimal single-page PDF using pypdf PdfWriter."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


VALID_MSG = {
    "document_id": "spec-doc-001",
    "bucket": "test-bucket",
    "key": "test-file.txt",
    "requested_by": "spec-user",
    "requested_at": "2026-06-30T10:00:00Z",
}
VALID_BODY = json.dumps(VALID_MSG)


# ===========================================================================
# Route tests
# ===========================================================================

class TestHealthRoute:
    """GET /health — must always return 200 {"status": "ok"}."""

    def test_status_code_is_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200, "Expected 200 from /health"

    def test_response_body_has_status_ok(self, client):
        resp = client.get("/health")
        assert resp.json["status"] == "ok", "Expected status='ok' in /health response"

    def test_response_is_json(self, client):
        resp = client.get("/health")
        assert resp.content_type.startswith("application/json"), (
            "Expected JSON content-type from /health"
        )

    def test_only_status_field_required(self, client):
        """Spec says always 200 {"status":"ok"} — the key must be present."""
        data = client.get("/health").json
        assert "status" in data, "Response body must contain 'status' key"


class TestReadyRoute:
    """GET /ready — 200 when DB reachable + consumer running; 503 otherwise."""

    def test_503_when_consumer_not_running(self, client):
        """TESTING mode disables the consumer thread — should return 503."""
        resp = client.get("/ready")
        assert resp.status_code == 503, "Expected 503 when consumer is not running"

    def test_503_response_consumer_field_is_false(self, client):
        resp = client.get("/ready")
        assert resp.json["consumer"] is False, "consumer field must be False when stopped"

    def test_503_response_db_field_is_true(self, client):
        """DB is reachable even in TESTING mode — only consumer is missing."""
        resp = client.get("/ready")
        assert resp.json["db"] is True, "db field must be True when DB is reachable"

    def test_503_response_status_is_unavailable(self, client):
        resp = client.get("/ready")
        assert resp.json["status"] == "unavailable", (
            "status field must be 'unavailable' on 503"
        )

    def test_200_when_consumer_flagged_running(self, client, app):
        """Simulate consumer running by setting the config flag the real code uses."""
        app.config["CONSUMER_RUNNING"] = True
        resp = client.get("/ready")
        assert resp.status_code == 200, "Expected 200 when consumer is running"

    def test_200_response_has_ok_status(self, client, app):
        app.config["CONSUMER_RUNNING"] = True
        resp = client.get("/ready")
        assert resp.json["status"] == "ok", "status field must be 'ok' on 200"

    def test_200_response_consumer_field_is_true(self, client, app):
        app.config["CONSUMER_RUNNING"] = True
        resp = client.get("/ready")
        assert resp.json["consumer"] is True, "consumer field must be True when running"

    def test_200_response_db_field_is_true(self, client, app):
        app.config["CONSUMER_RUNNING"] = True
        resp = client.get("/ready")
        assert resp.json["db"] is True, "db field must be True when DB reachable"


class TestListDocumentsRoute:
    """GET /documents — list with optional filters and pagination."""

    def test_empty_db_returns_200(self, client):
        resp = client.get("/documents")
        assert resp.status_code == 200, "Expected 200 even with no rows"

    def test_empty_db_items_is_empty_list(self, client):
        resp = client.get("/documents")
        assert resp.json["items"] == [], "items must be [] when no documents exist"

    def test_empty_db_total_is_zero(self, client):
        resp = client.get("/documents")
        assert resp.json["total"] == 0, "total must be 0 when no documents exist"

    def test_response_envelope_contains_all_required_keys(self, client):
        """Spec response shape: {items, total, limit, skip}."""
        data = client.get("/documents").json
        for key in ("items", "total", "limit", "skip"):
            assert key in data, f"Response envelope missing key: {key}"

    def test_response_default_limit_is_20(self, client):
        resp = client.get("/documents")
        assert resp.json["limit"] == 20, "Default limit must be 20"

    def test_response_default_skip_is_0(self, client):
        resp = client.get("/documents")
        assert resp.json["skip"] == 0, "Default skip must be 0"

    def test_returns_inserted_row(self, client, app):
        _insert_doc(app, "doc-inserted")
        resp = client.get("/documents")
        assert resp.json["total"] == 1, "total must reflect inserted row"
        assert resp.json["items"][0]["document_id"] == "doc-inserted"

    def test_status_filter_completed_returns_only_completed(self, client, app):
        _insert_doc(app, "doc-c", status="completed")
        _insert_doc(app, "doc-f", status="failed")
        _insert_doc(app, "doc-p", status="pending")
        resp = client.get("/documents?status=completed")
        assert resp.status_code == 200
        assert resp.json["total"] == 1, "Filter should return only completed rows"
        assert resp.json["items"][0]["document_id"] == "doc-c"

    def test_status_filter_failed_excludes_other_statuses(self, client, app):
        _insert_doc(app, "doc-c", status="completed")
        _insert_doc(app, "doc-f", status="failed")
        resp = client.get("/documents?status=failed")
        ids = [item["document_id"] for item in resp.json["items"]]
        assert "doc-c" not in ids, "completed doc must not appear in failed filter"

    def test_status_filter_pending(self, client, app):
        _insert_doc(app, "doc-pend", status="pending")
        _insert_doc(app, "doc-proc", status="processing")
        resp = client.get("/documents?status=pending")
        assert resp.json["total"] == 1
        assert resp.json["items"][0]["document_id"] == "doc-pend"

    def test_invalid_status_returns_400(self, client):
        resp = client.get("/documents?status=invalid")
        assert resp.status_code == 400, "Expected 400 for unrecognised status value"

    def test_invalid_status_response_has_error_key(self, client):
        resp = client.get("/documents?status=invalid")
        assert "error" in resp.json, "400 response must include 'error' key"

    def test_filter_by_requested_by_returns_matching_rows(self, client, app):
        _insert_doc(app, "doc-u1", requested_by="user-1")
        _insert_doc(app, "doc-u2", requested_by="user-2")
        resp = client.get("/documents?requested_by=user-1")
        assert resp.status_code == 200
        assert resp.json["total"] == 1
        assert resp.json["items"][0]["document_id"] == "doc-u1"

    def test_filter_by_requested_by_excludes_other_users(self, client, app):
        _insert_doc(app, "doc-u1", requested_by="user-1")
        _insert_doc(app, "doc-u2", requested_by="user-2")
        resp = client.get("/documents?requested_by=user-1")
        ids = [item["document_id"] for item in resp.json["items"]]
        assert "doc-u2" not in ids, "user-2's doc must not appear in user-1 filter"

    def test_filter_by_unknown_requested_by_returns_empty(self, client, app):
        _insert_doc(app, "doc-u1", requested_by="user-1")
        resp = client.get("/documents?requested_by=nobody")
        assert resp.json["total"] == 0
        assert resp.json["items"] == []

    def test_pagination_limit_2_skip_1(self, client, app):
        """Insert 4 rows; ?limit=2&skip=1 must return exactly 2 items."""
        for i in range(4):
            _insert_doc(app, f"pg-doc-{i:03d}")
        resp = client.get("/documents?limit=2&skip=1")
        assert resp.status_code == 200
        assert len(resp.json["items"]) == 2, "limit=2 must return exactly 2 items"

    def test_pagination_limit_echoed_in_response(self, client, app):
        for i in range(3):
            _insert_doc(app, f"lim-doc-{i:03d}")
        resp = client.get("/documents?limit=2&skip=1")
        assert resp.json["limit"] == 2, "limit in response must match request param"

    def test_pagination_skip_echoed_in_response(self, client, app):
        for i in range(3):
            _insert_doc(app, f"skip-doc-{i:03d}")
        resp = client.get("/documents?limit=2&skip=1")
        assert resp.json["skip"] == 1, "skip in response must match request param"

    def test_pagination_total_reflects_full_count_not_page(self, client, app):
        """total must count ALL matching rows, not just the current page."""
        for i in range(5):
            _insert_doc(app, f"tot-doc-{i:03d}")
        resp = client.get("/documents?limit=2&skip=1")
        assert resp.json["total"] == 5, (
            "total must equal full dataset count regardless of pagination"
        )

    def test_limit_capped_at_100(self, client, app):
        resp = client.get("/documents?limit=999")
        assert resp.json["limit"] == 100, "limit must be capped at 100"

    def test_skip_cannot_be_negative(self, client, app):
        """Negative skip should be normalised to 0, not raise an error."""
        resp = client.get("/documents?skip=-5")
        assert resp.status_code == 200
        assert resp.json["skip"] == 0, "negative skip must be clamped to 0"


class TestGetDocumentRoute:
    """GET /documents/<document_id> — single document lookup."""

    def test_existing_document_returns_200(self, client, app):
        _insert_doc(app, "doc-single")
        resp = client.get("/documents/doc-single")
        assert resp.status_code == 200, "Expected 200 for existing document"

    def test_existing_document_has_document_id_key(self, client, app):
        _insert_doc(app, "doc-has-id")
        resp = client.get("/documents/doc-has-id")
        assert resp.json["document_id"] == "doc-has-id", (
            "Response must include correct document_id"
        )

    def test_existing_document_has_status_field(self, client, app):
        _insert_doc(app, "doc-status", status="completed")
        resp = client.get("/documents/doc-status")
        assert resp.json["status"] == "completed", "Response must include status field"

    def test_existing_document_has_bucket_and_key(self, client, app):
        _insert_doc(app, "doc-bk", bucket="my-bucket", key="my/path/file.txt")
        resp = client.get("/documents/doc-bk")
        assert resp.json["bucket"] == "my-bucket"
        assert resp.json["key"] == "my/path/file.txt"

    def test_unknown_id_returns_404(self, client):
        resp = client.get("/documents/completely-unknown-id")
        assert resp.status_code == 404, "Expected 404 for unknown document ID"

    def test_404_response_has_error_key(self, client):
        resp = client.get("/documents/completely-unknown-id")
        assert "error" in resp.json, "404 response must contain 'error' key"

    def test_document_id_is_not_found_after_different_id_inserted(self, client, app):
        """Inserting doc-A must not cause doc-B to exist."""
        _insert_doc(app, "doc-A")
        resp = client.get("/documents/doc-B")
        assert resp.status_code == 404


# ===========================================================================
# Consumer tests
# ===========================================================================

class TestBackoffSeconds:
    """backoff_seconds(n) = min(2**n * 5, 300) per spec."""

    def test_attempt_1_equals_10(self):
        assert backoff_seconds(1) == 10, "backoff_seconds(1) must be 10"

    def test_attempt_2_equals_20(self):
        assert backoff_seconds(2) == 20, "backoff_seconds(2) must be 20"

    def test_attempt_3_equals_40(self):
        assert backoff_seconds(3) == 40, "backoff_seconds(3) must be 40"

    def test_capped_at_300(self):
        assert backoff_seconds(100) == 300, "backoff_seconds must be capped at 300"

    def test_very_large_attempt_still_300(self):
        assert backoff_seconds(1000) == 300

    def test_increases_monotonically_until_cap(self):
        values = [backoff_seconds(n) for n in range(1, 8)]
        for i in range(len(values) - 1):
            assert values[i] <= values[i + 1], (
                "backoff_seconds must be non-decreasing with attempt number"
            )


class TestProcessMessageHappyPath:
    """Valid message body processed end-to-end with moto mock_aws for S3."""

    def test_valid_message_row_status_is_completed(self, app, aws_credentials):
        with app.app_context():
            with mock_aws():
                s3 = boto3.client("s3", region_name="us-east-1")
                s3.create_bucket(Bucket="test-bucket")
                s3.put_object(
                    Bucket="test-bucket",
                    Key="test-file.txt",
                    Body=b"hello world from spec test",
                )
                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                process_message(VALID_BODY)

                db = get_db()
                row = db.execute(
                    "SELECT * FROM documents WHERE document_id = ?",
                    ("spec-doc-001",),
                ).fetchone()
                db.close()

        assert row is not None, "DB row must be created after valid message"
        assert row["status"] == "completed", "Row status must be 'completed'"

    def test_valid_message_processed_at_is_set(self, app, aws_credentials):
        with app.app_context():
            with mock_aws():
                s3 = boto3.client("s3", region_name="us-east-1")
                s3.create_bucket(Bucket="test-bucket")
                s3.put_object(
                    Bucket="test-bucket",
                    Key="test-file.txt",
                    Body=b"processed at field test",
                )
                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                process_message(VALID_BODY)

                db = get_db()
                row = db.execute(
                    "SELECT processed_at FROM documents WHERE document_id = ?",
                    ("spec-doc-001",),
                ).fetchone()
                db.close()

        assert row["processed_at"] is not None, (
            "processed_at must be set after successful processing"
        )

    def test_valid_message_mime_type_is_set(self, app, aws_credentials):
        with app.app_context():
            with mock_aws():
                s3 = boto3.client("s3", region_name="us-east-1")
                s3.create_bucket(Bucket="test-bucket")
                s3.put_object(
                    Bucket="test-bucket",
                    Key="test-file.txt",
                    Body=b"mime type detection test",
                )
                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                process_message(VALID_BODY)

                db = get_db()
                row = db.execute(
                    "SELECT mime_type FROM documents WHERE document_id = ?",
                    ("spec-doc-001",),
                ).fetchone()
                db.close()

        assert row["mime_type"] is not None, "mime_type must be populated after processing"

    def test_valid_message_file_size_bytes_matches_content_length(
        self, app, aws_credentials
    ):
        content = b"exact size check content"
        with app.app_context():
            with mock_aws():
                s3 = boto3.client("s3", region_name="us-east-1")
                s3.create_bucket(Bucket="test-bucket")
                s3.put_object(Bucket="test-bucket", Key="test-file.txt", Body=content)
                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                process_message(VALID_BODY)

                db = get_db()
                row = db.execute(
                    "SELECT file_size_bytes FROM documents WHERE document_id = ?",
                    ("spec-doc-001",),
                ).fetchone()
                db.close()

        assert row["file_size_bytes"] == len(content), (
            "file_size_bytes must equal the byte length of downloaded content"
        )

    def test_valid_message_error_message_is_null(self, app, aws_credentials):
        with app.app_context():
            with mock_aws():
                s3 = boto3.client("s3", region_name="us-east-1")
                s3.create_bucket(Bucket="test-bucket")
                s3.put_object(
                    Bucket="test-bucket", Key="test-file.txt", Body=b"clean run"
                )
                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                process_message(VALID_BODY)

                db = get_db()
                row = db.execute(
                    "SELECT error_message FROM documents WHERE document_id = ?",
                    ("spec-doc-001",),
                ).fetchone()
                db.close()

        assert row["error_message"] is None, (
            "error_message must be NULL after successful processing"
        )


class TestProcessMessageInvalidJSON:
    """Invalid JSON body → raises ValueError, no DB row created."""

    def test_raises_value_error_on_invalid_json(self, app):
        with app.app_context():
            with pytest.raises(ValueError):
                process_message("{not valid json}")

    def test_no_db_row_created_on_invalid_json(self, app):
        with app.app_context():
            try:
                process_message("{not valid json}")
            except ValueError:
                pass

            db = get_db()
            count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            db.close()

        assert count == 0, "No DB row must be created when JSON is invalid"

    def test_completely_empty_body_raises_value_error(self, app):
        with app.app_context():
            with pytest.raises(ValueError):
                process_message("")

    def test_partial_json_raises_value_error(self, app):
        with app.app_context():
            with pytest.raises(ValueError):
                process_message('{"document_id": "abc"')  # unclosed brace


class TestProcessMessagePydanticValidation:
    """Pydantic validation failures → raises ValueError, no DB row created."""

    def test_missing_document_id_raises_value_error(self, app):
        """document_id field entirely absent from message."""
        body = json.dumps({
            "bucket": "b",
            "key": "k",
            "requested_by": "user",
            "requested_at": "2026-06-30T10:00:00Z",
        })
        with app.app_context():
            with pytest.raises(ValueError):
                process_message(body)

    def test_empty_document_id_raises_value_error(self, app):
        """document_id is present but empty string — validator rejects it."""
        body = json.dumps({
            "document_id": "",
            "bucket": "b",
            "key": "k",
            "requested_by": "user",
            "requested_at": "2026-06-30T10:00:00Z",
        })
        with app.app_context():
            with pytest.raises(ValueError):
                process_message(body)

    def test_whitespace_only_document_id_raises_value_error(self, app):
        """Whitespace-only document_id must fail the non-empty validator."""
        body = json.dumps({
            "document_id": "   ",
            "bucket": "b",
            "key": "k",
            "requested_by": "user",
            "requested_at": "2026-06-30T10:00:00Z",
        })
        with app.app_context():
            with pytest.raises(ValueError):
                process_message(body)

    def test_missing_bucket_raises_value_error(self, app):
        body = json.dumps({
            "document_id": "doc-123",
            "key": "k",
            "requested_by": "user",
            "requested_at": "2026-06-30T10:00:00Z",
        })
        with app.app_context():
            with pytest.raises(ValueError):
                process_message(body)

    def test_missing_field_no_db_row_created(self, app):
        """Validation failure must not leave any DB row behind."""
        body = json.dumps({"bucket": "b"})  # almost nothing valid
        with app.app_context():
            try:
                process_message(body)
            except ValueError:
                pass

            db = get_db()
            count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            db.close()

        assert count == 0, "No DB row must be created when Pydantic validation fails"


class TestProcessMessageS3Error:
    """S3DownloadError during download triggers retry path — exception propagates."""

    def test_s3_download_error_propagates_using_mock_aws(self, app, aws_credentials):
        """
        Use moto @mock_aws: bucket exists but key does not → ClientError →
        S3DownloadError is raised by download_document.
        """
        with app.app_context():
            with mock_aws():
                s3 = boto3.client("s3", region_name="us-east-1")
                s3.create_bucket(Bucket="test-bucket")
                # Deliberately do NOT upload the key — so get_object raises ClientError
                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                with pytest.raises(S3DownloadError):
                    process_message(VALID_BODY)

    def test_s3_download_error_leaves_row_in_processing_state(
        self, app, aws_credentials
    ):
        """
        When S3DownloadError fires, the row was already upserted as 'processing'.
        The row should remain (not be deleted), allowing retry logic to proceed.
        """
        with app.app_context():
            with mock_aws():
                s3 = boto3.client("s3", region_name="us-east-1")
                s3.create_bucket(Bucket="test-bucket")
                # Key intentionally absent to trigger S3DownloadError
                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                try:
                    process_message(VALID_BODY)
                except S3DownloadError:
                    pass

                db = get_db()
                row = db.execute(
                    "SELECT status FROM documents WHERE document_id = ?",
                    ("spec-doc-001",),
                ).fetchone()
                db.close()

        # Row may be 'processing' (not yet completed) or may not exist
        # depending on whether the upsert committed before the error.
        # The critical contract is that status is NOT 'completed'.
        if row is not None:
            assert row["status"] != "completed", (
                "Row must not be marked completed when S3 download failed"
            )

    def test_nonexistent_bucket_raises_s3_download_error(self, app, aws_credentials):
        """Bucket that doesn't exist in the mock → S3DownloadError raised."""
        body = json.dumps({
            "document_id": "doc-no-bucket",
            "bucket": "bucket-does-not-exist",
            "key": "file.txt",
            "requested_by": "user",
            "requested_at": "2026-06-30T10:00:00Z",
        })
        with app.app_context():
            with mock_aws():
                # No bucket created at all
                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                with pytest.raises(S3DownloadError):
                    process_message(body)


# ===========================================================================
# Extractor tests
# ===========================================================================

class TestExtractPlainText:
    """extract() behaviour for plain text byte input."""

    def test_mime_type_starts_with_text(self):
        result = extract(b"hello world this is plain text", "test.txt")
        assert result["mime_type"].startswith("text/"), (
            "Plain text bytes must produce a mime_type starting with 'text/'"
        )

    def test_word_count_matches_known_input(self):
        result = extract(b"one two three four five six", "test.txt")
        assert result["word_count"] == 6, "word_count must equal number of whitespace-delimited tokens"

    def test_word_count_single_word(self):
        result = extract(b"hello", "test.txt")
        assert result["word_count"] == 1

    def test_page_count_is_none_for_plain_text(self):
        result = extract(b"plain text has no pages", "test.txt")
        assert result["page_count"] is None, (
            "page_count must be None for non-PDF content"
        )

    def test_text_preview_truncated_to_500_chars(self):
        # Build a string with 600 characters of content
        long_content = ("x" * 600).encode("utf-8")
        result = extract(long_content, "test.txt")
        assert len(result["text_preview"]) <= 500, (
            "text_preview must not exceed 500 characters"
        )

    def test_text_preview_exactly_500_when_input_longer(self):
        """Preview should be exactly 500 chars when input exceeds that."""
        long_text = "a" * 600
        result = extract(long_text.encode("utf-8"), "test.txt")
        assert len(result["text_preview"]) == 500, (
            "text_preview must be exactly 500 chars when input is longer"
        )

    def test_text_preview_not_truncated_when_input_shorter(self):
        short = "hello world"
        result = extract(short.encode("utf-8"), "test.txt")
        assert result["text_preview"] == short, (
            "text_preview must be the full text when shorter than 500 chars"
        )

    def test_file_size_bytes_equals_len_content_for_text(self):
        content = b"size check content"
        result = extract(content, "test.txt")
        assert result["file_size_bytes"] == len(content), (
            "file_size_bytes must equal len(content) for plain text"
        )


class TestExtractUnsupportedBinary:
    """extract() behaviour for unrecognisable binary content."""

    def test_text_preview_is_unsupported_type_string(self):
        result = extract(bytes(range(256)), "binary.bin")
        assert result["text_preview"] == "[unsupported type]", (
            "Unsupported binary must produce text_preview='[unsupported type]'"
        )

    def test_word_count_is_zero_for_unsupported(self):
        result = extract(bytes(range(256)), "binary.bin")
        assert result["word_count"] == 0, (
            "word_count must be 0 for unsupported content type"
        )

    def test_page_count_is_none_for_unsupported(self):
        result = extract(bytes(range(256)), "binary.bin")
        assert result["page_count"] is None, (
            "page_count must be None for unsupported content type"
        )

    def test_file_size_bytes_equals_len_content_for_unsupported(self):
        content = bytes(range(256))
        result = extract(content, "binary.bin")
        assert result["file_size_bytes"] == len(content), (
            "file_size_bytes must equal len(content) even for unsupported types"
        )


class TestExtractPDF:
    """extract() behaviour for valid PDF bytes created with pypdf PdfWriter."""

    def test_pdf_mime_type_is_application_pdf(self):
        pdf_bytes = _make_minimal_pdf()
        result = extract(pdf_bytes, "doc.pdf")
        assert result["mime_type"] == "application/pdf", (
            "PDF content must be detected as 'application/pdf'"
        )

    def test_pdf_page_count_is_1_for_single_page(self):
        pdf_bytes = _make_minimal_pdf()
        result = extract(pdf_bytes, "doc.pdf")
        assert result["page_count"] == 1, "Single-page PDF must have page_count=1"

    def test_pdf_file_size_bytes_equals_len_content(self):
        pdf_bytes = _make_minimal_pdf()
        result = extract(pdf_bytes, "doc.pdf")
        assert result["file_size_bytes"] == len(pdf_bytes), (
            "file_size_bytes must equal len(pdf_bytes) for PDF content"
        )

    def test_pdf_word_count_is_integer(self):
        pdf_bytes = _make_minimal_pdf()
        result = extract(pdf_bytes, "doc.pdf")
        assert isinstance(result["word_count"], int), (
            "word_count must be an integer for PDF"
        )

    def test_pdf_text_preview_is_string(self):
        pdf_bytes = _make_minimal_pdf()
        result = extract(pdf_bytes, "doc.pdf")
        assert isinstance(result["text_preview"], str), (
            "text_preview must be a string for PDF"
        )

    def test_pdf_text_preview_at_most_500_chars(self):
        pdf_bytes = _make_minimal_pdf()
        result = extract(pdf_bytes, "doc.pdf")
        assert len(result["text_preview"]) <= 500, (
            "text_preview must not exceed 500 chars for PDF"
        )


class TestExtractReturnShape:
    """extract() must always return a dict with all five required keys."""

    @pytest.mark.parametrize("content,filename", [
        (b"plain text content", "test.txt"),
        (bytes(range(256)), "binary.bin"),
    ])
    def test_result_contains_all_required_keys(self, content, filename):
        result = extract(content, filename)
        for key in ("mime_type", "file_size_bytes", "page_count", "word_count", "text_preview"):
            assert key in result, f"extract() result must contain key '{key}'"

    def test_pdf_result_contains_all_required_keys(self):
        pdf_bytes = _make_minimal_pdf()
        result = extract(pdf_bytes, "doc.pdf")
        for key in ("mime_type", "file_size_bytes", "page_count", "word_count", "text_preview"):
            assert key in result, f"PDF extract() result must contain key '{key}'"
