"""Flask dashboard — config GUI + process launcher for RedClaw."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Generator

from flask import Flask, Response, jsonify, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Shared log helpers
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()
_log_buffer: deque[str] = deque(maxlen=2000)


def _log_msg(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    with _log_lock:
        _log_buffer.append(f"[{ts}] {msg}")


def get_logs_snapshot() -> list[str]:
    with _log_lock:
        return list(_log_buffer)


def clear_logs() -> None:
    with _log_lock:
        _log_buffer.clear()


# ---------------------------------------------------------------------------
# Local MCP server manager
# ---------------------------------------------------------------------------

_SERVERS_DIR = Path(__file__).resolve().parent.parent / "servers"
_VENV311_PYTHON = Path(__file__).resolve().parent.parent / ".venv311" / "Scripts" / "python.exe"

SERVER_DEFS = [
    {"name": "TTS", "script": "tts_server.py", "port": 8006,
     "python": str(_VENV311_PYTHON) if _VENV311_PYTHON.exists() else None},
    {"name": "STT", "script": "stt_server.py", "port": 8007},
    {"name": "Web Reader", "script": "web_reader_server.py", "port": 8003},
]


class ServerManager:
    """Manages local MCP server subprocesses (TTS, STT, Web Reader)."""

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen] = {}

    @staticmethod
    def _kill_port(port: int) -> None:
        """Kill any process holding the given port."""
        if sys.platform == "win32":
            try:
                out = subprocess.check_output(
                    f'netstat -ano | findstr ":{port} " | findstr LISTENING',
                    shell=True, text=True,
                )
                for line in out.strip().splitlines():
                    parts = line.split()
                    pid = int(parts[-1])
                    subprocess.call(f'taskkill /F /PID {pid} /T', shell=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (subprocess.CalledProcessError, ValueError, IndexError):
                pass
        else:
            import fcntl
            try:
                out = subprocess.check_output(['lsof', '-ti', f':{port}'], text=True)
                for pid in out.strip().splitlines():
                    subprocess.call(['kill', '-9', pid.strip()])
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass

    def start(self, name: str) -> str | None:
        # Clean up dead entries
        proc = self._procs.get(name)
        if proc and proc.poll() is not None:
            self._procs.pop(name, None)

        if name in self._procs:
            return f"{name} already running"

        sdef = next((s for s in SERVER_DEFS if s["name"] == name), None)
        if not sdef:
            return f"Unknown server: {name}"
        script = _SERVERS_DIR / sdef["script"]
        if not script.exists():
            return f"Script not found: {script}"

        # Kill anything holding the port before starting
        self._kill_port(sdef["port"])

        python = sdef.get("python") or sys.executable
        cmd = [python, str(script), "--port", str(sdef["port"])]
        env = os.environ.copy()
        env["COQUI_TOS_AGREED"] = "1"
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                bufsize=1,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
        except Exception as exc:
            return str(exc)
        self._procs[name] = proc
        _log_msg(f"▶ {name} started on port {sdef['port']} (PID {proc.pid})")
        threading.Thread(target=self._reader, args=(name, proc), daemon=True).start()
        return None

    def stop(self, name: str) -> str | None:
        proc = self._procs.get(name)
        if not proc or proc.poll() is not None:
            self._procs.pop(name, None)
            return f"{name} not running"
        # Kill the process tree (handles FastMCP spawning child uvicorn workers)
        if sys.platform == "win32":
            subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        self._procs.pop(name, None)
        _log_msg(f"■ {name} stopped")
        return None

    def start_all(self) -> list[str]:
        errors = []
        for sdef in SERVER_DEFS:
            err = self.start(sdef["name"])
            if err:
                errors.append(err)
        return errors

    def stop_all(self) -> None:
        for name in list(self._procs):
            self.stop(name)

    def status(self) -> dict:
        result = {}
        for sdef in SERVER_DEFS:
            name = sdef["name"]
            proc = self._procs.get(name)
            running = proc is not None and proc.poll() is None
            result[name] = {
                "running": running,
                "port": sdef["port"],
                "pid": proc.pid if running else None,
            }
        return result

    def _reader(self, name: str, proc: subprocess.Popen) -> None:
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                _log_msg(f"[{name}] {line}")
        except ValueError:
            pass
        rc = proc.wait()
        # Clean up dead proc from dict so restart works
        if self._procs.get(name) is proc:
            self._procs.pop(name, None)
        _log_msg(f"■ {name} exited with code {rc}")


sm = ServerManager()


# ---------------------------------------------------------------------------
# RedClaw process manager
# ---------------------------------------------------------------------------

class ProcessManager:
    """Manages a single RedClaw subprocess with log capture."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self.running else None

    def start(self, config: dict) -> str | None:
        """Start RedClaw with the given config. Returns error or None."""
        if self.running:
            return "Process already running"

        cmd = [sys.executable, "-m", "redclaw"]
        cmd = self._build_cmd(cmd, config)

        env = os.environ.copy()
        if config.get("telegram_token"):
            env["REDCLAW_TELEGRAM_TOKEN"] = config["telegram_token"]
        if config.get("telegram_user_id"):
            env["REDCLAW_TELEGRAM_USER_ID"] = str(config["telegram_user_id"])

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                bufsize=1,
            )
        except Exception as exc:
            return str(exc)

        _log_msg(f"▶ Started PID {self._process.pid}: {' '.join(cmd)}")
        threading.Thread(target=self._reader_thread, daemon=True).start()
        return None

    def stop(self) -> None:
        if not self.running:
            return
        _log_msg("■ Stopping RedClaw...")
        try:
            if self._process.stdin:
                self._process.stdin.write(b"/quit\n")
                self._process.stdin.flush()
        except Exception:
            pass
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
        _log_msg("■ RedClaw stopped.")
        self._process = None

    def status(self) -> dict:
        return {
            "running": self.running,
            "pid": self.pid,
        }

    def _reader_thread(self) -> None:
        assert self._process and self._process.stdout
        try:
            for raw in self._process.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                _log_msg(line)
        except ValueError:
            pass
        rc = self._process.wait() if self._process else -1
        _log_msg(f"■ Process exited with code {rc}")

    @staticmethod
    def _build_cmd(cmd: list[str], c: dict) -> list[str]:
        if c.get("provider"):
            cmd += ["--provider", c["provider"]]
        if c.get("model"):
            cmd += ["--model", c["model"]]
        if c.get("base_url"):
            cmd += ["--base-url", c["base_url"]]
        if c.get("permission_mode"):
            cmd += ["--permission-mode", c["permission_mode"]]
        if c.get("working_dir"):
            cmd += ["--working-dir", c["working_dir"]]
        if c.get("max_tokens"):
            cmd += ["--max-tokens", str(c["max_tokens"])]
        if c.get("launch_mode") and c["launch_mode"] != "repl":
            cmd += ["--mode", c["launch_mode"]]
        if c.get("port"):
            cmd += ["--port", str(c["port"])]
        if c.get("skills_dir"):
            cmd += ["--skills-dir"] + [s.strip() for s in c["skills_dir"].split(",") if s.strip()]
        if c.get("skills_manage"):
            cmd += ["--skills-manage"]
        if c.get("memory_dir"):
            cmd += ["--memory-dir", c["memory_dir"]]
        if c.get("compact_llm"):
            cmd += ["--compact-llm"]
        if c.get("subagent"):
            cmd += ["--subagent"]
        # Always pass --mcp-servers to override CLI defaults
        # Auto-include any running local MCP servers
        mcp_text = c.get("mcp_servers", "")
        urls = set(u.strip() for u in mcp_text.splitlines() if u.strip())
        for sdef in SERVER_DEFS:
            sname = sdef["name"]
            sproc = sm._procs.get(sname)
            if sproc and sproc.poll() is None:
                urls.add(f"http://localhost:{sdef['port']}/sse")
        cmd += ["--mcp-servers"] + sorted(urls)
        if c.get("tts_url"):
            cmd += ["--tts-url", c["tts_url"]]
        if c.get("stt_url"):
            cmd += ["--stt-url", c["stt_url"]]
        if c.get("search_url"):
            cmd += ["--search-url", c["search_url"]]
        if c.get("reader_url"):
            cmd += ["--reader-url", c["reader_url"]]
        if c.get("verbose"):
            cmd += ["--verbose"]
        if c.get("assistant"):
            cmd += ["--assistant"]
        return cmd


