# 🔊 C0D3.5P34K

**iPhone-to-Mac dictation + remote mouse. One folder, `./start.sh`, done.**

Open the URL from any browser on your local network. Tap the mic, speak, and
text appears at your cursor like magic. The mouse trackpad lets you point,
click, drag, and scroll — no extra hardware needed.

Drop the folder on a USB drive. Plug into any Mac. Run it. Your phone becomes a
wireless keyboard-and-mouse combo over Wi-Fi.

---

## Quick Start

```bash
# macOS only (uses Quartz for mouse control)
git clone https://github.com/Yeshua00/voicebridge
cd voicebridge
./start.sh
```

Or double-click `C0D3.5P34K.command` in Finder.

**First run** creates a Python virtual environment and installs dependencies
(flask, pyobjc, requests). Everything stays inside the folder — zero system
files, zero config, zero cleanup.

---

## Flow

```
┌─────────────┐           ┌──────────────────┐           ┌───────────────┐
│ iPhone      │  WebRTC   │ C0D3.5P34K       │  HTTP     │ whisper.cpp   │
│ Safari PWA  │──────────►│ Flask server     │──────────►│ (port 9999)   │
│             │  audio    │ (port 9998)       │  .wav     │               │
│             │◄──────────┤                   │◄──────────┤               │
│             │  JSON     │ clipboard +       │  text     │               │
│             │  paste    │ osascript Cmd+V   │           │               │
└─────────────┘           └──────────────────┘           └───────────────┘
                                 │
                                 │ Quartz (CoreGraphics)
                                 ▼
                          Mac mouse pointer
```

---

## Features — Code Breakdown

### 1. 🎤 Voice → Text Pipeline

When you tap the mic on iPhone, the browser captures audio via WebRTC,
sends it to the server, which feeds it to `whisper.cpp` and pastes the
result at your cursor.

**iPhone: capture audio with MediaRecorder**

```js
// index.html — getUserMedia requests mic access via WebRTC
let stream = await navigator.mediaDevices.getUserMedia({ audio: {
  sampleRate: 16000,
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true
}});

// Negotiate best codec — Opus in WebM is preferred
let mimeType = "audio/webm;codecs=opus";
if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = "audio/webm";
if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = "";

mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
mediaRecorder.start();
```

**Minimum recording guard** — discards recordings under 2 seconds to avoid
transcribing random taps or silence:

```js
// index.html — silence/short recording guard
if (recordingSeconds < 2) {
  setStatus("Too short (" + recordingSeconds + "s), try again", "error");
  micState("error", "!");
  return;
}
```

**Browser sends raw audio to server:**

```js
// index.html — POST audio blob to server
let r = await fetch(`/transcribe`, {
  method: "POST",
  headers: { "Content-Type": blob.type },
  body: blob
});
let data = await r.json();
let text = data.text;
```

**Server converts to WAV with ffmpeg then sends to whisper:**

```py
# server.py — ffmpeg conversion then whisper inference
with tempfile.NamedTemporaryFile(suffix=".in", delete=False) as tmp:
    f.save(tmp.name)
    raw_path = tmp.name

# Whisper needs 16kHz mono WAV — AAC/Opus won't work
subprocess.run(
    ["ffmpeg", "-y", "-i", raw_path,
     "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", wav_path],
    capture_output=True, timeout=15, check=True,
)

resp = requests.post(
    WHISPER_URL,
    files={"file": ("audio.wav", open(wav_path, "rb"), "audio/wav")},
    timeout=30,
)
text = resp.json().get("text", "").strip()
```

---

### 2. ⌨️ Paste at Cursor

Transcribed text gets copied to clipboard and injected via macOS Accessibility
keystrokes — works in any app: terminal, editor, browser, Slack, etc.

```py
# server.py — clipboard + Cmd+V + Enter
subprocess.run(["pbcopy"], input=text.encode(), timeout=5)

# Paste with Cmd+V via System Events (needs Accessibility permission)
subprocess.run(["osascript", "-e",
    'tell application "System Events" to keystroke "v" using command down'],
    timeout=5, check=True)

# Press Enter to submit
subprocess.run(["osascript", "-e",
    'delay 0.15\ntell application "System Events" to key code 36'],
    timeout=5, check=True)
```

