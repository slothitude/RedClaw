"""Start all local MCP servers at once.

Launches TTS, STT, and Web Reader as subprocesses.
Handles graceful shutdown with Ctrl+C.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Python 3.11 venv for Coqui TTS (not compatible with 3.13)
_VENV311_PYTHON = Path(__file__).parent.parent / ".venv311" / "Scripts" / "python.exe"

SERVERS = [
    {
        "name": "TTS",
        "script": Path(__file__).parent / "tts_server.py",
        "port": 8006,
        "python": str(_VENV311_PYTHON) if _VENV311_PYTHON.exists() else sys.executable,
    },
    {
        "name": "STT",
        "script": Path(__file__).parent / "stt_server.py",
        "port": 8007,
    },
    {
        "name": "Web Reader",
        "script": Path(__file__).parent / "web_reader_server.py",
        "port": 8003,
    },
]


def main():
    parser = argparse.ArgumentParser(description="Start all RedClaw MCP servers")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    env = os.environ.copy()
    env["COQUI_TOS_AGREED"] = "1"  # Accept Coqui CPML for non-commercial use

    procs = []
    for srv in SERVERS:
        script = srv["script"]
        if not script.exists():
            logger.error(f"Server script not found: {script}")
            continue
        python = srv.get("python", sys.executable)
        cmd = [python, str(script), "--port", str(srv["port"])]
        if args.verbose:
            cmd.append("--verbose")
        logger.info(f"Starting {srv['name']} on port {srv['port']}...")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        procs.append({"name": srv["name"], "port": srv["port"], "proc": proc})
        logger.info(f"  {srv['name']} PID={proc.pid}")

    if not procs:
        logger.error("No servers started.")
        return 1

    print("\n" + "=" * 50)
    print("RedClaw Local MCP Servers")
    print("=" * 50)
    for p in procs:
        print(f"  {p['name']:12s}  http://localhost:{p['port']}/sse")
    print("=" * 50)
    print("Press Ctrl+C to stop all servers.\n")

    try:
        # Monitor processes
        while True:
            for p in procs:
                ret = p["proc"].poll()
                if ret is not None:
                    logger.warning(f"{p['name']} exited with code {ret}")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nShutting down all servers...")
    finally:
        for p in procs:
            proc = p["proc"]
            if proc.poll() is None:
                logger.info(f"Stopping {p['name']} (PID={proc.pid})...")
                if sys.platform == "win32":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print("All servers stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
