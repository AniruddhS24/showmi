# Architecture

Showmi is a local browser automation agent with two main parts: a **Python backend** and a **Chrome extension** frontend.

## Repo Structure

```
showmi/
├── src/showmi/           # Python package (backend)
│   ├── main.py           # CLI entry point (showmi start, stop, etc.)
│   ├── server.py         # FastAPI web server and REST API
│   ├── orchestrator.py   # Routes user messages to the right sub-agent
│   ├── planning.py       # Converts recorded browser sessions into workflows
│   ├── agent.py          # Wrapper around browser-use for browser automation
│   ├── config.py         # Configuration from environment variables
│   ├── db.py             # SQLite database (sessions, models, memories)
│   ├── hooks.py          # Step-level logging for the browser agent
│   └── workflow_utils.py # Read/write workflow files (markdown + YAML)
├── extension/            # Chrome extension (Manifest v3 sidebar)
├── docs/                 # Documentation
├── pyproject.toml        # Package config and dependencies
├── install.sh            # One-line installer
└── Makefile              # Dev shortcuts
```

## How It Works

```
Chrome Sidebar  ──HTTP/SSE──>  FastAPI Server  ──>  Orchestrator  ──>  Browser Agent
                                                        │
                                                        ├──>  Planning Agent
                                                        └──>  Memory / Workflows
```

1. The user types a message in the Chrome sidebar
2. The sidebar sends it to the FastAPI server (`server.py`)
3. The server hands it to the **orchestrator** (`orchestrator.py`)
4. The orchestrator decides what to do:
   - **Run a browser task** — delegates to `agent.py`, which uses the `browser-use` library to control Chrome
   - **Run a saved workflow** — looks up a workflow file and executes its steps
   - **Start recording** — asks the user to demonstrate an action in the browser
   - **Plan a workflow** — sends a recording to `planning.py` to generate a reusable workflow
   - **Query memory** — searches the SQLite database for relevant past context

## Key Files

### `main.py` — CLI

The entry point for all `showmi` commands. Handles starting/stopping the server as a background daemon, managing LLM models, and viewing sessions. The server is launched via `uvicorn showmi.server:app`.

### `server.py` — API Server

A FastAPI app that exposes REST endpoints for the Chrome extension:

- `/health` — liveness check
- `/api/sessions` — create, list, delete chat sessions
- `/api/chat` — send a message (returns an SSE stream)
- `/api/models` — CRUD for LLM configurations
- `/api/workflows` — list, get, save, delete workflows
- `/api/sessions/{id}/planning/*` — planning agent interactions

Also contains `run_agent()`, which sets up the browser-use agent with the active model's settings.

### `orchestrator.py` — Agent Router

The brain of the system. Receives a user message and uses LLM tool-calling to decide the next action. Defines tools like `run_browser_agent`, `run_workflow`, `query_memories`, `store_memory`, `start_recording`, and `start_planning`. Supports both Anthropic and OpenAI as providers.

### `planning.py` — Workflow Generator

Takes a recorded browser session (clicks, typing, scrolling) and uses an LLM to generate a reusable workflow in markdown format. The workflow can include loops, conditionals, and `{{parameters}}`.

### `agent.py` — Browser Automation

Thin wrapper around the `browser-use` library. Creates a `Browser` instance (either via Chrome DevTools Protocol or a local Chrome profile) and runs the agent with configured step hooks.

### `db.py` — Database

SQLite storage at `~/.showmi/data.db`. Tables:

- `sessions` / `messages` — chat history
- `models` — LLM configurations (API keys are base64-encoded)
- `memories` — episodic, procedural, and semantic memories
- `memory_usage` — tracks which memories get used
- `context_summaries` — compressed session context

### `config.py` — Configuration

A frozen dataclass loaded from environment variables (via `.env` or shell). Settings include LLM model, API key, browser options, and agent behavior.

### `hooks.py` — Agent Hooks

Callbacks for `on_step_start` and `on_step_end` during browser automation. Logs each step to the console and appends events to a JSONL file.

### `workflow_utils.py` — Workflow I/O

Reads and writes workflow files from `~/.showmi/workflows/`. Each workflow is a markdown file with YAML frontmatter (name, description, parameters). Also handles screenshot deduplication and event filtering for recordings.

## Data Directory

All user data lives in `~/.showmi/`:

```
~/.showmi/
├── data.db          # SQLite database
├── showmi.pid       # PID of the running server
├── logs/            # Server and event logs
├── workflows/       # Saved workflow markdown files
└── chats/           # Session recordings and GIFs
```
