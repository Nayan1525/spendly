import sqlite3

from database.db import get_db
from schemas.expense_category import ExpenseCategoryCreate, ExpenseCategoryResponse


class DuplicateCategoryError(Exception):
    pass


def create_expense_category(data: ExpenseCategoryCreate) -> ExpenseCategoryResponse:
    db = get_db()
    try:
        try:
            db.execute(
                "INSERT INTO expense_categories (name, description, color) VALUES (?, ?, ?)",
                (data.name, data.description, data.color),
            )
            db.commit()
        except sqlite3.IntegrityError:
            raise DuplicateCategoryError(f"Category '{data.name}' already exists")

        row = db.execute(
            "SELECT * FROM expense_categories WHERE name = ?", (data.name,)
        ).fetchone()
        return ExpenseCategoryResponse.model_validate(dict(row))
    finally:
        db.close()
