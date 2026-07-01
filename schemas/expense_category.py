from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ExpenseCategoryBase(BaseModel):
    # Placeholder fields inferred from the resource name — adjust to match
    # the real expense_categories table once it exists.
    name: str
    description: str | None = None
    color: str | None = None  # hex UI tag color, e.g. "#FF5733"


class ExpenseCategoryCreate(ExpenseCategoryBase):
    pass


class ExpenseCategoryResponse(ExpenseCategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
