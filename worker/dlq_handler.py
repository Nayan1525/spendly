import sys
import boto3
from worker.config import DLQ_QUEUE_URL, AWS_REGION, ALERT_SNS_TOPIC_ARN


def send_alert(message: dict) -> None:
    """Log a permanently-failed message and optionally publish to SNS."""
    try:
        body   = message.get("Body", "")
        msg_id = message.get("MessageId", "unknown")
        print(
            f"[ALERT] Message failed permanently. ID={msg_id} Body={body}",
            file=sys.stderr,
        )
        if ALERT_SNS_TOPIC_ARN:
            sns = boto3.client("sns", region_name=AWS_REGION)
            sns.publish(
                TopicArn=ALERT_SNS_TOPIC_ARN,
                Subject="Spendly SQS processing failure",
                Message=body,
            )
    except Exception as exc:
        print(f"[ALERT] send_alert itself failed: {exc}", file=sys.stderr)


def run_dlq_handler() -> None:
    if not DLQ_QUEUE_URL:
        print("ERROR: DLQ_QUEUE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = boto3.client("sqs", region_name=AWS_REGION)
    print(f"DLQ handler started. Polling {DLQ_QUEUE_URL}", flush=True)

    while True:
        response = client.receive_message(
            QueueUrl=DLQ_QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
            AttributeNames=["All"],
        )
        for message in response.get("Messages", []):
            msg_id = message.get("MessageId", "unknown")
            print(f"[DLQ] Processing failed message {msg_id}: {message.get('Body', '')}")
            send_alert(message)
            client.delete_message(
                QueueUrl=DLQ_QUEUE_URL,
                ReceiptHandle=message["ReceiptHandle"],
            )
            print(f"[DLQ] Deleted {msg_id} from DLQ", flush=True)


if __name__ == "__main__":
    try:
        run_dlq_handler()
    except KeyboardInterrupt:
        print("DLQ handler stopped.")
