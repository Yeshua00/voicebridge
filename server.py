#!/usr/bin/env python3
"""
C0D3.5P34K — iPhone-to-Mac voice relay + remote mouse over local network.
Serves a PWA web page that records from iPhone/iPad browser mic,
transcribes via whisper-server, and pastes at cursor.
Also provides a remote trackpad with tap-to-click and scroll.

Self-contained: all paths relative to this script's directory.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import Quartz
import requests
from flask import Flask, jsonify, request, send_from_directory

# ── Paths (all relative to this script) ──────────────────────────
APP_DIR = Path(__file__).parent.resolve()
WEB_DIR = APP_DIR / "web"
CERTS_DIR = APP_DIR / ".certs"
CERTS_DIR.mkdir(exist_ok=True)

CERT_FILE = CERTS_DIR / "cert.pem"
KEY_FILE = CERTS_DIR / "key.pem"

WHISPER_URL = os.environ.get("WHISPER_URL", "http://127.0.0.1:9999/inference")
PORT = int(os.environ.get("C0D3_PORT", 9998))

app = Flask(__name__)


# ── Self-signed certificate (auto-generated) ─────────────────────

def _ensure_cert():
    """Generate a self-signed cert with openssl if missing."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return True
    print("[c0d3sp34k] Generating self-signed certificate...", flush=True)
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:4096",
             "-keyout", str(KEY_FILE),
             "-out", str(CERT_FILE),
             "-days", "3650", "-nodes",
             "-subj", "/CN=C0D3.5P34K"],
            check=True, capture_output=True, timeout=30,
        )
        # Restrict permissions
        KEY_FILE.chmod(0o600)
        CERT_FILE.chmod(0o644)
        print(f"[c0d3sp34k] Cert written to {CERT_FILE}", flush=True)
        return True
    except FileNotFoundError:
        print("[c0d3sp34k] WARNING: openssl not found — will serve HTTP only", flush=True)
        return False
    except Exception as e:
        print(f"[c0d3sp34k] Cert generation failed: {e}", flush=True)
        return False


# ── Local network detection ──────────────────────────────────────

def _get_local_ips():
    """Return list of non-loopback IPv4 addresses."""
    import socket
    ips = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            addr = info[4][0]
            if addr and not addr.startswith("127.") and "." in addr:
                ips.add(addr)
    except Exception:
        pass
    # Also try ifconfig as fallback
    try:
        out = subprocess.run(
            ["ifconfig"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "inet":
                addr = parts[1]
                if not addr.startswith("127."):
                    ips.add(addr)
    except Exception:
        pass
    return sorted(ips)


def _print_startup_info():
    protocol = "https" if (CERT_FILE.exists() and KEY_FILE.exists()) else "http"
    ips = _get_local_ips()
    
    print(f"\n{'═' * 60}", flush=True)
    print(f"  ╔════════════════════════════════════════════╗", flush=True)
    print(f"  ║        C0D3.5P34K is running              ║", flush=True)
    print(f"  ╚════════════════════════════════════════════╝", flush=True)
    print(f"", flush=True)
    for ip in ips:
        url = f"{protocol}://{ip}:{PORT}"
        print(f"  🌐  {url}", flush=True)
    print(f"", flush=True)
    print(f"  Open one of the above URLs in Safari on your iPhone.", flush=True)
    print(f"  Add to Home Screen for full PWA experience.", flush=True)
    print(f"", flush=True)
    if protocol == "http":
        print(f"  ⚠  HTTP only — mic requires HTTPS on iOS Safari.", flush=True)
        print(f"  Install openssl and restart for HTTPS support.", flush=True)
        print(f"", flush=True)
    print(f"{'═' * 60}\n", flush=True)


# ── Routes ───────────────────────────────────────────────────────

@app.route("/")
def index():
    resp = send_from_directory(str(WEB_DIR), "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/<path:filename>")
def static_files(filename):
    resp = send_from_directory(str(WEB_DIR), filename)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/health")
def health():
    whisper_ok = False
    try:
        r = requests.get("http://127.0.0.1:9999/health", timeout=3)
        whisper_ok = r.status_code == 200
    except Exception:
        pass
    return jsonify({"whisper": whisper_ok, "status": "ok"})


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files and "file" not in request.files:
        return jsonify({"error": "No audio file in request"}), 400

    f = request.files.get("audio") or request.files.get("file")
    ct = f.content_type or "unknown"
    fn = f.filename or "unnamed"

    with tempfile.NamedTemporaryFile(suffix=".in", delete=False) as tmp:
        f.save(tmp.name)
        raw_path = tmp.name
        sz = os.path.getsize(raw_path)

    print(f"[tr] recv: ct={ct} fn={fn} size={sz}", flush=True)

    if sz < 100:
        print(f"[tr] too small ({sz}B), returning silence", flush=True)
        try: os.unlink(raw_path)
        except OSError: pass
        return jsonify({"text": "(silence detected)"})

    wav_path = raw_path + ".wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path,
             "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", wav_path],
            capture_output=True, timeout=15, check=True,
        )
        ws = os.path.getsize(wav_path)
        print(f"[tr] ffmpeg ok: {sz}B -> {ws}B wav", flush=True)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode()[-300:]
        print(f"[tr] ffmpeg fail: {err}", flush=True)
        try: os.unlink(raw_path)
        except OSError: pass
        return jsonify({"error": f"ffmpeg: {err}"}), 502
    except Exception as e:
        print(f"[tr] ffmpeg exception: {e}", flush=True)
        try: os.unlink(raw_path)
        except OSError: pass
        return jsonify({"error": str(e)}), 500

    try:
        resp = requests.post(
            WHISPER_URL,
            files={"file": ("audio.wav", open(wav_path, "rb"), "audio/wav")},
            timeout=30,
        )
        if resp.status_code == 200:
            text = resp.json().get("text", "").strip()
            print(f"[tr] whisper ok: '{text[:80]}'", flush=True)
            return jsonify({"text": text or "(silence detected)"})
        else:
            print(f"[tr] whisper fail: HTTP {resp.status_code} {resp.text[:200]}", flush=True)
            return jsonify({"error": f"Whisper error: {resp.status_code}"}), 502
    except Exception as e:
        print(f"[tr] whisper exception: {e}", flush=True)
        return jsonify({"error": str(e)}), 500
    finally:
        for p in (raw_path, wav_path):
            try: os.unlink(p)
            except OSError: pass


