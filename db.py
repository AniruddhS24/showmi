import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SHOWMI_DIR = Path.home() / ".self-learning-browseragent"
SCREENSHOTS_DIR = SHOWMI_DIR / "screenshots"
LOGS_DIR = SHOWMI_DIR / "logs"
CHATS_DIR = SHOWMI_DIR / "chats"
WORKFLOWS_DIR = SHOWMI_DIR / "workflows"
DB_PATH = SHOWMI_DIR / "data.db"


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
    """Create the ~/.self-learning-browseragent directory structure and database tables."""
    SHOWMI_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                source_session_id TEXT,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                FOREIGN KEY (source_session_id) REFERENCES sessions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'anthropic',
                api_key_encrypted TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                temperature REAL NOT NULL DEFAULT 0.5,
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )


# ── Sessions ──

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


def list_sessions(limit: int = 50) -> list[dict]:
    """Return recent sessions, most recent first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, title FROM sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


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


# ── Models ──

def _encode_key(api_key: str) -> str:
    """Obfuscate API key for local storage. Not true encryption — placeholder
    until a proper secret store is wired in."""
    import base64
    if not api_key:
        return ""
    return base64.b64encode(api_key.encode()).decode()


def _decode_key(encoded: str) -> str:
    """Reverse of _encode_key."""
    import base64
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded.encode()).decode()
    except Exception:
        return encoded  # fallback: return as-is if not encoded


def list_models() -> list[dict]:
    """Return all saved models."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, provider, api_key_encrypted, base_url, model, temperature, is_active, created_at, updated_at FROM models ORDER BY created_at ASC"
        ).fetchall()
    results = []
    for row in rows:
        m = dict(row)
        m["api_key"] = _decode_key(m.pop("api_key_encrypted"))
        results.append(m)
    return results


def get_active_model() -> dict | None:
    """Return the currently active model, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, provider, api_key_encrypted, base_url, model, temperature, is_active, created_at, updated_at FROM models WHERE is_active = 1 LIMIT 1"
        ).fetchone()
    if not row:
        return None
    m = dict(row)
    m["api_key"] = _decode_key(m.pop("api_key_encrypted"))
    return m


def save_model(data: dict) -> dict:
    """Create or update a model config. Returns the saved model."""
    now = datetime.now(timezone.utc).isoformat()
    model_id = data.get("id") or str(uuid4())
    encoded_key = _encode_key(data.get("api_key", ""))

    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM models WHERE id = ?", (model_id,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE models SET name=?, provider=?, api_key_encrypted=?, base_url=?, model=?, temperature=?, updated_at=?
                   WHERE id=?""",
                (
                    data.get("name", ""),
                    data.get("provider", "anthropic"),
                    encoded_key,
                    data.get("base_url", ""),
                    data.get("model", ""),
                    float(data.get("temperature", 0.5)),
                    now,
                    model_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO models (id, name, provider, api_key_encrypted, base_url, model, temperature, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    model_id,
                    data.get("name", ""),
                    data.get("provider", "anthropic"),
                    encoded_key,
                    data.get("base_url", ""),
                    data.get("model", ""),
                    float(data.get("temperature", 0.5)),
                    now,
                    now,
                ),
            )

    return {**data, "id": model_id}


def set_active_model(model_id: str) -> None:
    """Set a model as the active one (deactivating all others)."""
    with get_connection() as conn:
        conn.execute("UPDATE models SET is_active = 0")
        conn.execute("UPDATE models SET is_active = 1 WHERE id = ?", (model_id,))


def delete_model(model_id: str) -> None:
    """Delete a model by ID."""
    with get_connection() as conn:
        conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
