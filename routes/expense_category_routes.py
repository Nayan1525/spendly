from flask import Blueprint, jsonify, request, session
from pydantic import ValidationError

from schemas.expense_category import ExpenseCategoryCreate
from services.expense_category_service import DuplicateCategoryError, create_expense_category

expense_category_bp = Blueprint("expense_category", __name__)


@expense_category_bp.route("/expense-categories", methods=["POST"])
def add_expense_category():
    if "user_id" not in session:
        return jsonify({"error": "Login required"}), 401

    try:
        data = ExpenseCategoryCreate.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return jsonify({"error": exc.errors()}), 400

    try:
        category = create_expense_category(data)
    except DuplicateCategoryError as exc:
        return jsonify({"error": str(exc)}), 409

    return jsonify(category.model_dump(mode="json")), 201
