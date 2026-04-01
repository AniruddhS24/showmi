# Showmi

A self-learning browser agent you control from a Chrome sidebar.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AniruddhS24/self-learning-browseruse/main/install.sh | sh
```

Then open Chrome, go to `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select `~/.showmi/repo/extension/`.

## Usage

```bash
showmi start          # start the server (background)
showmi status         # see server status
showmi stop           # stop server
showmi upgrade        # upgrade to latest version
showmi uninstall      # delete all data and uninstall showmi
```

Open the Showmi sidebar in Chrome and start chatting!
