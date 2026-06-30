from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

VALID_CATEGORIES = [
    "Food", "Transport", "Bills", "Health",
    "Entertainment", "Shopping", "Other",
]


class ExpenseMessage(BaseModel):
    user_id:     int
    amount:      float
    category:    str
    date:        str          # YYYY-MM-DD
    description: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("amount must be greater than 0")
        return v

    @field_validator("category")
    @classmethod
    def category_valid(cls, v):
        if v not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of {VALID_CATEGORIES}")
        return v

    @field_validator("date")
    @classmethod
    def date_format(cls, v):
        datetime.strptime(v, "%Y-%m-%d")
        return v