pm = ProcessManager()

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.post("/api/start")
def api_start():
    err = pm.start(request.get_json(force=True))
    if err:
        return jsonify({"error": err}), 400
    return jsonify(pm.status())


@app.post("/api/stop")
def api_stop():
    pm.stop()
    return jsonify(pm.status())


@app.get("/api/status")
def api_status():
    return jsonify({**pm.status(), "servers": sm.status()})


@app.get("/api/logs")
def api_logs_stream():
    def gen():
        sent = 0
        while True:
            with _log_lock:
                logs = list(_log_buffer)
            current = len(logs)
            if current > sent:
                for line in logs[sent:]:
                    yield f"data: {json.dumps(line)}\n\n"
                sent = current
            else:
                yield ": keepalive\n\n"
                time.sleep(0.3)
    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/logs/snapshot")
def api_logs_snapshot():
    return jsonify({"logs": get_logs_snapshot()})


@app.post("/api/logs/clear")
def api_logs_clear():
    clear_logs()
    return jsonify({"ok": True})


# --- Server management ---

@app.get("/api/servers")
def api_servers():
    return jsonify(sm.status())


@app.post("/api/servers/<name>/start")
def api_server_start(name: str):
    err = sm.start(name)
    if err:
        return jsonify({"error": err}), 400
    return jsonify(sm.status())


