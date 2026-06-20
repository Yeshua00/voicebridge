#!/usr/bin/env bash
#
# C0D3.5P34K — iPhone-to-Mac voice relay + remote mouse
#
# One-time setup & launch script. Everything stays inside this folder.
# Run from any Mac with Python 3:  ./start.sh
#

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

echo "═══════════════════════════════════════════════"
echo "  C0D3.5P34K — Setup & Launch"
echo "═══════════════════════════════════════════════"
echo ""

# ── 1. Check Python ────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "❌  Python 3 is required. Install from https://python.org"
    exit 1
fi

PYVER=$($PYTHON --version 2>&1)
echo "✓  Found $PYVER"

# ── 2. Virtual environment ─────────────────────────────────────
VENV_DIR="$APP_DIR/.venv"
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo "📦  Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
    echo "✓  Virtual environment created"
fi

# ── 3. Install dependencies ────────────────────────────────────
echo "📦  Installing Python dependencies..."
"$VENV_DIR/bin/pip" install -q -r "$APP_DIR/requirements.txt" 2>&1 | tail -1
echo "✓  Dependencies installed"

# ── 4. Check openssl (for HTTPS cert generation) ───────────────
if ! command -v openssl &>/dev/null; then
    echo "⚠  openssl not found — will serve HTTP"
    echo "   Install it: brew install openssl"
    echo "   (HTTPS required for microphone on iOS Safari)"
    echo ""
fi

# ── 5. Check ffmpeg (for audio conversion) ─────────────────────
if ! command -v ffmpeg &>/dev/null; then
    echo "⚠  ffmpeg not found — audio transcription won't work"
    echo "   Install it: brew install ffmpeg"
    echo ""
fi

# ── 6. Start server ────────────────────────────────────────────
echo "🚀  Starting server..."
echo ""

exec "$VENV_DIR/bin/python3" "$APP_DIR/server.py"
