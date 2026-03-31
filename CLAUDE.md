# Showmi

Local browser automation agent with a Chrome extension sidebar UI. Python backend (FastAPI + browser-use) paired with a Chrome extension for chat, recording, and model management.

## CLI (`showmi`)

Installed via `pyproject.toml` entry point (`showmi = "main:cli"`). Uses argparse subcommands.

```
showmi start [--port 8765]   # Start server as background daemon (PID in ~/.showmi/showmi.pid)
showmi stop                  # Graceful stop (SIGTERM → 5s → SIGKILL)
showmi restart               # stop + start
showmi serve [--port] [--reload]  # Foreground dev server
showmi logs [-n 50]          # Tail server logs

showmi run "<task>" [--confirm]   # One-off browser task via CLI
showmi models list|add|rm|activate  # Manage LLM providers
showmi sessions [-n 20]      # List/view chat sessions
showmi status                # Show server status, models, paths
showmi upgrade               # Git pull + reinstall deps + restart
showmi uninstall             # Remove ~/.showmi and CLI symlink
```

## ~/.showmi directory

```
~/.showmi/
├── data.db              # SQLite: sessions, messages, models, memories tables
├── showmi.pid           # PID of running server
├── IDENTITY.md          # Editable agent persona (injected into system prompt)
├── MEMORY.md            # Auto-generated from memories table
├── logs/
│   ├── server.log       # Uvicorn output
│   └── events.jsonl     # Per-step JSONL event log
├── chats/{session_id}/
│   └── context.md       # Compressed context summary per session
└── workflows/{slug}.md  # Recorded workflows with YAML frontmatter
```

## Server (server.py)

FastAPI on port 8765. CORS enabled for all origins.

**REST routes:**
- `GET /health` — health check
- `GET/POST/DELETE /api/sessions` — session CRUD
- `GET/POST/PUT/DELETE /api/models` — model CRUD, `PUT .../activate`
- `GET/POST/PUT/DELETE /api/workflows` — workflow CRUD
- `POST /api/workflows/compile` — LLM-compile a recording into a workflow
- `GET /identity`, `GET /memory` — read persona/memory files

**WebSocket:** `ws://localhost:8765/ws` — bidirectional channel for task execution. Client sends task messages, server streams step/result/error updates.

## Database (db.py)

SQLite at `~/.showmi/data.db`. Four tables:

- **sessions** — id (UUID), title, status (idle/running/completed/error), created_at
- **messages** — session_id FK, role (user/assistant), content, metadata (JSON)
- **models** — provider (anthropic/openai/local), model name, api_key (base64-encoded), base_url, temperature, is_active flag
- **memories** — category, content, source_session_id FK

API keys are base64-encoded (not encrypted).

## Key source files

- `main.py` — CLI entry point
- `server.py` — FastAPI routes + WebSocket handler + agent orchestration
- `db.py` — SQLite schema, all CRUD functions, directory constants
- `agent.py` — Standalone agent runner (used by `showmi run`)
- `config.py` — Frozen dataclass from env vars (.env support)
- `hooks.py` — Workflow loader, step hooks (console + JSONL logging)
- `workflow_utils.py` — Frontmatter parsing, workflow file I/O, LLM compilation

## Chrome extension (extension/)

Manifest V3 sidebar panel. Files: `manifest.json`, `sidepanel.{html,js,css}`, `background.js`, `recorder.js`.

- **sidepanel** — Main UI: chat, model selector, recording controls, workflow review
- **background.js** — Service worker: recording state, injects recorder into tabs
- **recorder.js** — Content script: captures clicks, inputs, navigation as DOM events

## How agent execution works

1. Extension sends task via WebSocket
2. Server creates/reuses session, saves user message
3. Builds system prompt from IDENTITY.md + MEMORY.md + loaded workflows
4. Instantiates LLM (ChatAnthropic or ChatOpenAI based on active model)
5. Runs browser-use Agent with step hooks that stream updates back via WebSocket
6. On completion, compresses context and saves result to DB

## How workflow recording works

1. User clicks Record in extension → background.js injects recorder.js into all tabs
2. recorder.js captures DOM events (click, input, navigation, select, keypress, scroll)
3. User clicks Stop → events sent to `POST /api/workflows/compile`
4. Server calls LLM with recording + COMPILE_SYSTEM_PROMPT to produce semantic workflow
5. User reviews and saves → stored as `~/.showmi/workflows/{slug}.md`
6. Saved workflows are injected into the agent's system prompt for future tasks

## Dependencies

browser-use, langchain-openai, langchain-anthropic, fastapi, uvicorn, websockets, pyyaml, python-dotenv. Python >=3.11. Managed with uv.