The **ABORT** button sends Escape + Ctrl+C to cancel out of any stuck prompt:

```py
# server.py — interrupt: Escape then Ctrl+C
subprocess.run(["osascript", "-e",
    'tell application "System Events" to key code 53\n'
    'delay 0.05\n'
    'tell application "System Events" to keystroke "c" using control down'],
    timeout=5, check=True)
```

---

### 3. 🖱️ Remote Mouse Trackpad

A full trackpad rendered in the browser. Touch drag moves your Mac cursor.
Tap-to-click. Drag-to-scroll. Sensitivity control. All without cliclick —
uses Quartz CoreGraphics directly.

**Touch tracking with velocity-based acceleration:**

```js
// index.html — trackpad touch handling
let startX, startY, lastX, lastY, lastTime;
const DRAG_BUFFER = 6;   // px before movement starts
const TAP_TIMEOUT = 250; // ms to distinguish tap vs drag

function updateTrackpad(e) {
  e.preventDefault();
  let touch = e.touches[0];
  let rect = trackpad.getBoundingClientRect();
  
  if (e.type === 'touchstart') {
    startX = touch.clientX;
    startY = touch.clientY;
    lastX = touch.clientX;
    lastY = touch.clientY;
    lastTime = Date.now();
    return;
  }
  
  // Compute distance with velocity-based acceleration
  let dx = touch.clientX - lastX;
  let dy = touch.clientY - lastY;
  let dist = Math.hypot(dx, dy);
  let dt = Date.now() - lastTime;
  let velocity = dist / Math.max(dt, 16);
  
  // Dynamic acceleration: fast flicks = bigger cursor moves
  let accel = Math.min(3.5, 1 + velocity * 0.04);
  let moveX = Math.round(dx * accel * SENSITIVITY);
  let moveY = Math.round(dy * accel * SENSITIVITY);
  
  fetch('/mouse-move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dx: moveX, dy: moveY })
  });
  
  lastX = touch.clientX;
  lastY = touch.clientY;
  lastTime = Date.now();
}
```

**Tap-to-click detection** — if finger stays within 6px for 250ms, it's a click:

```js
// index.html — tap vs drag disambiguation
function handleTouchEnd(e) {
  let dx = lastX - startX;
  let dy = lastY - startY;
  let dist = Math.hypot(dx, dy);
  let elapsed = Date.now() - startTime;
  
  if (dist < DRAG_BUFFER && elapsed < TAP_TIMEOUT) {
    // It was a tap — send click
    fetch('/mouse-click', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ button: 'left' })
    });
  }
}
```

**Server handles all mouse events via Quartz (no cliclick):**

```py
# server.py — Quartz CoreGraphics for all mouse operations
def _get_mouse_pos():
    event = Quartz.CGEventCreate(None)
    return Quartz.CGEventGetLocation(event)

def _post_mouse_event(event_type, pos, button=0):
    ev = Quartz.CGEventCreateMouseEvent(None, event_type, pos, button)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

@app.route("/mouse-click", methods=["POST"])
def mouse_click():
    button = request.json.get("button", "left")
    pos = _get_mouse_pos()
    is_left = button == "left"
    btn = Quartz.kCGMouseButtonLeft if is_left else Quartz.kCGMouseButtonRight
    down_type = Quartz.kCGEventLeftMouseDown if is_left else Quartz.kCGEventRightMouseDown
    up_type = Quartz.kCGEventLeftMouseUp if is_left else Quartz.kCGEventRightMouseUp
    _post_mouse_event(down_type, pos, btn)
    _post_mouse_event(up_type, pos, btn)
    return jsonify({"ok": True})
```

**Drag** — press CLICK button (turns into a "drag hold"), move finger, release:

```py
@app.route("/mouse-down", methods=["POST"])
def mouse_down():
    pos = _get_mouse_pos()
    _post_mouse_event(Quartz.kCGEventLeftMouseDown, pos, Quartz.kCGMouseButtonLeft)
    return jsonify({"ok": True})

@app.route("/mouse-up", methods=["POST"])
def mouse_up():
    pos = _get_mouse_pos()
    _post_mouse_event(Quartz.kCGEventLeftMouseUp, pos, Quartz.kCGMouseButtonLeft)
    return jsonify({"ok": True})
```