@app.post("/api/servers/<name>/stop")
def api_server_stop(name: str):
    err = sm.stop(name)
    if err:
        return jsonify({"error": err}), 400
    return jsonify(sm.status())


@app.post("/api/servers/start-all")
def api_servers_start_all():
    errors = sm.start_all()
    if errors:
        return jsonify({"errors": errors}), 400
    return jsonify(sm.status())


@app.post("/api/servers/stop-all")
def api_servers_stop_all():
    sm.stop_all()
    return jsonify(sm.status())


# --- Config save/load ---

import hashlib

_CONFIG_DIR = Path.home() / ".redclaw"
_CONFIG_FILE = _CONFIG_DIR / "dashboard_config.json"
_PIN_FILE = _CONFIG_DIR / "dashboard_pin.hash"


def _hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


@app.post("/api/config/save")
def api_config_save():
    data = request.get_json(force=True)
    pin = str(data.get("pin", ""))
    if len(pin) != 4 or not pin.isdigit():
        return jsonify({"error": "PIN must be exactly 4 digits"}), 400
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _PIN_FILE.write_text(_hash_pin(pin))
    config = {k: v for k, v in data.items() if k != "pin"}
    _CONFIG_FILE.write_text(json.dumps(config, indent=2))
    _log_msg("Config saved.")
    return jsonify({"ok": True})


@app.post("/api/config/load")
def api_config_load():
    data = request.get_json(force=True)
    pin = str(data.get("pin", ""))
    if not _PIN_FILE.exists() or not _CONFIG_FILE.exists():
        return jsonify({"error": "No saved config found"}), 404
    if _hash_pin(pin) != _PIN_FILE.read_text().strip():
        return jsonify({"error": "Invalid PIN"}), 403
    config = json.loads(_CONFIG_FILE.read_text())
    return jsonify({"config": config})


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RedClaw Dashboard</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d1117;--surface:#161b22;--surface2:#1c2128;--border:#30363d;
  --text:#e6edf3;--text-dim:#8b949e;--accent:#e94560;--accent-hover:#ff6b81;
  --green:#3fb950;--yellow:#d29922;--red:#f85149;--blue:#58a6ff;
  --radius:8px;--font:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  --mono:'Cascadia Code','Fira Code','JetBrains Mono',Consolas,monospace;
}
html,body{height:100%;font-family:var(--font);background:var(--bg);color:var(--text);overflow:hidden}
a{color:var(--blue);text-decoration:none}

/* Layout */
.app{display:flex;height:100vh;overflow:hidden}
.sidebar{width:380px;min-width:320px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.sidebar-header{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.sidebar-header h1{font-size:18px;font-weight:700;color:var(--accent);letter-spacing:-0.5px}
.sidebar-header .logo{font-size:22px}
.sidebar-scroll{flex:1;overflow-y:auto;padding:12px 16px 24px}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}

/* Status bar */
.status-bar{padding:12px 20px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px}
.status-dot{width:10px;height:10px;border-radius:50%;background:var(--text-dim);flex-shrink:0}
.status-dot.running{background:var(--green);animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(63,185,80,.5)}50%{box-shadow:0 0 0 6px rgba(63,185,80,0)}}
.status-info{flex:1;font-size:13px;color:var(--text-dim)}
.status-info span{color:var(--text)}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface2);color:var(--text);font-size:13px;font-weight:500;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--text-dim);background:var(--border)}
.btn-primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent-hover);border-color:var(--accent-hover)}
.btn-danger{border-color:var(--red);color:var(--red)}
.btn-danger:hover{background:var(--red);color:#fff}
.btn-sm{padding:4px 10px;font-size:12px}
.btn-group{display:flex;gap:8px}

/* Cards */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:10px;overflow:hidden;transition:border-color .15s}
.card:hover{border-color:var(--text-dim)}
.card-header{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;cursor:pointer;user-select:none;font-size:13px;font-weight:600}
.card-header:hover{background:var(--surface2)}
.card-chevron{transition:transform .2s;font-size:12px;color:var(--text-dim)}
.card.collapsed .card-chevron{transform:rotate(-90deg)}
.card-body{padding:10px 14px 14px;border-top:1px solid var(--border)}
.card.collapsed .card-body{display:none}

