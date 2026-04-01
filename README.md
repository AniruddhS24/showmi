# Showmi

A self-learning browser agent you control from a Chrome sidebar.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AniruddhS24/self-learning-browseruse/main/install.sh | sh
```

Then open Chrome, go to `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select `~/.showmi/repo/extension/`.

## Usage

```bash
showmi serve          # start the server
showmi models add     # configure a model (anthropic, openai, or local)
```

Open the Showmi sidebar in Chrome and start chatting.

## Uninstall

```bash
rm -rf ~/.showmi ~/.local/bin/showmi
```
