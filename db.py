import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

STOCKHOLM_DIR = Path.home() / ".stockholm"
SCREENSHOTS_DIR = STOCKHOLM_DIR / "screenshots"
LOGS_DIR = STOCKHOLM_DIR / "logs"
DB_PATH = STOCKHOLM_DIR / "data.db"


@contextmanager
def get_connection():
    """Yield a sqlite3 connection with row_factory set, auto-commit on success."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the ~/.stockholm directory structure and database tables."""
    STOCKHOLM_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP NOT NULL,
                title TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                created_at TIMESTAMP NOT NULL,
                metadata TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
            """
        )


def create_session(title: str | None = None) -> str:
    """Create a new session and return its ID."""
    session_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (id, created_at, title) VALUES (?, ?, ?)",
            (session_id, now, title),
        )
    return session_id


def add_message(
    session_id: str, role: str, content: str, metadata: dict | None = None
) -> int:
    """Add a message to a session and return its ID."""
    now = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata) if metadata is not None else None
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at, metadata) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, now, meta_json),
        )
        return cursor.lastrowid


def get_session_messages(session_id: str) -> list[dict]:
    """Return all messages for a session, ordered by creation time."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, session_id, role, content, created_at, metadata FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
    results = []
    for row in rows:
        msg = dict(row)
        if msg["metadata"]:
            msg["metadata"] = json.loads(msg["metadata"])
        results.append(msg)
    return results


def list_sessions() -> list[dict]:
    """Return all sessions, most recent first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, title FROM sessions ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]
