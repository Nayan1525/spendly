import json
import sys
import boto3
from pydantic import ValidationError

from database.db import get_db
from database.queries import insert_expense
from worker.config import SQS_QUEUE_URL, AWS_REGION, MAX_RETRIES
from worker.dlq_handler import send_alert
from worker.schemas import ExpenseMessage


def backoff_seconds(attempt: int) -> int:
    """Exponential backoff capped at 300 s: 10, 20, 40 … 300."""
    return min(2 ** attempt * 5, 300)


def process_message(body: str) -> None:
    """Validate and insert one message. Raises:
    - json.JSONDecodeError  — body is not valid JSON
    - ValueError            — body is valid JSON but fails schema validation
    - Exception             — DB or other transient error
    """
    data = json.loads(body)      # json.JSONDecodeError propagates directly
    try:
        expense = ExpenseMessage(**data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

    db = get_db()
    try:
        insert_expense(
            db,
            expense.user_id,
            expense.amount,
            expense.category,
            expense.date,
            expense.description,
        )
    finally:
        db.close()


def main() -> None:
    if not SQS_QUEUE_URL:
        print("ERROR: SQS_QUEUE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = boto3.client("sqs", region_name=AWS_REGION)
    print(f"Worker started. Polling {SQS_QUEUE_URL}", flush=True)

    while True:
        response = client.receive_message(
            QueueUrl=SQS_QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
            AttributeNames=["ApproximateReceiveCount"],
        )

        for message in response.get("Messages", []):
            msg_id  = message["MessageId"]
            receipt = message["ReceiptHandle"]
            attempt = int(
                message.get("Attributes", {}).get("ApproximateReceiveCount", "1")
            )

            try:
                process_message(message["Body"])
                client.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt)
                print(f"Processed {msg_id}", flush=True)

            except ValueError as exc:
                # Schema / JSON error — retrying won't fix it; log and skip.
                # Do not delete: let SQS exhaust maxReceiveCount → DLQ naturally.
                print(f"Schema error {msg_id}: {exc}", file=sys.stderr)

            except Exception as exc:
                # Transient error (DB, network) — retry with exponential backoff.
                print(
                    f"Error processing {msg_id} (attempt {attempt}): {exc}",
                    file=sys.stderr,
                )
                if attempt < MAX_RETRIES:
                    delay = backoff_seconds(attempt)
                    client.change_message_visibility(
                        QueueUrl=SQS_QUEUE_URL,
                        ReceiptHandle=receipt,
                        VisibilityTimeout=delay,
                    )
                    print(f"Visibility extended by {delay}s for {msg_id}", flush=True)
                else:
                    print(
                        f"Max retries reached for {msg_id}, escalating to DLQ",
                        file=sys.stderr,
                    )
                    send_alert(message)
                    # Do not delete — SQS moves it to DLQ after maxReceiveCount.


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Worker stopped.")
