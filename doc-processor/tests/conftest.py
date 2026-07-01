import os
import tempfile
import pytest
import boto3
from moto import mock_aws

from app import create_app
from app.db import init_db


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


@pytest.fixture()
def mock_sqs(aws_credentials):
    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        queue = client.create_queue(QueueName="doc-queue")
        yield {"client": client, "url": queue["QueueUrl"]}


@pytest.fixture()
def mock_s3(aws_credentials):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="doc-bucket")
        # Upload sample files
        client.put_object(
            Bucket="doc-bucket",
            Key="sample.txt",
            Body=b"hello world this is a test document",
        )
        yield {"client": client, "bucket": "doc-bucket"}
