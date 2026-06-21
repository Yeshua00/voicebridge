#!/usr/bin/env python3
"""
OpenCode Voice Bridge — iOS voice input over Tailscale.
Serves a web page that records from iPhone/iPad browser mic,
transcribes via whisper-server, and injects into OpenCode.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import Quartz
import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)
WHISPER_URL = "http://127.0.0.1:9999/inference"
OPENCODE_ADDR = os.environ.get("OPENCODE_ADDR", "http://localhost:4096")
CLICLICK = "/opt/homebrew/bin/cliclick"
STATIC_DIR = os.path.expanduser("~/voice-bridge/web")

ENV_FILE = os.path.expanduser("~/.voice-bridge/.opencode-env")

def _detect_opencode_env():
    env = os.environ
    needed = {"OPENCODE_SERVER_PASSWORD"}
    if needed.issubset(env):
        return
    # Read from env file
    errs = []
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        errs.append(f"{ENV_FILE} not found")
    except PermissionError:
        errs.append(f"{ENV_FILE} permission denied")
    except Exception as e:
        errs.append(f"{ENV_FILE} error: {e}")
    if needed.issubset(env):
        return
    # Fallback: read from login shell
    try:
        r = subprocess.run(
            ["/bin/zsh", "-l", "-c",
             "echo O_PW=$OPENCODE_SERVER_PASSWORD"],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.strip().split("\n"):
            if line.startswith("O_PW="):
                env.setdefault("OPENCODE_SERVER_PASSWORD", line.split("=", 1)[1])
    except Exception:
        pass
    print(f"[voice-bridge] Env detection: file_errs={errs}, "
          f"has_pw={'OPENCODE_SERVER_PASSWORD' in env}, "
          f"pw_start={env.get('OPENCODE_SERVER_PASSWORD','?')[:8]}",
          flush=True)

_detect_opencode_env()


@app.route("/")
def index():
    resp = send_from_directory(STATIC_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/echo", methods=["GET", "POST"])
def echo():
    """Debug endpoint — echoes back what the client sent."""
    info = {
        "method": request.method,
        "headers": dict(request.headers),
        "args": dict(request.args),
        "content_type": request.content_type,
    }
    if request.files:
        info["files"] = {k: {"name": v.filename, "type": v.content_type} for k, v in request.files.items()}
    if request.form:
        info["form"] = dict(request.form)
    try:
        info["json"] = request.get_json(silent=True)
    except Exception:
        pass
    if request.data:
        info["body_size"] = len(request.get_data())
    return jsonify(info)


def _check_accessibility():
    """Test if macOS Accessibility (keystroke injection) is actually working.
    Returns True if cliclick can inject keys, False otherwise."""
    if not os.path.isfile(CLICLICK):
        return False
    try:
        # Harmless shift press+release — tests Accessibility permission
        subprocess.run([CLICLICK, "kd:shift", "ku:shift"], timeout=3, check=True,
                       capture_output=True)
        return True
    except Exception:
        return False


@app.route("/health")
def health():
    whisper_ok = False
    opencode_ok = False
    try:
        r = requests.get("http://127.0.0.1:9999/health", timeout=3)
        whisper_ok = r.status_code == 200
    except Exception:
        pass
    try:
        r = subprocess.run(["opencode", "session", "list"], capture_output=True, timeout=5)
        opencode_ok = r.returncode == 0
    except Exception:
        pass
    cliclick_ok = os.access(CLICLICK, os.X_OK) if os.path.isfile(CLICLICK) else False
    accessibility_ok = _check_accessibility()
    return jsonify({
        "whisper": whisper_ok,
        "opencode": opencode_ok,
        "cliclick": cliclick_ok,
        "accessibility": accessibility_ok,
    })


@app.route("/sessions")
def list_sessions():
    try:
        result = subprocess.run(
            ["opencode", "session", "list"],
            capture_output=True, text=True, timeout=10,
        )
        sessions = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 2 and not line.startswith("─") and "Session ID" not in line:
                sessions.append({"id": parts[0], "title": " ".join(parts[1:])})
        return jsonify({"sessions": sessions[:10], "error": result.stderr.strip() or None})
    except Exception as e:
        return jsonify({"sessions": [], "error": str(e)})


@app.route("/transcribe", methods=["POST"])
def transcribe():
    import sys, os

    if "audio" not in request.files and "file" not in request.files:
        return jsonify({"error": "No audio file in request"}), 400

    f = request.files.get("audio") or request.files.get("file")
    ct = f.content_type or "unknown"
    fn = f.filename or "unnamed"
    sz = 0

    with tempfile.NamedTemporaryFile(suffix=".in", delete=False) as tmp:
        f.save(tmp.name)
        raw_path = tmp.name
        sz = os.path.getsize(raw_path)

    print(f"[tr] recv: ct={ct} fn={fn} size={sz}", flush=True)
    wav_path = raw_path + ".wav"

    if sz < 100:
        print(f"[tr] too small ({sz}B), returning silence", flush=True)
        try: os.unlink(raw_path)
        except OSError: pass
        return jsonify({"text": "(silence detected)"})

    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path,
             "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
             wav_path],
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


@app.route("/inject", methods=["POST"])
def inject():
    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"ok": False, "error": "No text provided"}), 400
    text = data["text"].strip()
    if not text:
        return jsonify({"ok": False, "error": "Empty text"}), 400

    try:
        session_id = data.get("session") or None
        text_arg = text

        if session_id:
            cmd = ["opencode", "run", "--attach", OPENCODE_ADDR,
                   "-s", session_id, text_arg]
        else:
            cmd = ["opencode", "run", "--attach", OPENCODE_ADDR,
                   text_arg]

        inject_result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )

        if inject_result.returncode != 0 and session_id:
            fallback_cmd = ["opencode", "run", "--attach", OPENCODE_ADDR, text_arg]
            inject_result = subprocess.run(
                fallback_cmd, capture_output=True, text=True, timeout=30,
            )
            session_id = "default"

        ok = inject_result.returncode == 0
        err = inject_result.stderr.strip() or None
        return jsonify({
            "ok": ok,
            "session": session_id,
            "error": err if not ok else None,
        }), 200 if ok else 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/send", methods=["POST"])
def send():
    """Alias for /inject — index.html calls /send."""
    return inject()


@app.route("/paste", methods=["POST"])
def paste_text():
    """Copy text to clipboard, Cmd+V at cursor, then press Enter to submit.
    Clipboard always works. Paste needs macOS Accessibility for osascript/cliclick."""
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

    cliclick_available = os.path.isfile(CLICLICK)
    if cliclick_available:
        try:
            subprocess.run([CLICLICK, "kd:cmd", "kp:v", "ku:cmd"], timeout=5, check=True)
            results["paste"] = True
            subprocess.run([CLICLICK, "kp:return"], timeout=5, check=True)
            results["enter"] = True
        except Exception:
            pass

    if not results.get("paste"):
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
            results["paste"] = False
            results["enter"] = False

    return jsonify({"ok": results["clipboard"], "text": text, **results})


@app.route("/interrupt", methods=["POST"])
def interrupt():
    """Press Escape (+ Ctrl+C) to cancel/dismiss in any app."""
    try:
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 53\n'
            'delay 0.05\n'
            'tell application "System Events" to keystroke "c" using control down'],
            timeout=5, check=True)
        return jsonify({"ok": True, "action": "interrupt"})
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": "osascript failed", "detail": str(e)}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/mouse-move", methods=["POST"])
def mouse_move():
    """Move mouse RELATIVE to current position by (dx, dy) pixels.
    
    Uses CoreGraphics (Quartz) for fast in-process mouse movement
    instead of subprocessing to cliclick.
    """
    data = request.get_json(silent=True) or {}
    dx = int(data.get("dx", 0))
    dy = int(data.get("dy", 0))
    if dx == 0 and dy == 0:
        return jsonify({"ok": True})
    try:
        event = Quartz.CGEventCreate(None)
        pos = Quartz.CGEventGetLocation(event)
        new_pos = Quartz.CGPoint(pos.x + dx, pos.y + dy)
        move_event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, new_pos, 0,
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, move_event)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/mouse-click", methods=["POST"])
def mouse_click():
    """Send a mouse click at current cursor position."""
    data = request.get_json(silent=True) or {}
    button = data.get("button", "left")
    cmd = "c:." if button == "left" else "rc:."
    try:
        subprocess.run([CLICLICK, cmd], timeout=2, check=True)
        return jsonify({"ok": True})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "cliclick timed out"}), 504
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/mouse-down", methods=["POST"])
def mouse_down():
    """Press and hold mouse button (for drag)."""
    try:
        subprocess.run([CLICLICK, "dd:."], timeout=2, check=True)
        return jsonify({"ok": True})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "cliclick timed out"}), 504
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/mouse-up", methods=["POST"])
def mouse_up():
    """Release mouse button (end drag)."""
    try:
        subprocess.run([CLICLICK, "du:."], timeout=2, check=True)
        return jsonify({"ok": True})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "cliclick timed out"}), 504
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/mouse-scroll", methods=["POST"])
def mouse_scroll():
    """Scroll by sending arrow up/down key presses."""
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


@app.route("/<path:filename>")
def static_files(filename):
    """Serve static assets (icons, manifest, etc)."""
    resp = send_from_directory(STATIC_DIR, filename)
    # Allow caching for static assets (iOS needs to cache the icon for PWA)
    if filename.endswith((".png", ".svg", ".ico", ".webmanifest")):
        resp.headers["Cache-Control"] = "public, max-age=86400, must-revalidate"
    else:
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


if __name__ == "__main__":
    import ssl as _ssl
    import threading
    port = int(os.environ.get("VOICE_BRIDGE_PORT", 9998))
    http_port = int(os.environ.get("VOICE_BRIDGE_HTTP_PORT", 9997))
    cert_file = os.path.expanduser("~/.voice-bridge/cert.pem")
    key_file = os.path.expanduser("~/.voice-bridge/key.pem")
    has_ssl = os.path.isfile(cert_file) and os.path.isfile(key_file)
    ssl_ctx = None
    if has_ssl:
        ssl_ctx = _ssl.create_default_context(_ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(cert_file, key_file)
    
    import werkzeug.serving as ws
    th = threading.Thread(
        target=ws.run_simple,
        args=("127.0.0.1", http_port, app),
        kwargs={"use_debugger": False, "use_reloader": False},
        daemon=True,
    )
    th.start()
    print(f"[voice-bridge] HTTP backend on 127.0.0.1:{http_port} (for tailscale serve)")
    
    print(f"[voice-bridge] Starting on 0.0.0.0:{port} {'HTTPS' if has_ssl else 'HTTP'}")
    print(f"[voice-bridge]   Serving static from {STATIC_DIR}")
    print(f"[voice-bridge]   Whisper at {WHISPER_URL}")
    print(f"[voice-bridge]   Tailscale: https://100.127.167.105:{port}")

    # Startup diagnostics
    acc = _check_accessibility()
    if acc:
        print("[voice-bridge]   ✓ Accessibility granted — keystroke injection works")
    else:
        print("[voice-bridge]   ✗ Accessibility DENIED — paste will copy to clipboard only")
        print("[voice-bridge]     Grant Accessibility permission in:")
        print("[voice-bridge]     System Settings → Privacy & Security → Accessibility")
        print("[voice-bridge]     Add Terminal and/or /opt/homebrew/bin/cliclick")

    app.run(host="0.0.0.0", port=port, debug=False, ssl_context=ssl_ctx)
