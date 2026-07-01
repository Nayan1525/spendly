from pydantic import BaseModel, field_validator
import sqlite3


class DocumentMessage(BaseModel):
    document_id:  str
    bucket:       str
    key:          str
    requested_by: str
    requested_at: str  # ISO-8601

    @field_validator("document_id", "bucket", "key", "requested_by", "requested_at")
    @classmethod
    def must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must not be empty")
        return v


def document_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)
