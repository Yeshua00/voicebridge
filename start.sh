#!/usr/bin/env bash
#
# C0D3.5P34K — iPhone-to-Mac voice relay + remote mouse
#
# One-time setup & launch script. Everything stays inside this folder.
# Run from any Mac with Python 3:  ./start.sh
#
# Installs whisper.cpp + model, starts whisper-server, then launches
# the Flask server. All data lives in .venv/, .models/, .certs/.
#

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

echo "═══════════════════════════════════════════════"
echo "  C0D3.5P34K — Setup & Launch"
echo "═══════════════════════════════════════════════"
echo ""

# ── Helper: section header ──
section() { echo "─── $1 ───"; }

# ── 1. Check Python ────────────────────────────────────────────
section "Python"
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "❌  Python 3 required. Install from https://python.org"
    exit 1
fi
PYVER=$($PYTHON --version 2>&1)
echo "  ✓  $PYVER"

# ── 2. Virtual environment + Python deps ───────────────────────
section "Python dependencies"
VENV_DIR="$APP_DIR/.venv"
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo "  Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
fi
echo "  Installing flask, requests, pyobjc..."
"$VENV_DIR/bin/pip" install -q -r "$APP_DIR/requirements.txt" 2>&1 | tail -1
echo "  ✓  Python deps ready"

# ── 3. Install whisper.cpp ─────────────────────────────────────
section "Whisper STT engine"
WHISPER_BIN=""
# Check if whisper-server is already on PATH
if command -v whisper-server &>/dev/null; then
    WHISPER_BIN=$(command -v whisper-server)
    echo "  ✓  Found whisper-server at $WHISPER_BIN"
elif command -v brew &>/dev/null; then
    # Install via Homebrew
    if brew list whisper-cpp &>/dev/null 2>&1; then
        echo "  ✓  whisper-cpp already installed via Homebrew"
    else
        echo "  Installing whisper-cpp via Homebrew (this may take a minute)..."
        brew install whisper-cpp 2>&1 | tail -3
    fi
    WHISPER_BIN=$(brew --prefix whisper-cpp 2>/dev/null)/bin/whisper-server
    if [ ! -f "$WHISPER_BIN" ]; then
        WHISPER_BIN=$(command -v whisper-server 2>/dev/null || true)
    fi
    echo "  ✓  whisper-server ready"
fi

# If still not found, compile from source
if [ -z "$WHISPER_BIN" ] || [ ! -f "$WHISPER_BIN" ]; then
    echo "  Homebrew not available — compiling whisper.cpp from source..."
    if ! command -v cmake &>/dev/null; then
        echo "  ⚠  cmake not found, installing via Homebrew..."
        if command -v brew &>/dev/null; then
            brew install cmake 2>&1 | tail -1
        else
            echo "  ❌  Need cmake to compile whisper.cpp."
            echo "     Install Xcode Command Line Tools: xcode-select --install"
            exit 1
        fi
    fi

    WHISPER_SRC="$APP_DIR/.whisper-src"
    WHISPER_BUILD="$WHISPER_SRC/build"
    WHISPER_BIN="$WHISPER_BUILD/bin/whisper-server"

    if [ ! -f "$WHISPER_BIN" ]; then
        if [ ! -d "$WHISPER_SRC" ]; then
            echo "  Cloning whisper.cpp..."
            git clone --depth 1 https://github.com/ggerganov/whisper.cpp "$WHISPER_SRC" 2>&1 | tail -3
        fi
        echo "  Compiling (this takes a minute)..."
        mkdir -p "$WHISPER_BUILD"
        cmake -S "$WHISPER_SRC" -B "$WHISPER_BUILD" -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -3
        cmake --build "$WHISPER_BUILD" --target whisper-server -- -j$(sysctl -n hw.logicalcpu 2>/dev/null || echo 4) 2>&1 | tail -5
    fi
    echo "  ✓  whisper-server compiled"
fi

# ── 4. Download model ──────────────────────────────────────────
section "Speech model"
MODEL_DIR="$APP_DIR/.models"
MODEL_NAME="ggml-base.en.bin"
MODEL_FILE="$MODEL_DIR/$MODEL_NAME"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODEL_NAME"

if [ -f "$MODEL_FILE" ]; then
    echo "  ✓  Model found ($(du -h "$MODEL_FILE" | cut -f1))"
else
    echo "  Downloading $MODEL_NAME (~140MB) from Hugging Face..."
    mkdir -p "$MODEL_DIR"
    # Use curl with resume support
    if command -v curl &>/dev/null; then
        curl -L -o "$MODEL_FILE" --continue-at - "$MODEL_URL" 2>&1 | tail -3
    else
        wget -O "$MODEL_FILE" -c "$MODEL_URL" 2>&1 | tail -3
    fi
    echo "  ✓  Model downloaded"
fi

# ── 5. Check openssl (for HTTPS cert generation) ───────────────
section "Network"
if ! command -v openssl &>/dev/null; then
    echo "  ⚠  openssl not found — serving HTTP only"
    echo "     (HTTPS required for microphone on iOS Safari)"
else
    echo "  ✓  openssl available (HTTPS supported)"
fi

# ── 6. Check ffmpeg (for audio conversion) ─────────────────────
if ! command -v ffmpeg &>/dev/null; then
    echo "  ⚠  ffmpeg not found — audio transcription won't work"
    echo "     Install: brew install ffmpeg"
else
    echo "  ✓  ffmpeg available"
fi

# ── 7. Start whisper-server (background) ───────────────────────
section "Starting whisper-server"
WHISPER_PORT="${WHISPER_PORT:-9999}"
WHISPER_PID=""
# Check if already running
if lsof -ti :$WHISPER_PORT &>/dev/null 2>&1; then
    EXISTING=$(lsof -ti :$WHISPER_PORT 2>/dev/null)
    echo "  whisper-server already running on port $WHISPER_PORT (PID $EXISTING)"
else
    echo "  Launching whisper-server on port $WHISPER_PORT..."
    "$WHISPER_BIN" --port "$WHISPER_PORT" -m "$MODEL_FILE" > "$APP_DIR/.whisper.log" 2>&1 &
    WHISPER_PID=$!
    # Wait for it to be ready
    for i in $(seq 1 30); do
        if curl -s "http://127.0.0.1:$WHISPER_PORT/health" >/dev/null 2>&1; then
            echo "  ✓  whisper-server ready (PID $WHISPER_PID)"
            break
        fi
        if [ $i -eq 30 ]; then
            echo "  ⚠  whisper-server not responding — check .whisper.log"
        fi
        sleep 1
    done
fi

# ── 8. Launch Flask server ─────────────────────────────────────
section "Launch"
echo ""
echo "  🚀  Starting C0D3.5P34K..."
echo ""
echo "  Press Ctrl+C to stop everything."
echo ""

export C0D3_PORT="${C0D3_PORT:-9998}"
exec "$VENV_DIR/bin/python3" "$APP_DIR/server.py"
