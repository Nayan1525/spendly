import structlog
from flask import Blueprint, jsonify, current_app

from app.db import get_db

health_bp = Blueprint("health", __name__)
logger = structlog.get_logger(__name__)


@health_bp.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@health_bp.route("/ready")
def ready():
    db_ok = False
    try:
        db = get_db()
        db.execute("SELECT 1")
        db.close()
        db_ok = True
    except Exception:
        pass

    consumer_running = current_app.config.get("CONSUMER_RUNNING", False)

    if db_ok and consumer_running:
        return jsonify({"status": "ok", "db": True, "consumer": True}), 200

    return jsonify({
        "status": "unavailable",
        "db": db_ok,
        "consumer": consumer_running,
    }), 503
