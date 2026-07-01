import structlog
from flask import Blueprint, jsonify, request

from app.db import get_db
from app.models.document import document_to_dict

documents_bp = Blueprint("documents", __name__)
logger = structlog.get_logger(__name__)

VALID_STATUSES = {"pending", "processing", "completed", "failed"}


@documents_bp.route("/documents")
def list_documents():
    status       = request.args.get("status", "").strip() or None
    requested_by = request.args.get("requested_by", "").strip() or None
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
        skip  = max(int(request.args.get("skip", 0)), 0)
    except ValueError:
        return jsonify({"error": "limit and skip must be integers"}), 400

    if status and status not in VALID_STATUSES:
        return jsonify({"error": f"status must be one of {sorted(VALID_STATUSES)}"}), 400

    conditions, params = [], []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if requested_by:
        conditions.append("requested_by = ?")
        params.append(requested_by)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM documents {where}", params).fetchone()[0]
    rows  = db.execute(
        f"SELECT * FROM documents {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, skip],
    ).fetchall()
    db.close()

    return jsonify({
        "items": [document_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "skip":  skip,
    }), 200


@documents_bp.route("/documents/<document_id>")
def get_document(document_id: str):
    db = get_db()
    row = db.execute(
        "SELECT * FROM documents WHERE document_id = ?", (document_id,)
    ).fetchone()
    db.close()

    if row is None:
        return jsonify({"error": "document not found"}), 404

    return jsonify(document_to_dict(row)), 200
