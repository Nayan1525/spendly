import boto3
import structlog
from botocore.exceptions import ClientError

from app.config import settings

logger = structlog.get_logger(__name__)


class S3DownloadError(Exception):
    pass


def download_document(bucket: str, key: str) -> bytes:
    kwargs = {"region_name": settings.AWS_REGION}
    if settings.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

    client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        **kwargs,
    )
    try:
        logger.info("s3_download_started", bucket=bucket, key=key)
        response = client.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read()
        logger.info("s3_download_completed", bucket=bucket, key=key, size=len(content))
        return content
    except ClientError as exc:
        raise S3DownloadError(f"Failed to download s3://{bucket}/{key}: {exc}") from exc
    except Exception as exc:
        raise S3DownloadError(f"Unexpected error downloading s3://{bucket}/{key}: {exc}") from exc