**Scroll** — swipe on trackpad sends arrow key presses proportional to distance:

```py
@app.route("/mouse-scroll", methods=["POST"])
def mouse_scroll():
    dy = request.json.get("dy", 0)
    if abs(dy) < 10:
        return jsonify({"ok": True, "scrolled": 0})
    steps = min(int(abs(dy) / 20), 5)
    key_code = 125 if dy > 0 else 126  # down arrow / up arrow
    for _ in range(steps):
        subprocess.run(["osascript", "-e",
            f'tell application "System Events" to key code {key_code}'])
    return jsonify({"ok": True, "scrolled": steps})
```

---

### 4. 🎨 UI — Cyberpunk Neon Aesthetic

The entire UI is a single HTML file with embedded CSS. No frameworks, no
build step, no dependencies.

**Color palette** — pure dark + neon green with yellow secondary:

```css
/* index.html — cyberpunk neon palette */
body {
  background: linear-gradient(to bottom, #000000 0%, #0a0a0f 100%);
  color: #00ff41;              /* primary neon green */
}
.subtitle { color: #ffcc00; }  /* secondary yellow */
.badge   { color: #ffcc00; }   /* tertiary gold */
.status.error { color: #ff3355; } /* error red */
```

**Scanlines** — subtle CRT monitor effect scrolling top-to-bottom:

```css
/* index.html — passive scanline overlay */
.scan-lines {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: repeating-linear-gradient(
    to bottom,
    transparent 0px, transparent 8px,
    rgba(0, 255, 65, 0.025) 8px, rgba(0, 255, 65, 0.025) 10px
  );
  background-size: 100% 20px;
  animation: scan-scroll 12s linear infinite;
  pointer-events: none;  /* clicks pass through */
}

@keyframes scan-scroll {
  0%   { background-position: 0 0; }
  100% { background-position: 0 20px; }
}
```

**Radar sweep** — a scan line glides down the screen every 6 seconds:

```css
/* index.html — radar sweep with overlay blend mode */
.radar-sweep {
  position: fixed;
  left: 0; right: 0;
  height: 120px;
  background: linear-gradient(to bottom,
    transparent 0%, rgba(0, 255, 65, 0.0) 15%,
    rgba(0, 255, 65, 0.06) 35%, rgba(0, 255, 65, 0.12) 50%,
    rgba(0, 255, 65, 0.06) 65%, rgba(0, 255, 65, 0.0) 85%,
    transparent 100%);
  mix-blend-mode: overlay;
  animation: radar-sweep 6s linear infinite;
  pointer-events: none;
}

@keyframes radar-sweep {
  0%   { top: -120px; }
  100% { top: 100vh; }
}
```

**Pulse rings** — concentric circles breathe behind the mic button:

```css
/* index.html — radar pulse rings */
.radar-rings {
  position: absolute;
  top: 50%; left: 50%;
  width: 340px; height: 340px;
  margin: -170px 0 0 -170px;
  border-radius: 50%;
  background: radial-gradient(circle,
    rgba(0, 255, 65, 0.08) 0%, transparent 25%,
    rgba(0, 255, 65, 0.04) 40%, transparent 60%,
    rgba(0, 255, 65, 0.02) 75%, transparent 100%);
  mix-blend-mode: overlay;
  animation: rings-pulse 8s ease-in-out infinite;
}

@keyframes rings-pulse {
  0%, 100% { transform: scale(1);    opacity: 0.5; }
  50%      { transform: scale(1.3);  opacity: 0.15; }
}
```

**Recording state** — mic pulses red with animated glow:

```css
/* index.html — recording glow pulse */
.mic-btn.recording {
  border-color: rgba(255, 51, 85, 0.5);
  color: #ff3355;
  box-shadow:
    0 0 20px rgba(255, 51, 85, 0.25),
    0 0 40px rgba(255, 51, 85, 0.1);
  animation: glow-pulse 1.2s ease-in-out infinite;
}

@keyframes glow-pulse {
  0%, 100% {
    transform: scale(1);
    box-shadow: 0 0 20px rgba(255, 51, 85, 0.25);
  }
  50% {
    transform: scale(1.05);
    box-shadow: 0 0 30px rgba(255, 51, 85, 0.4);
  }
}
```