/* Form elements */
.field{margin-bottom:10px}
.field:last-child{margin-bottom:0}
.field label{display:block;font-size:11px;font-weight:600;color:var(--text-dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.field input[type="text"],.field input[type="number"],.field input[type="password"],.field select,.field textarea{
  width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;font-family:var(--font);transition:border-color .15s;outline:none}
.field input:focus,.field select:focus,.field textarea:focus{border-color:var(--accent)}
.field textarea{resize:vertical;min-height:60px;font-family:var(--mono);font-size:12px;line-height:1.5}
.field select{cursor:pointer;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%238b949e' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center}
.field select option{background:var(--surface);color:var(--text)}

/* Toggle switch */
.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:4px 0}
.toggle-row .toggle-label{font-size:13px;color:var(--text)}
.toggle{position:relative;width:36px;height:20px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.toggle .slider{position:absolute;inset:0;background:var(--border);border-radius:10px;cursor:pointer;transition:background .2s}
.toggle .slider::before{content:'';position:absolute;left:2px;top:2px;width:16px;height:16px;background:#fff;border-radius:50%;transition:transform .2s}
.toggle input:checked+.slider{background:var(--accent)}
.toggle input:checked+.slider::before{transform:translateX(16px)}

/* Terminal */
.terminal-wrap{flex:1;overflow:hidden;display:flex;flex-direction:column}
.terminal{flex:1;overflow-y:auto;padding:16px 20px;font-family:var(--mono);font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-all;background:var(--bg);color:var(--text-dim)}
.terminal::-webkit-scrollbar{width:8px}
.terminal::-webkit-scrollbar-track{background:transparent}
.terminal::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
.terminal::-webkit-scrollbar-thumb:hover{background:var(--text-dim)}

.log-line{min-height:1.6em}
.log-line.error{color:var(--red)}
.log-line.warn{color:var(--yellow)}
.log-line.info{color:var(--blue)}
.log-line.success{color:var(--green)}
.log-line.accent{color:var(--accent)}

/* Empty state */
.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--text-dim);gap:12px}
.empty-state .icon{font-size:48px;opacity:.3}
.empty-state p{font-size:14px}

/* Server rows */
.server-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)}
.server-row:last-child{border-bottom:none}
.server-dot{width:8px;height:8px;border-radius:50%;background:var(--text-dim);flex-shrink:0}
.server-dot.on{background:var(--green)}
.server-name{flex:1;font-size:13px}
.server-port{font-size:11px;color:var(--text-dim);font-family:var(--mono)}
.server-row .btn-sm{padding:2px 8px;font-size:11px}

/* Modal */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;width:320px;box-shadow:0 8px 32px rgba(0,0,0,.5)}
.modal h3{font-size:16px;margin-bottom:16px;color:var(--accent)}
.modal .pin-inputs{display:flex;gap:8px;justify-content:center;margin-bottom:16px}
.modal .pin-inputs input{width:48px;height:52px;text-align:center;font-size:24px;font-weight:700;background:var(--bg);border:2px solid var(--border);border-radius:8px;color:var(--text);outline:none}
.modal .pin-inputs input:focus{border-color:var(--accent)}
.modal .btn-row{display:flex;gap:8px;justify-content:flex-end}
.modal .error-msg{color:var(--red);font-size:12px;text-align:center;min-height:18px;margin-bottom:8px}

