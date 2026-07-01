import json
import threading
import structlog
from datetime import datetime, timezone
from pydantic import ValidationError

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.db import get_db
from app.models.document import DocumentMessage
from app.services.s3_client import download_document, S3DownloadError
from app.services.extractor import extract

logger = structlog.get_logger(__name__)

MAX_RETRIES = 3
_stop_event = threading.Event()


def backoff_seconds(attempt: int) -> int:
    return min(2 ** attempt * 5, 300)


def _sqs_client():
    kwargs = {"region_name": settings.AWS_REGION}
    if settings.S3_ENDPOINT_URL:
        # localstack uses the same endpoint for both SQS and S3
        kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
    return boto3.client(
        "sqs",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        **kwargs,
    )


def process_message(body: str) -> None:
    log = logger.bind()

    # 1. Parse JSON
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.error("message_invalid_json", error=str(exc), body=body[:200])
        raise ValueError("invalid JSON") from exc

    # 2. Pydantic validation
    try:
        msg = DocumentMessage(**data)
    except ValidationError as exc:
        logger.error("message_schema_invalid", error=str(exc), body=body[:200])
        raise ValueError("schema validation failed") from exc

    log = logger.bind(document_id=msg.document_id)
    log.info("message_received", bucket=msg.bucket, key=msg.key)

    # 3. Upsert row as processing
    db = get_db()
    db.execute(
        """
        INSERT INTO documents
            (document_id, bucket, key, status, requested_by, requested_at)
        VALUES (?, ?, ?, 'processing', ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
            status='processing', updated_at=datetime('now')
        """,
        (msg.document_id, msg.bucket, msg.key, msg.requested_by, msg.requested_at),
    )
    db.commit()
    db.close()

    # 4. Download from S3
    content = download_document(msg.bucket, msg.key)

    # 5. Extract text + metadata
    result = extract(content, msg.key)

    # 6. Update row as completed
    db = get_db()
    db.execute(
        """
        UPDATE documents SET
            status='completed',
            processed_at=?,
            mime_type=?,
            file_size_bytes=?,
            page_count=?,
            word_count=?,
            text_preview=?,
            error_message=NULL,
            updated_at=datetime('now')
        WHERE document_id=?
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            result["mime_type"],
            result["file_size_bytes"],
            result["page_count"],
            result["word_count"],
            result["text_preview"],
            msg.document_id,
        ),
    )
    db.commit()
    db.close()
    log.info("message_processed_ok")


def _mark_failed(document_id: str, error: str) -> None:
    try:
        db = get_db()
        db.execute(
            """
            UPDATE documents SET
                status='failed', error_message=?, updated_at=datetime('now')
            WHERE document_id=?
            """,
            (error, document_id),
        )
        db.commit()
        db.close()
    except Exception:
        pass


def _poll_loop() -> None:
    client = _sqs_client()
    logger.info("consumer_thread_started", queue=settings.SQS_QUEUE_URL)

    while not _stop_event.is_set():
        try:
            response = client.receive_message(
                QueueUrl=settings.SQS_QUEUE_URL,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("sqs_receive_error", error=str(exc))
            _stop_event.wait(5)
            continue

        for message in response.get("Messages", []):
            receipt  = message["ReceiptHandle"]
            msg_id   = message.get("MessageId", "unknown")
            attempt  = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
            body     = message.get("Body", "")

            try:
                process_message(body)
                client.delete_message(QueueUrl=settings.SQS_QUEUE_URL, ReceiptHandle=receipt)

            except ValueError:
                # Schema / JSON error — delete immediately, never retry
                client.delete_message(QueueUrl=settings.SQS_QUEUE_URL, ReceiptHandle=receipt)

            except Exception as exc:
                if attempt >= MAX_RETRIES:
                    logger.error(
                        "message_max_retries_exceeded",
                        message_id=msg_id, attempt=attempt, error=str(exc),
                    )
                    # Leave message for DLQ; try to mark the row failed
                    try:
                        data = json.loads(body)
                        _mark_failed(data.get("document_id", ""), str(exc))
                    except Exception:
                        pass
                else:
                    delay = backoff_seconds(attempt)
                    logger.warning(
                        "message_transient_error",
                        message_id=msg_id, attempt=attempt,
                        retry_in=delay, error=str(exc),
                    )
                    client.change_message_visibility(
                        QueueUrl=settings.SQS_QUEUE_URL,
                        ReceiptHandle=receipt,
                        VisibilityTimeout=delay,
                    )

    logger.info("consumer_thread_stopped")


def start_consumer(app) -> None:
    _stop_event.clear()

    def run():
        with app.app_context():
            _poll_loop()

    t = threading.Thread(target=run, daemon=True, name="sqs-consumer")
    t.start()
    app.config["CONSUMER_RUNNING"] = True
    app.config["_consumer_thread"] = t
