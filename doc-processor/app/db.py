import sqlite3
from app.config import settings


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id     TEXT    UNIQUE NOT NULL,
            bucket          TEXT    NOT NULL,
            key             TEXT    NOT NULL,
            status          TEXT    NOT NULL DEFAULT 'pending',
            requested_by    TEXT,
            requested_at    TEXT,
            processed_at    TEXT,
            mime_type       TEXT,
            file_size_bytes INTEGER,
            page_count      INTEGER,
            word_count      INTEGER,
            text_preview    TEXT,
            error_message   TEXT,
            created_at      TEXT    DEFAULT (datetime('now')),
            updated_at      TEXT    DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_documents_status
            ON documents(status);

        CREATE INDEX IF NOT EXISTS idx_documents_requested_by
            ON documents(requested_by);
    """)
    db.commit()
    db.close()
