import os

SQS_QUEUE_URL         = os.environ.get("SQS_QUEUE_URL")
AWS_REGION            = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY_ID     = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")

DLQ_QUEUE_URL         = os.environ.get("DLQ_QUEUE_URL")
MAX_RETRIES           = int(os.environ.get("MAX_RETRIES", "3"))
ALERT_SNS_TOPIC_ARN   = os.environ.get("ALERT_SNS_TOPIC_ARN")
