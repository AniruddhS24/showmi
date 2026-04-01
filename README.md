# Showmi

A self-learning browser agent you control from a Chrome sidebar.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AniruddhS24/self-learning-browseruse/main/install.sh | sh
```

## Setup

### 1. Configure a model

```bash
showmi models add
```

### 2. Load the Chrome extension

1. Open `chrome://extensions` in Chrome
2. Enable **Developer mode** (toggle in top-right)
3. Click **Load unpacked**
4. Select `~/.showmi/repo/extension/`

### 3. Start the server

```bash
showmi start
```

Open the Showmi sidebar in Chrome and start chatting!

## Commands

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