@app.route("/paste", methods=["POST"])
def paste_text():
    """Copy text to clipboard, Cmd+V at cursor, then Enter."""
    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"ok": False, "error": "No text provided"}), 400
    text = data["text"].strip()
    if not text:
        return jsonify({"ok": False, "error": "Empty text"}), 400

    results = {"clipboard": False, "paste": False, "enter": False}
    try:
        subprocess.run(["pbcopy"], input=text.encode(), timeout=5)
        results["clipboard"] = True
    except Exception as e:
        results["clipboard_error"] = str(e)
    try:
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "v" using command down'],
            timeout=5, check=True)
        results["paste"] = True
        subprocess.run(["osascript", "-e",
            'delay 0.15\ntell application "System Events" to key code 36'],
            timeout=5, check=True)
        results["enter"] = True
    except Exception:
        pass

    return jsonify({"ok": results["clipboard"], "text": text, **results})


@app.route("/interrupt", methods=["POST"])
def interrupt():
    """Press Escape + Ctrl+C to cancel/dismiss."""
    try:
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 53\n'
            'delay 0.05\n'
            'tell application "System Events" to keystroke "c" using control down'],
            timeout=5, check=True)
        return jsonify({"ok": True, "action": "interrupt"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Mouse: Quartz CoreGraphics (no cliclick needed) ─────────────

def _get_mouse_pos():
    event = Quartz.CGEventCreate(None)
    return Quartz.CGEventGetLocation(event)


def _post_mouse_event(event_type, pos, button=0):
    ev = Quartz.CGEventCreateMouseEvent(None, event_type, pos, button)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


@app.route("/mouse-move", methods=["POST"])
def mouse_move():
    data = request.get_json(silent=True) or {}
    dx = int(data.get("dx", 0))
    dy = int(data.get("dy", 0))
    if dx == 0 and dy == 0:
        return jsonify({"ok": True})
    try:
        pos = _get_mouse_pos()
        new_pos = Quartz.CGPoint(pos.x + dx, pos.y + dy)
        _post_mouse_event(Quartz.kCGEventMouseMoved, new_pos, 0)
        return jsonify({"ok": True, "x": int(new_pos.x), "y": int(new_pos.y)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/mouse-click", methods=["POST"])
def mouse_click():
    data = request.get_json(silent=True) or {}
    button = data.get("button", "left")
    is_left = button == "left"
    try:
        pos = _get_mouse_pos()
        btn = Quartz.kCGMouseButtonLeft if is_left else Quartz.kCGMouseButtonRight
        down_type = Quartz.kCGEventLeftMouseDown if is_left else Quartz.kCGEventRightMouseDown
        up_type = Quartz.kCGEventLeftMouseUp if is_left else Quartz.kCGEventRightMouseUp
        _post_mouse_event(down_type, pos, btn)
        _post_mouse_event(up_type, pos, btn)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/mouse-down", methods=["POST"])
def mouse_down():
    """Press and hold left button (for drag)."""
    try:
        pos = _get_mouse_pos()
        _post_mouse_event(Quartz.kCGEventLeftMouseDown, pos, Quartz.kCGMouseButtonLeft)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/mouse-up", methods=["POST"])
def mouse_up():
    """Release left button (end drag)."""
    try:
        pos = _get_mouse_pos()
        _post_mouse_event(Quartz.kCGEventLeftMouseUp, pos, Quartz.kCGMouseButtonLeft)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/mouse-scroll", methods=["POST"])
def mouse_scroll():
    """Scroll by sending arrow key presses."""
    data = request.get_json(silent=True) or {}
    dy = data.get("dy", 0)
    if abs(dy) < 10:
        return jsonify({"ok": True, "scrolled": 0})
    steps = min(int(abs(dy) / 20), 5)
    key_code = 125 if dy > 0 else 126  # down / up arrow
    for _ in range(steps):
        subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to key code {key_code}'],
            timeout=2,
        )
    return jsonify({"ok": True, "scrolled": steps})


# ── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import ssl as _ssl

    has_ssl = _ensure_cert()
    ssl_ctx = None
    if has_ssl:
        ssl_ctx = _ssl.create_default_context(_ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))

    _print_startup_info()

    print(f"[c0d3sp34k] Serving from {WEB_DIR}", flush=True)
    app.run(host="0.0.0.0", port=PORT, debug=False, ssl_context=ssl_ctx)
