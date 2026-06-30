import boto3
from worker.config import SQS_QUEUE_URL, AWS_REGION
from worker.schemas import ExpenseMessage


def publish_expense(user_id, amount, category, date, description):
    if not SQS_QUEUE_URL:
        raise RuntimeError("SQS_QUEUE_URL environment variable is not set")

    msg = ExpenseMessage(
        user_id=user_id,
        amount=amount,
        category=category,
        date=date,
        description=description,
    )
    client = boto3.client("sqs", region_name=AWS_REGION)
    response = client.send_message(
        QueueUrl=SQS_QUEUE_URL,
        MessageBody=msg.model_dump_json(),
    )
    return response["MessageId"]
