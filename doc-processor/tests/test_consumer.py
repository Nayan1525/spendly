import json
import pytest
import boto3
from moto import mock_aws
from unittest.mock import patch

from app.services.consumer import process_message, backoff_seconds, MAX_RETRIES
from app.db import get_db


VALID_BODY = json.dumps({
    "document_id":  "doc-001",
    "bucket":       "doc-bucket",
    "key":          "sample.txt",
    "requested_by": "user-1",
    "requested_at": "2026-06-30T10:00:00Z",
})


class TestBackoff:
    def test_increases_with_attempt(self):
        assert backoff_seconds(1) < backoff_seconds(2)

    def test_capped_at_300(self):
        assert backoff_seconds(100) == 300

    def test_first_attempt(self):
        assert backoff_seconds(1) == 10


class TestProcessMessage:

    def test_valid_message_creates_completed_row(self, app, mock_s3):
        with app.app_context():
            with mock_aws():
                s3 = boto3.client("s3", region_name="us-east-1")
                s3.create_bucket(Bucket="doc-bucket")
                s3.put_object(Bucket="doc-bucket", Key="sample.txt", Body=b"hello world")

                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                process_message(VALID_BODY)

                db = get_db()
                row = db.execute(
                    "SELECT * FROM documents WHERE document_id=?", ("doc-001",)
                ).fetchone()
                db.close()

            assert row is not None
            assert row["status"] == "completed"
            assert row["word_count"] == 2

    def test_invalid_json_raises_value_error(self, app):
        with app.app_context():
            with pytest.raises(ValueError, match="invalid JSON"):
                process_message("not json at all {{{")

    def test_schema_validation_error_raises_value_error(self, app):
        with app.app_context():
            bad_body = json.dumps({"document_id": "", "bucket": "b", "key": "k",
                                   "requested_by": "u", "requested_at": "t"})
            with pytest.raises(ValueError, match="schema validation failed"):
                process_message(bad_body)

    def test_missing_field_raises_value_error(self, app):
        with app.app_context():
            bad_body = json.dumps({"bucket": "b", "key": "k"})
            with pytest.raises(ValueError):
                process_message(bad_body)

    def test_s3_download_error_raises(self, app):
        with app.app_context():
            from app.services.s3_client import S3DownloadError
            with patch("app.services.consumer.download_document",
                       side_effect=S3DownloadError("not found")):
                with pytest.raises(S3DownloadError):
                    process_message(VALID_BODY)

    def test_description_none_stored_correctly(self, app):
        with app.app_context():
            with mock_aws():
                s3 = boto3.client("s3", region_name="us-east-1")
                s3.create_bucket(Bucket="doc-bucket")
                s3.put_object(Bucket="doc-bucket", Key="sample.txt", Body=b"test content")

                from app import config as cfg
                cfg.settings.S3_ENDPOINT_URL = ""

                process_message(VALID_BODY)

                db = get_db()
                row = db.execute(
                    "SELECT error_message FROM documents WHERE document_id=?",
                    ("doc-001",)
                ).fetchone()
                db.close()

            assert row["error_message"] is None
