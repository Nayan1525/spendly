from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SQS_QUEUE_URL: str = ""
    SQS_DLQ_URL: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = "test"
    AWS_SECRET_ACCESS_KEY: str = "test"
    S3_ENDPOINT_URL: str = ""          # e.g. http://localstack:4566 in docker
    LOG_LEVEL: str = "INFO"
    ENV: str = "development"           # development | production
    DB_PATH: str = "documents.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