/* Responsive */
@media(max-width:900px){
  .app{flex-direction:column}
  .sidebar{width:100%;min-width:0;max-height:45vh;border-right:none;border-bottom:1px solid var(--border)}
}
</style>
</head>
<body>
<div class="app">
  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="sidebar-header">
      <span class="logo">&#x1F43E;</span>
      <h1>RedClaw</h1>
      <div style="margin-left:auto;display:flex;gap:6px">
        <button class="btn btn-sm" onclick="showPinModal('save')" title="Save config">&#128190;</button>
        <button class="btn btn-sm" onclick="showPinModal('load')" title="Load config">&#128194;</button>
      </div>
    </div>
    <div class="sidebar-scroll" id="configPanel">

      <!-- Servers -->
      <div class="card" id="card-servers">
        <div class="card-header" onclick="toggleCard('servers')">
          <span>Local Servers</span>
          <span class="card-chevron">&#9660;</span>
        </div>
        <div class="card-body">
          <div id="serverRows"></div>
          <div style="margin-top:10px;display:flex;gap:8px">
            <button class="btn btn-sm btn-primary" onclick="startAllServers()" style="flex:1">Start All</button>
            <button class="btn btn-sm btn-danger" onclick="stopAllServers()" style="flex:1">Stop All</button>
          </div>
        </div>
      </div>

      <!-- General -->
      <div class="card" id="card-general">
        <div class="card-header" onclick="toggleCard('general')">
          <span>General</span>
          <span class="card-chevron">&#9660;</span>
        </div>
        <div class="card-body">
          <div class="field">
            <label>Provider</label>
            <select id="provider">
              <option value="zai">Zai</option>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="ollama">Ollama</option>
              <option value="groq">Groq</option>
              <option value="deepseek">DeepSeek</option>
              <option value="openrouter">OpenRouter</option>
            </select>
          </div>
          <div class="field">
            <label>Model</label>
            <input type="text" id="model" placeholder="(provider default)">
          </div>
          <div class="field">
            <label>Base URL</label>
            <input type="text" id="base_url" placeholder="https://...">
          </div>
          <div class="field">
            <label>Permission Mode</label>
            <select id="permission_mode">
              <option value="ask">Ask</option>
              <option value="read_only">Read Only</option>
              <option value="workspace_write">Workspace Write</option>
              <option value="danger_full_access">Full Access</option>
            </select>
          </div>
          <div class="field">
            <label>Working Directory</label>
            <input type="text" id="working_dir" placeholder="(current directory)">
          </div>
          <div class="field">
            <label>Max Tokens</label>
            <input type="number" id="max_tokens" value="8192" min="256" step="256">
          </div>
          <div class="field">
            <label>Launch Mode</label>
            <select id="launch_mode">
              <option value="repl">REPL</option>
              <option value="webchat">WebChat</option>
              <option value="telegram">Telegram</option>
              <option value="rpc">RPC</option>
            </select>
          </div>
          <div class="field">
            <label>Port (WebChat)</label>
            <input type="number" id="port" value="8080" min="1024">
          </div>
        </div>
      </div>

      <!-- Skills -->
      <div class="card collapsed" id="card-skills">
        <div class="card-header" onclick="toggleCard('skills')">
          <span>Skills</span>
          <span class="card-chevron">&#9660;</span>
        </div>
        <div class="card-body">
          <div class="field">
            <label>Skills Directories (comma-separated)</label>
            <input type="text" id="skills_dir" placeholder="/path/to/skills,/another/path">
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Enable Skill CRUD</span>
            <label class="toggle"><input type="checkbox" id="skills_manage"><span class="slider"></span></label>
          </div>
        </div>
      </div>

      <!-- Memory -->
      <div class="card collapsed" id="card-memory">
        <div class="card-header" onclick="toggleCard('memory')">
          <span>Memory</span>
          <span class="card-chevron">&#9660;</span>
        </div>
        <div class="card-body">
          <div class="field">
            <label>Memory Directory</label>
            <input type="text" id="memory_dir" placeholder="~/.redclaw/memory">
          </div>
          <div class="toggle-row">
            <span class="toggle-label">LLM Compaction</span>
            <label class="toggle"><input type="checkbox" id="compact_llm"><span class="slider"></span></label>
          </div>
        </div>
      </div>

      <!-- Assistant / Persona -->
      <div class="card collapsed" id="card-assistant">
        <div class="card-header" onclick="toggleCard('assistant')">
          <span>Assistant / Persona</span>
          <span class="card-chevron">&#9660;</span>
        </div>
        <div class="card-body">
          <div class="toggle-row">
            <span class="toggle-label">Assistant Mode</span>
            <label class="toggle"><input type="checkbox" id="assistant"><span class="slider"></span></label>
          </div>
          <div class="field" style="margin-top:8px">
            <label>Persona Name</label>
            <input type="text" id="persona_name" placeholder="e.g. Jarvis, Alfred, Friday">
          </div>
          <div class="field">
            <label>Timezone</label>
            <input type="text" id="timezone" placeholder="UTC" value="UTC">
          </div>
          <div class="field">
            <label>Morning Briefing Time</label>
            <input type="text" id="briefing_time" placeholder="07:30" value="07:30">
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Daily Briefing</span>
            <label class="toggle"><input type="checkbox" id="briefing_enabled" checked><span class="slider"></span></label>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Weather in Briefing</span>
            <label class="toggle"><input type="checkbox" id="briefing_weather" checked><span class="slider"></span></label>
          </div>
          <div class="field">
            <label>Weather Location</label>
            <input type="text" id="weather_location" placeholder="e.g. New York, London, Tokyo">
          </div>
          <div class="toggle-row">
            <span class="toggle-label">News in Briefing</span>
            <label class="toggle"><input type="checkbox" id="briefing_news" checked><span class="slider"></span></label>
          </div>
          <div class="toggle-row">
            <span class="toggle-label">Tasks in Briefing</span>
            <label class="toggle"><input type="checkbox" id="briefing_tasks" checked><span class="slider"></span></label>
          </div>
          <div class="field">
            <label>News Topics (comma-separated)</label>
            <input type="text" id="news_topics" placeholder="tech, science, AI" value="tech">
          </div>
        </div>
      </div>

      <!-- Advanced -->
      <div class="card collapsed" id="card-advanced">
        <div class="card-header" onclick="toggleCard('advanced')">
          <span>Advanced</span>
          <span class="card-chevron">&#9660;</span>
        </div>
        <div class="card-body">
          <div class="toggle-row">
            <span class="toggle-label">Enable Subagents</span>
            <label class="toggle"><input type="checkbox" id="subagent"><span class="slider"></span></label>
          </div>
          <div class="toggle-row" style="margin-top:8px">
            <span class="toggle-label">Verbose Logging</span>
            <label class="toggle"><input type="checkbox" id="verbose"><span class="slider"></span></label>
          </div>
        </div>
      </div>

      <!-- MCP -->
      <div class="card collapsed" id="card-mcp">
        <div class="card-header" onclick="toggleCard('mcp')">
          <span>MCP / Voice</span>
          <span class="card-chevron">&#9660;</span>
        </div>
        <div class="card-body">
          <div class="field">
            <label>MCP Servers (one per line)</label>
            <textarea id="mcp_servers" rows="3" placeholder="http://localhost:8006/sse&#10;http://localhost:8007/sse"></textarea>
          </div>
          <div class="field">
            <label>TTS URL</label>
            <input type="text" id="tts_url" placeholder="http://...">
          </div>
          <div class="field">
            <label>STT URL</label>
            <input type="text" id="stt_url" placeholder="http://...">
          </div>
        </div>
      </div>

      <!-- Web -->
      <div class="card collapsed" id="card-web">
        <div class="card-header" onclick="toggleCard('web')">
          <span>Web</span>
          <span class="card-chevron">&#9660;</span>
        </div>
        <div class="card-body">
          <div class="field">
            <label>Search URL (SearXNG)</label>
            <input type="text" id="search_url" placeholder="http://localhost:8888">
          </div>
          <div class="field">
            <label>Reader URL</label>
            <input type="text" id="reader_url" placeholder="http://localhost:8003/sse">
          </div>
        </div>
      </div>

      <!-- Telegram -->
      <div class="card collapsed" id="card-telegram">
        <div class="card-header" onclick="toggleCard('telegram')">
          <span>Telegram</span>
          <span class="card-chevron">&#9660;</span>
        </div>
        <div class="card-body">
          <div class="field">
            <label>Bot Token</label>
            <input type="password" id="telegram_token" placeholder="123456:ABC...">
          </div>
          <div class="field">
            <label>User ID</label>
            <input type="number" id="telegram_user_id" placeholder="123456789">
          </div>
        </div>
      </div>

    </div>
  </aside>

  <!-- Main panel -->
  <main class="main">
    <div class="status-bar">
      <div class="status-dot" id="statusDot"></div>
      <div class="status-info" id="statusInfo">Idle</div>
      <div class="btn-group">
        <button class="btn btn-primary" id="btnStart" onclick="startProcess()">&#9654; Start</button>
        <button class="btn btn-danger" id="btnStop" onclick="stopProcess()" disabled>&#9632; Stop</button>
        <button class="btn btn-sm" onclick="clearLogs()" title="Clear logs">&#128465;</button>
      </div>
    </div>
    <div class="terminal-wrap">
      <div class="terminal" id="terminal">
        <div class="empty-state" id="emptyState">
          <div class="icon">&#128187;</div>
          <p>Configure RedClaw and press Start</p>
        </div>
      </div>
    </div>
  </main>

  <!-- PIN Modal -->
  <div class="modal-overlay" id="pinModal">
    <div class="modal">
      <h3 id="pinTitle">Enter PIN</h3>
      <div class="pin-inputs">
        <input type="password" maxlength="1" class="pin-digit" data-idx="0" autofocus>
        <input type="password" maxlength="1" class="pin-digit" data-idx="1">
        <input type="password" maxlength="1" class="pin-digit" data-idx="2">
        <input type="password" maxlength="1" class="pin-digit" data-idx="3">
      </div>
      <div class="error-msg" id="pinError"></div>
      <div class="btn-row">
        <button class="btn" onclick="closePinModal()">Cancel</button>
        <button class="btn btn-primary" id="pinConfirm" onclick="confirmPin()">Confirm</button>
      </div>
    </div>
  </div>