**Tap ripple** — gradient bloom radiates from touch point:

```css
/* index.html — tap ripple effect */
.ripple {
  position: fixed;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: radial-gradient(circle,
    rgba(0, 255, 65, 0.15) 0%, transparent 70%);
  pointer-events: none;
  animation: ripple-expand 0.6s ease-out forwards;
}

@keyframes ripple-expand {
  0%   { transform: translate(-50%, -50%) scale(1);   opacity: 0.5; }
  100% { transform: translate(-50%, -50%) scale(60);  opacity: 0; }
}
```

---

### 5. 📱 PWA — Add to iPhone Home Screen

The page is a fully installable Progressive Web App. Meta tags tell iOS to
treat it as a standalone app (no Safari chrome):

```html
<!-- index.html — PWA meta tags -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="C0D3.5P34K">
<meta name="theme-color" content="#0a0a0f">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" sizes="180x180" href="/icon-180.png">
```

Manifest provides icons at all required sizes:

```json
{
  "name": "C0D3.5P34K",
  "short_name": "C0D3.5P34K",
  "display": "standalone",
  "background_color": "#0a0a0f",
  "theme_color": "#0a0a0f",
  "icons": [
    { "src": "/icon-180.png", "sizes": "180x180", "type": "image/png" },
    { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable" },
    { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ],
  "start_url": "/"
}
```

After adding to the home screen, tapping the icon launches C0D3.5P34K in
full-screen mode with no address bar — feels like a native app.

---

### 6. 🔄 Landscape / Horizontal Layout

When you rotate your phone, the UI reconfigures into a two-column grid:

```css
/* index.html — landscape responsive grid */
@media (orientation: landscape) {
  .container {
    display: grid;
    grid-template-columns: 1fr 1.6fr;  /* mic column | content column */
    grid-template-rows: auto auto minmax(0, 1fr) auto;
    gap: 2px 16px;
  }
  .mic-area  { grid-column: 1; grid-row: 3; }  /* mic on left */
  .history   { grid-column: 2; grid-row: 3; }  /* history on right */
  .mouse-section { grid-column: 2; grid-row: 4; }
  .mic-btn   { width: 100px; height: 100px; }   /* compact mic */
  .trackpad  { height: 100px; }                  /* compact trackpad */
  .subtitle  { display: none; }                  /* hide subtitle */
}
```

---

### 7. 📋 Paste History

The last 3 pastes are stored in-memory and displayed as tappable items.
Tap any to re-paste it:

```js
// index.html — paste history (newest at bottom)
let pasteHistory = [];
const MAX_HISTORY = 3;

function renderHistory() {
  historyEl.innerHTML = pasteHistory.map((t, i) =>
    `<div class="history-item" data-index="${i}">
       ${t.length > 60 ? t.slice(0, 60) + '…' : t}
     </div>`
  ).join("");
}

historyEl.addEventListener("click", e => {
  let item = e.target.closest(".history-item");
  if (!item) return;
  let text = pasteHistory[parseInt(item.dataset.index)];
  if (text) sendToPaste(text);
});

// Capped at 3 items, newest bottom
function addHistory(text) {
  pasteHistory.push(text);
  if (pasteHistory.length > MAX_HISTORY) pasteHistory.shift();
  renderHistory();
  historyEl.scrollTop = historyEl.scrollHeight;
}
```

---

### 8. 🔐 Auto HTTPS (Self-Signed)

iOS Safari requires HTTPS for microphone access. The server auto-generates
a self-signed certificate on first run using openssl:

```py
# server.py — auto-cert generation
def _ensure_cert():
    if CERT_FILE.exists() and KEY_FILE.exists():
        return True
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:4096",
         "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
         "-days", "3650", "-nodes",
         "-subj", "/CN=C0D3.5P34K"],
        check=True, capture_output=True, timeout=30,
    )
    KEY_FILE.chmod(0o600)
    return True
```

Your browser will show a "not trusted" warning for a self-signed cert.
Tap "Show Details" → "Visit Website Anyway" — required once per install.

