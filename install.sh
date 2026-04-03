#!/usr/bin/env bash
set -e

INSTALL_DIR="$HOME/.local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

chmod +x "$SCRIPT_DIR/claude-server"
mkdir -p "$INSTALL_DIR"
ln -sf "$SCRIPT_DIR/claude-server" "$INSTALL_DIR/claude-server"

echo "Installed to $INSTALL_DIR/claude-server"

if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo ""
    echo "Note: $INSTALL_DIR is not in your PATH. Add it:"
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
    echo ""
fi

echo "Done. Try: claude-server start"
