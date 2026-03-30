#!/bin/sh
# Showmi installer — run with: curl -fsSL <url>/install.sh | sh
set -e

SHOWMI_HOME="$HOME/.showmi"
REPO_URL="https://github.com/AniruddhS24/self-learning-browseruse.git"
REPO_DIR="$SHOWMI_HOME/repo"
SRC_DIR="$REPO_DIR"
VENV_DIR="$SHOWMI_HOME/.venv"
BIN_DIR="$SHOWMI_HOME/bin"
LINK_DIR="$HOME/.local/bin"

info()  { printf "  \033[36m%s\033[0m %s\n" "$1" "$2"; }
ok()    { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail()  { printf "  \033[31m✗\033[0m %s\n" "$1"; exit 1; }

echo ""
echo "  Installing Showmi..."
echo ""

# ── Check prerequisites ──
command -v git >/dev/null 2>&1 || fail "git is required but not found"

PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3; do
  if command -v "$cmd" >/dev/null 2>&1; then
    if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
      PYTHON="$cmd"
      break
    fi
  fi
done

[ -z "$PYTHON" ] && fail "Python 3.11+ is required. Install from https://python.org"
ok "Python: $($PYTHON --version)"
ok "Git:    $(git --version | head -1)"

# ── Clone or update repo ──
if [ -d "$REPO_DIR/.git" ]; then
  info "Updating" "$REPO_DIR"
  git -C "$REPO_DIR" pull --quiet 2>/dev/null || true
else
  info "Cloning" "$REPO_URL"
  mkdir -p "$SHOWMI_HOME"
  git clone --quiet "$REPO_URL" "$REPO_DIR"
fi
ok "Source: $SRC_DIR"

# ── Create venv and install ──
if command -v uv >/dev/null 2>&1; then
  info "Installing" "with uv (fast)"
  uv venv "$VENV_DIR" --python "$PYTHON" --quiet 2>/dev/null
  uv pip install --python "$VENV_DIR/bin/python" -e "$SRC_DIR" --quiet
else
  info "Installing" "with pip"
  "$PYTHON" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --upgrade pip --quiet 2>/dev/null
  "$VENV_DIR/bin/pip" install -e "$SRC_DIR" --quiet
fi
ok "Installed dependencies"

# ── Create wrapper script ──
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/showmi" << 'WRAPPER'
#!/bin/sh
exec "$HOME/.showmi/.venv/bin/showmi" "$@"
WRAPPER
chmod +x "$BIN_DIR/showmi"

# ── Symlink into PATH ──
mkdir -p "$LINK_DIR"
ln -sf "$BIN_DIR/showmi" "$LINK_DIR/showmi"
ok "CLI: $LINK_DIR/showmi"

# ── Initialize data directory ──
"$VENV_DIR/bin/python" -c "from db import init_db; init_db()" 2>/dev/null || true
ok "Data: ~/.showmi/"

# ── Check PATH ──
echo ""
case ":$PATH:" in
  *":$LINK_DIR:"*) ;;
  *)
    SHELL_NAME="$(basename "$SHELL")"
    case "$SHELL_NAME" in
      zsh)  RC="~/.zshrc" ;;
      bash) RC="~/.bashrc" ;;
      fish) RC="~/.config/fish/config.fish" ;;
      *)    RC="your shell config" ;;
    esac
    echo "  Add to PATH by running:"
    echo ""
    echo "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> $RC"
    echo "    source $RC"
    echo ""
    ;;
esac

echo "  \033[32mShowmi installed!\033[0m"
echo ""
echo "  Next steps:"
echo ""
echo "    showmi models add          # configure an LLM"
echo "    showmi start               # start the background server"
echo ""
echo "  Then load the Chrome extension:"
echo "    chrome://extensions → Developer mode → Load unpacked"
echo "    Select: $SRC_DIR/extension/"
echo ""
