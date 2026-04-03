#!/usr/bin/env bash
set -e

REPO="https://github.com/StaticB1/claude-server"
INSTALL_DIR="$HOME/.local/bin"
SHELL_RC="$HOME/.bashrc"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

echo "Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Please install Python 3."
    exit 1
fi

if ! command -v claude &>/dev/null; then
    echo "Warning: claude CLI not found. Install it first:"
    echo "  https://docs.anthropic.com/en/docs/claude-code"
    echo ""
fi

# Detect if running via curl pipe (no local repo files present)
if [[ ! -f "$(dirname "${BASH_SOURCE[0]}")/claude-server.py" ]]; then
    if ! command -v git &>/dev/null; then
        echo "Error: git not found. Please install git."
        exit 1
    fi
    CLONE_DIR="$HOME/.claude-server"
    echo "Cloning repo to $CLONE_DIR..."
    rm -rf "$CLONE_DIR"
    git clone --depth=1 "$REPO" "$CLONE_DIR"
    SCRIPT_DIR="$CLONE_DIR"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

chmod +x "$SCRIPT_DIR/claude-server"
mkdir -p "$INSTALL_DIR"
ln -sf "$SCRIPT_DIR/claude-server" "$INSTALL_DIR/claude-server"

echo "Installed to $INSTALL_DIR/claude-server"

if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo "$PATH_LINE" >> "$SHELL_RC"
    echo "Added $INSTALL_DIR to PATH in $SHELL_RC"
    export PATH="$INSTALL_DIR:$PATH"
fi

echo ""
echo "Done! Run: claude-server start"
echo "Then open a new terminal or run: source ~/.bashrc"