---

### 9. 🌐 Local Network Auto-Detect

On startup, the server finds your local IPs and prints clickable URLs:

```py
# server.py — detect local network interfaces
def _get_local_ips():
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
    # Fallback: parse ifconfig
    out = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "inet":
            addr = parts[1]
            if not addr.startswith("127."):
                ips.add(addr)
    return sorted(ips)
```

---

### 10. 📦 Self-Contained — No System Files

The `start.sh` script creates everything inside the app folder. Zero files
written outside the directory tree:

```bash
# start.sh — portable setup, nothing leaves this folder
VENV_DIR="$APP_DIR/.venv"    # ← inside ./voicebridge/.venv/
$PYTHON -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

# certs go in .certs/ subfolder (auto-created by server.py)
# logs go in server.log
# __pycache__ stays local
```

No brew dependencies, no launchd plists, no `~/.config` files, no system-wide
Python packages. Delete the folder = complete uninstall.

---

## Requirements

| Dependency | Where | Why |
|---|---|---|
| **Python 3** | system | Flask server, Quartz mouse control |
| **openssl** | system | Generate HTTPS cert (optional — HTTP works but mic needs HTTPS) |
| **ffmpeg** | system | Convert iPhone audio to WAV for whisper |
| **whisper.cpp** | user-installed | Speech-to-text server on port 9999 (e.g. `whisper-server --port 9999`) |
| macOS **Accessibility** | System Settings | `osascript` keystroke injection for paste |

---

## Architecture

```
                ╔═══════════════════════╗
                ║   iPhone Safari PWA   ║
                ║  ┌─────────────────┐  ║
                ║  │  getUserMedia    │  ║  ← WebRTC mic capture
                ║  │  MediaRecorder   │  ║  ← Opus audio chunks
                ║  │  Touch events    │  ║  ← Trackpad input
                ║  └─────────────────┘  ║
                ╚══════╤════════════════╝
                       │ HTTPS (Tailscale / LAN)
                       ▼
         ╔═══════════════════════════════╗
         ║   C0D3.5P34K Flask Server     ║
         ║                               ║
         ║  ┌─────────┐  ┌───────────┐   ║
         ║  │ /paste  │  │ /mouse-*  │   ║
         ║  │ pbcopy  │  │ Quartz    │   ║
         ║  │ osascript│  │ CGEvent   │   ║
         ║  │ Cmd+V   │  │ Post      │   ║
         ║  └────┬────┘  └─────┬─────┘   ║
         ║       │             │         ║
         ╚═══════╪═════════════╪═════════╝
                 │             │
                 ▼             ▼
         ┌─────────────┐ ┌──────────┐
         │ Any macOS   │ │ Mac      │
         │ app (focus) │ │ cursor   │
         └─────────────┘ └──────────┘
                 │
                 ▼
         ┌───────────────┐
         │ whisper.cpp   │
         │ :9999         │
         └───────────────┘
```

The server is stateless (except paste history, which lives in the browser).
Whisper runs as a separate process — any OpenAI-compatible STT endpoint works
by setting `WHISPER_URL`.

---

## Files

```
voicebridge/
├── C0D3.5P34K.command   # Double-click in Finder to launch
├── start.sh              # Terminal entry point
├── server.py             # Flask backend (354 lines)
├── requirements.txt      # Python dependencies
├── .gitignore
└── web/
    ├── index.html        # Full PWA: UI + JS + CSS (1400 lines, one file)
    ├── manifest.json     # PWA manifest
    ├── icon.svg          # SVG favicon / browser tab icon
    ├── icon-180.png      # Apple touch icon (180×180)
    ├── icon-192.png      # PWA icon (192×192, maskable)
    └── icon-512.png      # PWA icon (512×512, maskable)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Vanilla HTML / CSS / JS — single file, no frameworks, no build step |
| Backend | Flask (Python 3) |
| Mouse | Quartz CoreGraphics (pyobjc bindings) |
| Keystrokes | macOS Accessibility via osascript |
| Audio | WebRTC → MediaRecorder → whisper.cpp |
| HTTPS | Self-signed cert via openssl |
| Transport | HTTPS + JSON REST |
| Packaging | Python venv (self-contained) |
