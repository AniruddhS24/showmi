import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SHOWMI_DIR = Path.home() / ".showmi"
LOGS_DIR = SHOWMI_DIR / "logs"
CHATS_DIR = SHOWMI_DIR / "chats"
WORKFLOWS_DIR = SHOWMI_DIR / "workflows"
DB_PATH = SHOWMI_DIR / "data.db"
IDENTITY_PATH = SHOWMI_DIR / "IDENTITY.md"


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
    """Create the ~/.showmi directory structure and database tables."""
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
        _init_memories_schema(conn)
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


# ── Memory ──


def _sanitize_fts_query(query: str) -> str:
    """Strip characters that cause FTS5 parse errors."""
    import re
    return re.sub(r'["\'\(\)\:\*\^]', ' ', query).strip()


def _init_memories_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the memories table to the typed schema with FTS5 support."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if cols and "type" not in cols:
        # Old schema detected — drop everything and recreate
        conn.execute("DROP TRIGGER IF EXISTS memories_fts_update")
        conn.execute("DROP TRIGGER IF EXISTS memories_fts_delete")
        conn.execute("DROP TRIGGER IF EXISTS memories_fts_insert")
        conn.execute("DROP TABLE IF EXISTS memories_fts")
        conn.execute("DROP TABLE IF EXISTS memories")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            type                TEXT NOT NULL CHECK(type IN ('episodic','procedural','semantic')),
            content             TEXT NOT NULL,
            workflow_slug       TEXT,
            evidence_session_id TEXT REFERENCES sessions(id),
            num_uses            INTEGER NOT NULL DEFAULT 0,
            priority            INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL,
            last_used_at        TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
        USING fts5(content, content=memories, content_rowid=id)
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_fts_insert AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_fts_delete AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_fts_update AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
            INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
        END
    """)


def _sanitize_memory_content(text: str) -> str:
    """Strip JSON fragments and special characters that could break LLM JSON output.

    Memory content is injected into browser agent system prompts. Curly braces,
    square brackets, and backslashes can be mistaken for JSON action schemas and
    cause Pydantic AgentOutput validation errors.
    """
    # Remove JSON-like objects and arrays
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    # Remove stray structural characters and backslashes
    text = re.sub(r'[{}\[\]\\]', "", text)
    # Collapse multiple spaces
    text = re.sub(r"  +", " ", text).strip()
    return text


def add_memory(
    type: str,
    content: str,
    workflow_slug: str | None = None,
    evidence_session_id: str | None = None,
    priority: int = 0,
) -> int:
    """Insert a new memory. FTS5 trigger handles indexing. Returns new ID."""
    content = _sanitize_memory_content(content)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO memories
               (type, content, workflow_slug, evidence_session_id,
                num_uses, priority, created_at, last_used_at)
               VALUES (?, ?, ?, ?, 0, ?, ?, ?)""",
            (type, content, workflow_slug, evidence_session_id, priority, now, now),
        )
        return cursor.lastrowid


def retrieve_memories(
    query: str,
    workflow_slug: str | None = None,
    limit: int = 3,
) -> list[dict]:
    """Retrieve top memories by FTS5 BM25 + priority/recency. Falls back to recency if no FTS match."""
    safe_q = _sanitize_fts_query(query)
    with get_connection() as conn:
        rows = []
        if safe_q:
            slug_filter = "AND m.workflow_slug = ?" if workflow_slug else ""
            params: list = [safe_q]
            if workflow_slug:
                params.append(workflow_slug)
            params.append(limit)
            try:
                # Two-stage: BM25 finds the most relevant matches first,
                # then we rank those by priority/usage/recency.
                rows = conn.execute(f"""
                    SELECT * FROM (
                        SELECT m.id, m.type, m.content, m.workflow_slug,
                               m.num_uses, m.priority, m.last_used_at
                        FROM memories m
                        JOIN memories_fts fts ON fts.rowid = m.id
                        WHERE memories_fts MATCH ?
                          {slug_filter}
                        ORDER BY bm25(memories_fts) ASC
                        LIMIT ?
                    )
                    ORDER BY
                        priority DESC,
                        CASE WHEN julianday('now') - julianday(last_used_at) > 90 THEN 0 ELSE 1 END DESC,
                        num_uses DESC,
                        last_used_at DESC
                """, params).fetchall()
            except Exception:
                rows = []

        if not rows:
            slug_filter = "WHERE workflow_slug = ?" if workflow_slug else ""
            fallback_params: list = ([workflow_slug] if workflow_slug else []) + [limit]
            rows = conn.execute(f"""
                SELECT id, type, content, workflow_slug, num_uses, priority, last_used_at
                FROM memories
                {slug_filter}
                ORDER BY priority DESC, num_uses DESC, last_used_at DESC
                LIMIT ?
            """, fallback_params).fetchall()

    return [dict(row) for row in rows]


def use_memory(memory_id: int, session_id: str | None = None) -> None:
    """Increment num_uses, refresh last_used_at, optionally update evidence_session_id."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        if session_id:
            conn.execute(
                """UPDATE memories
                   SET num_uses = num_uses + 1, last_used_at = ?, evidence_session_id = ?
                   WHERE id = ?""",
                (now, session_id, memory_id),
            )
        else:
            conn.execute(
                "UPDATE memories SET num_uses = num_uses + 1, last_used_at = ? WHERE id = ?",
                (now, memory_id),
            )


def update_memory(
    memory_id: int,
    content: str | None = None,
    priority: int | None = None,
) -> None:
    """Update content and/or priority of a memory."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        if content is not None and priority is not None:
            conn.execute(
                "UPDATE memories SET content=?, priority=?, last_used_at=? WHERE id=?",
                (content, priority, now, memory_id),
            )
        elif content is not None:
            conn.execute(
                "UPDATE memories SET content=?, last_used_at=? WHERE id=?",
                (content, now, memory_id),
            )
        elif priority is not None:
            conn.execute(
                "UPDATE memories SET priority=? WHERE id=?",
                (priority, memory_id),
            )


def list_memories() -> list[dict]:
    """Return all memories ordered by priority DESC, last_used_at DESC. For REST API."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, type, content, workflow_slug, evidence_session_id,
                      num_uses, priority, created_at, last_used_at
               FROM memories
               ORDER BY priority DESC, last_used_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def delete_memory(memory_id: int) -> None:
    """Delete a memory by ID. FTS5 trigger handles deindexing."""
    with get_connection() as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))


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
