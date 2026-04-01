# Showmi ✨  
_A “show me” browser agent_

A self-learning browser agent you control from a Chrome sidebar. See [docs](docs/) for an architecture overview and system diagram (one I presented).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AniruddhS24/self-learning-browseruse/main/install.sh | sh
```

## Setup

### 1. Configure a model (can do this later too)

```bash
showmi models add
```

### 2. Load the Chrome extension

1. Open `chrome://extensions` in Chrome
2. Enable **Developer mode** (toggle in top-right)
3. Click **Load unpacked**
4. Select `~/.showmi/repo/extension/`

Hit Cmd+Shift+. to see hidden folders in MacOS Finder.

### 3. Start the server

```bash
showmi start
```

This starts the daemon worker that serves the showmi API. To call it, pin the Showmi sidebar in Chrome and start chatting!

## Commands

Some useful commands to manage server state. `showmi status` and `showmi restart` are useful if any issues, and `showmi logs` tails the server logs.

```bash
showmi start          # start the server (background)
showmi stop           # stop the server
showmi restart        # restart the server
showmi serve          # start in foreground (dev mode)
showmi status         # see server status and config
showmi models list    # list configured models
showmi sessions       # list recent chat sessions
showmi logs           # tail server logs
showmi upgrade        # pull latest code and restart
showmi uninstall      # delete all data and uninstall
```
