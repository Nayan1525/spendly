import pytest
from app.db import get_db


def _insert_doc(app, document_id="doc-001", status="completed", requested_by="user-1"):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO documents
               (document_id, bucket, key, status, requested_by, requested_at,
                word_count, file_size_bytes, text_preview)
               VALUES (?, 'b', 'k', ?, ?, '2026-06-30T10:00:00Z', 10, 100, 'preview')""",
            (document_id, status, requested_by),
        )
        db.commit()
        db.close()


class TestHealthRoutes:
    def test_health_always_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json["status"] == "ok"

    def test_ready_503_when_consumer_not_running(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 503
        assert resp.json["consumer"] is False
        assert resp.json["db"] is True

    def test_ready_200_when_consumer_running(self, client, app):
        app.config["CONSUMER_RUNNING"] = True
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json["consumer"] is True


class TestListDocuments:
    def test_empty_list(self, client):
        resp = client.get("/documents")
        assert resp.status_code == 200
        assert resp.json["items"] == []
        assert resp.json["total"] == 0

    def test_returns_inserted_row(self, client, app):
        _insert_doc(app, "doc-001")
        resp = client.get("/documents")
        assert resp.status_code == 200
        assert resp.json["total"] == 1
        assert resp.json["items"][0]["document_id"] == "doc-001"

    def test_filter_by_status(self, client, app):
        _insert_doc(app, "doc-001", status="completed")
        _insert_doc(app, "doc-002", status="failed")
        resp = client.get("/documents?status=failed")
        assert resp.json["total"] == 1
        assert resp.json["items"][0]["document_id"] == "doc-002"

    def test_filter_by_requested_by(self, client, app):
        _insert_doc(app, "doc-001", requested_by="alice")
        _insert_doc(app, "doc-002", requested_by="bob")
        resp = client.get("/documents?requested_by=alice")
        assert resp.json["total"] == 1
        assert resp.json["items"][0]["document_id"] == "doc-001"

    def test_pagination(self, client, app):
        for i in range(5):
            _insert_doc(app, f"doc-{i:03d}")
        resp = client.get("/documents?limit=2&skip=1")
        assert len(resp.json["items"]) == 2
        assert resp.json["limit"] == 2
        assert resp.json["skip"] == 1

    def test_invalid_status_returns_400(self, client):
        resp = client.get("/documents?status=unknown")
        assert resp.status_code == 400

    def test_limit_capped_at_100(self, client, app):
        for i in range(5):
            _insert_doc(app, f"doc-{i:03d}")
        resp = client.get("/documents?limit=200")
        assert resp.json["limit"] == 100


class TestGetDocument:
    def test_existing_document_200(self, client, app):
        _insert_doc(app, "doc-xyz")
        resp = client.get("/documents/doc-xyz")
        assert resp.status_code == 200
        assert resp.json["document_id"] == "doc-xyz"

    def test_missing_document_404(self, client):
        resp = client.get("/documents/does-not-exist")
        assert resp.status_code == 404
        assert "error" in resp.json