</div>

<script>
// --- Card toggling ---
function toggleCard(id) {
  document.getElementById('card-' + id).classList.toggle('collapsed');
}

// --- Gather config from form ---
function getConfig() {
  const v = id => document.getElementById(id)?.value?.trim() || '';
  const b = id => document.getElementById(id)?.checked || false;
  return {
    provider: v('provider'),
    model: v('model'),
    base_url: v('base_url'),
    permission_mode: v('permission_mode'),
    working_dir: v('working_dir'),
    max_tokens: parseInt(v('max_tokens')) || 8192,
    launch_mode: v('launch_mode'),
    port: parseInt(v('port')) || 8080,
    skills_dir: v('skills_dir'),
    skills_manage: b('skills_manage'),
    memory_dir: v('memory_dir'),
    compact_llm: b('compact_llm'),
    subagent: b('subagent'),
    verbose: b('verbose'),
    mcp_servers: document.getElementById('mcp_servers')?.value || '',
    tts_url: v('tts_url'),
    stt_url: v('stt_url'),
    search_url: v('search_url'),
    reader_url: v('reader_url'),
    telegram_token: v('telegram_token'),
    telegram_user_id: v('telegram_user_id'),
    assistant: b('assistant'),
  };
}

function setConfig(c) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ''; };
  const chk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
  set('provider', c.provider);
  set('model', c.model);
  set('base_url', c.base_url);
  set('permission_mode', c.permission_mode);
  set('working_dir', c.working_dir);
  set('max_tokens', c.max_tokens);
  set('launch_mode', c.launch_mode);
  set('port', c.port);
  set('skills_dir', c.skills_dir);
  chk('skills_manage', c.skills_manage);
  set('memory_dir', c.memory_dir);
  chk('compact_llm', c.compact_llm);
  chk('subagent', c.subagent);
  chk('verbose', c.verbose);
  chk('assistant', c.assistant);
  set('mcp_servers', c.mcp_servers);
  set('tts_url', c.tts_url);
  set('stt_url', c.stt_url);
  set('search_url', c.search_url);
  set('reader_url', c.reader_url);
  set('telegram_token', c.telegram_token);
  set('telegram_user_id', c.telegram_user_id);
}

