import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SHOWMI_DIR = Path.home() / ".self-learning-browseragent"
LOGS_DIR = SHOWMI_DIR / "logs"
CHATS_DIR = SHOWMI_DIR / "chats"
WORKFLOWS_DIR = SHOWMI_DIR / "workflows"
DB_PATH = SHOWMI_DIR / "data.db"
IDENTITY_PATH = SHOWMI_DIR / "IDENTITY.md"
MEMORY_PATH = SHOWMI_DIR / "MEMORY.md"


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
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP NOT NULL,
                title TEXT,
                status TEXT NOT NULL DEFAULT 'idle'
            )
            """
        )
        # Migrate: add status column if missing
        try:
            conn.execute("SELECT status FROM sessions LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'idle'")
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    # Create default IDENTITY.md if it doesn't exist
    if not IDENTITY_PATH.exists():
        IDENTITY_PATH.write_text(
            "# Agent Identity\n\n"
            "You are a helpful browser automation agent. "
            "You help users accomplish tasks in their web browser efficiently and accurately.\n"
        )

    # Create empty MEMORY.md if it doesn't exist
    if not MEMORY_PATH.exists():
        MEMORY_PATH.write_text("# Agent Memory\n\nNo memories stored yet.\n")


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


def delete_session(session_id: str) -> None:
    """Delete a session and all its messages."""
    with get_connection() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def update_session_status(session_id: str, status: str) -> None:
    """Update the status of a session (idle, running, completed, error)."""
    with get_connection() as conn:
        conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))


def update_session_title(session_id: str, title: str) -> None:
    """Update the title of a session."""
    with get_connection() as conn:
        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))


def list_sessions(limit: int = 50) -> list[dict]:
    """Return recent sessions, most recent first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, title, status FROM sessions ORDER BY created_at DESC LIMIT ?",
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


# ── Memory CRUD ──


def add_memory(
    category: str, content: str, source_session_id: str | None = None
) -> int:
    """Add a memory entry and rebuild the MEMORY.md file. Returns the new memory ID."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO memories (category, content, source_session_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (category, content, source_session_id, now, now),
        )
        memory_id = cursor.lastrowid
    rebuild_memory_file()
    return memory_id


def list_memories(category: str | None = None) -> list[dict]:
    """Return all memories, optionally filtered by category, most recent first."""
    with get_connection() as conn:
        if category:
            rows = conn.execute(
                "SELECT id, category, content, source_session_id, created_at, updated_at FROM memories WHERE category = ? ORDER BY created_at DESC",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, category, content, source_session_id, created_at, updated_at FROM memories ORDER BY created_at DESC"
            ).fetchall()
    return [dict(row) for row in rows]


def delete_memory(memory_id: int) -> None:
    """Delete a memory by ID and rebuild the MEMORY.md file."""
    with get_connection() as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    rebuild_memory_file()


CATEGORY_LABELS = {
    "preference": "User Preferences",
    "website_knowledge": "Website Knowledge",
    "workflow_learning": "Workflow Learnings",
    "general": "General",
}


def rebuild_memory_file() -> None:
    """Read all memories from the database and write MEMORY.md grouped by category."""
    memories = list_memories()
    now = datetime.now(timezone.utc).isoformat()

    grouped: dict[str, list[str]] = {}
    for mem in memories:
        cat = mem["category"]
        grouped.setdefault(cat, []).append(mem["content"])

    lines = [f"# Agent Memory\n", f"Last updated: {now}\n"]

    for cat_key, label in CATEGORY_LABELS.items():
        entries = grouped.pop(cat_key, [])
        if entries:
            lines.append(f"\n## {label}\n")
            for entry in entries:
                lines.append(f"- {entry}")

    # Any categories not in CATEGORY_LABELS
    for cat_key, entries in grouped.items():
        if entries:
            lines.append(f"\n## {cat_key.replace('_', ' ').title()}\n")
            for entry in entries:
                lines.append(f"- {entry}")

    MEMORY_PATH.write_text("\n".join(lines) + "\n")


def get_memory_text() -> str:
    """Return the contents of MEMORY.md, or empty string if it doesn't exist."""
    if MEMORY_PATH.exists():
        return MEMORY_PATH.read_text()
    return ""


def get_identity_text() -> str:
    """Return the contents of IDENTITY.md, or empty string if it doesn't exist."""
    if IDENTITY_PATH.exists():
        return IDENTITY_PATH.read_text()
    return ""


# ── Per-chat context ──


def save_context_summary(session_id: str, summary: str) -> None:
    """Write a context.md summary for a chat session."""
    chat_dir = CHATS_DIR / session_id
    chat_dir.mkdir(parents=True, exist_ok=True)
    (chat_dir / "context.md").write_text(summary)


def get_context_summary(session_id: str) -> str | None:
    """Read the context.md for a session, or return None if it doesn't exist."""
    context_path = CHATS_DIR / session_id / "context.md"
    if context_path.exists():
        return context_path.read_text()
    return None
