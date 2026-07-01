import uuid
import structlog
from flask import Flask, g

from app.config import settings
from app.logging_config import configure_logging
from app.db import init_db

logger = structlog.get_logger(__name__)


def create_app(test_config: dict | None = None) -> Flask:
    configure_logging(env=settings.ENV, log_level=settings.LOG_LEVEL)

    app = Flask(__name__)
    app.config["DB_PATH"] = settings.DB_PATH

    if test_config:
        app.config.update(test_config)
        if "DB_PATH" in test_config:
            from app import config as cfg_module
            cfg_module.settings.DB_PATH = test_config["DB_PATH"]

    init_db()

    @app.before_request
    def set_request_id():
        g.request_id = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=g.request_id)

    @app.teardown_request
    def clear_contextvars(exc):
        structlog.contextvars.clear_contextvars()

    from app.routes.health import health_bp
    from app.routes.documents import documents_bp
    app.register_blueprint(health_bp)
    app.register_blueprint(documents_bp)

    if not app.config.get("TESTING"):
        from app.services.consumer import start_consumer
        start_consumer(app)
        logger.info("doc_processor_started", env=settings.ENV)

    return app