// --- UI state ---
let eventSource = null;
let _pinAction = null; // 'save' or 'load'

function setRunning(running, pid) {
  const dot = document.getElementById('statusDot');
  const info = document.getElementById('statusInfo');
  const btnStart = document.getElementById('btnStart');
  const btnStop = document.getElementById('btnStop');

  dot.classList.toggle('running', running);
  if (running) {
    info.innerHTML = 'Running <span>(PID ' + pid + ')</span>';
    btnStart.disabled = true;
    btnStop.disabled = false;
  } else {
    info.textContent = 'Idle';
    btnStart.disabled = false;
    btnStop.disabled = true;
  }
}

// --- Terminal output ---
function appendLine(text) {
  const el = document.getElementById('emptyState');
  if (el) el.remove();

  const term = document.getElementById('terminal');
  const div = document.createElement('div');
  div.className = 'log-line';

  if (/\berror\b/i.test(text)) div.classList.add('error');
  else if (/\bwarn/i.test(text)) div.classList.add('warn');
  else if (/\bsuccess|\bstarted|\bconnected/i.test(text)) div.classList.add('success');
  else if (/redclaw/i.test(text)) div.classList.add('accent');

  div.textContent = text;
  term.appendChild(div);
  term.scrollTop = term.scrollHeight;
}

// --- RedClaw API ---
async function startProcess() {
  const res = await fetch('/api/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(getConfig()),
  });
  const data = await res.json();
  if (data.error) { appendLine('ERROR: ' + data.error); return; }
  setRunning(data.running, data.pid);
  connectSSE();
}

