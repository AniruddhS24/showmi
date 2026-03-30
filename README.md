# Showmi

Self-learning browser agent with a Chrome extension sidebar UI.

## Prerequisites

- Python 3.11+
- Google Chrome
- (Optional) [uv](https://github.com/astral-sh/uv) for faster installs

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AniruddhS24/self-learning-browseruse/main/install.sh | sh
```

This clones the repo into `~/.showmi/`, installs dependencies, and sets up the `showmi` CLI.

To uninstall, just `rm -rf ~/.showmi ~/.local/bin/showmi`.

## Quick start (dev)

```bash
git clone https://github.com/AniruddhS24/self-learning-browseruse.git && cd self-learning-browseruse

# Install (uses uv if available, falls back to pip)
make install

# Configure a model
.venv/bin/showmi models add

# Start the server
make serve
```

Then load the Chrome extension from `extension/` (chrome://extensions → Load unpacked).

## CLI

```
showmi serve                          Start the server (default :8765)
showmi serve -p 3000 --reload         Custom port, auto-reload

showmi run "book a flight to NYC"     Run a one-off browser task
showmi run "search for X" --confirm   Ask before each step

showmi models                         List configured models
showmi models add                     Add a model (interactive)
showmi models add --provider anthropic --model claude-sonnet-4-20250514 --api-key sk-...
showmi models activate <name>         Set active model
showmi models rm <name>               Delete a model

showmi sessions                       List recent chat sessions
showmi sessions <id>                  View messages in a session

showmi status                         Check server + config
```

## Configuration

Models are stored in `~/.showmi/data.db`. You can also set defaults via `.env`:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `CDP_URL` | (empty) | Chrome DevTools Protocol URL. Leave blank to auto-launch Chrome |
| `LLM_BASE_URL` | `http://localhost:8000/v1` | Fallback LLM endpoint |
| `MAX_STEPS` | `100` | Max agent steps per task |
| `USE_VISION` | `true` | Enable screenshot-based vision |

## Chrome extension

The sidebar extension lives in `extension/`. To install:

1. Open `chrome://extensions`
2. Enable "Developer mode"
3. Click "Load unpacked" → select the `extension/` directory

The extension connects to the server at `ws://localhost:8765/ws`.

## Data directory

```
~/.showmi/
├── data.db              # SQLite: sessions, messages, models, memories
├── workflows/           # .md files injected into the agent's system prompt
├── chats/               # Per-session context summaries
├── screenshots/
└── logs/
    └── events.jsonl     # Step-by-step event log
```
