#!/usr/bin/env bash
# NOTE: This script is no longer needed for normal usage.
# browser-use launches its own Chrome instance automatically.
#
# This script is only useful if you want to manually start Chrome with CDP
# for advanced use cases (e.g. connecting to a specific Chrome instance).

set -euo pipefail

PORT=9222
CDP_URL="http://localhost:${PORT}"

# Check if CDP is already available
if curl -s "${CDP_URL}/json/version" > /dev/null 2>&1; then
    echo "Chrome CDP already running at ${CDP_URL}"
    curl -s "${CDP_URL}/json/version" | python3 -c "import sys,json; v=json.load(sys.stdin); print(f\"  Browser: {v.get('Browser','unknown')}\")" 2>/dev/null || true
    exit 0
fi

echo ""
echo "Chrome 136+ blocks CDP on the default profile."
echo "browser-use handles this automatically — just run: .venv/bin/python server.py"
echo ""
echo "The agent will launch its own Chrome window with a copy of your profile."
echo ""