async function stopProcess() {
  await fetch('/api/stop', {method: 'POST'});
  setRunning(false, null);
}

async function clearLogs() {
  await fetch('/api/logs/clear', {method: 'POST'});
  document.getElementById('terminal').innerHTML = '';
}

// --- Server management ---
async function refreshServers() {
  const res = await fetch('/api/servers');
  const servers = await res.json();
  const container = document.getElementById('serverRows');
  container.innerHTML = '';
  for (const [name, info] of Object.entries(servers)) {
    const row = document.createElement('div');
    row.className = 'server-row';
    row.innerHTML = `
      <div class="server-dot ${info.running ? 'on' : ''}"></div>
      <span class="server-name">${name}</span>
      <span class="server-port">:${info.port}</span>
      <button class="btn btn-sm ${info.running ? 'btn-danger' : 'btn-primary'}"
        onclick="${info.running ? `stopServer('${name}')` : `startServer('${name}')`}">
        ${info.running ? 'Stop' : 'Start'}
      </button>`;
    container.appendChild(row);
  }
}

async function startServer(name) {
  await fetch('/api/servers/' + encodeURIComponent(name) + '/start', {method: 'POST'});
  refreshServers();
}

async function stopServer(name) {
  await fetch('/api/servers/' + encodeURIComponent(name) + '/stop', {method: 'POST'});
  refreshServers();
}

async function startAllServers() {
  await fetch('/api/servers/start-all', {method: 'POST'});
  refreshServers();
}

async function stopAllServers() {
  await fetch('/api/servers/stop-all', {method: 'POST'});
  refreshServers();
}

// --- Config save/load with PIN ---
function showPinModal(action) {
  _pinAction = action;
  document.getElementById('pinTitle').textContent = action === 'save' ? 'Set PIN to Save' : 'Enter PIN to Load';
  document.getElementById('pinError').textContent = '';
  document.querySelectorAll('.pin-digit').forEach(el => el.value = '');
  document.getElementById('pinModal').classList.add('open');
  setTimeout(() => document.querySelector('.pin-digit').focus(), 50);
}

function closePinModal() {
  document.getElementById('pinModal').classList.remove('open');
  _pinAction = null;
}

// Auto-advance PIN digits
document.addEventListener('input', (e) => {
  if (!e.target.classList.contains('pin-digit')) return;
  if (e.target.value.length === 1) {
    const next = e.target.nextElementSibling;
    if (next && next.classList.contains('pin-digit')) next.focus();
  }
});

// Backspace goes to previous
document.addEventListener('keydown', (e) => {
  if (!e.target.classList.contains('pin-digit')) return;
  if (e.key === 'Backspace' && !e.target.value) {
    const prev = e.target.previousElementSibling;
    if (prev && prev.classList.contains('pin-digit')) { prev.focus(); prev.value = ''; }
  }
  if (e.key === 'Enter') confirmPin();
});

async function confirmPin() {
  const digits = document.querySelectorAll('.pin-digit');
  const pin = Array.from(digits).map(d => d.value).join('');
  if (pin.length !== 4) {
    document.getElementById('pinError').textContent = 'Enter all 4 digits';
    return;
  }

  if (_pinAction === 'save') {
    const res = await fetch('/api/config/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pin, ...getConfig()}),
    });
    const data = await res.json();
    if (data.error) { document.getElementById('pinError').textContent = data.error; return; }
    closePinModal();
  } else if (_pinAction === 'load') {
    const res = await fetch('/api/config/load', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pin}),
    });
    const data = await res.json();
    if (data.error) { document.getElementById('pinError').textContent = data.error; return; }
    if (data.config) setConfig(data.config);
    closePinModal();
  }
}

// --- SSE ---
function connectSSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource('/api/logs');
  eventSource.onmessage = (e) => {
    try { appendLine(JSON.parse(e.data)); } catch {}
  };
  eventSource.onerror = () => { eventSource.close(); eventSource = null; };
}

// --- Init ---
(async function init() {
  const snap = await fetch('/api/logs/snapshot').then(r => r.json());
  if (snap.logs && snap.logs.length) snap.logs.forEach(l => appendLine(l));
  const st = await fetch('/api/status').then(r => r.json());
  setRunning(st.running, st.pid);
  if (st.running) connectSSE();
  refreshServers();
  setInterval(refreshServers, 5000);
})();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_dashboard(port: int = 9090) -> None:
    """Launch the dashboard server (blocking)."""
    url = f"http://127.0.0.1:{port}"
    print(f"RedClaw Dashboard: {url}")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
