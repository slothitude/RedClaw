"""Auto-updater — checks GitHub releases for new versions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from rich.console import Console

console = Console()

REPO_API = "https://api.github.com/repos/slothitude/RedClaw/releases/latest"
CHECK_FILE = ".last_update_check"
CHECK_INTERVAL = 24 * 60 * 60  # 24 hours in seconds


def _install_dir() -> Path | None:
    """Get the exe's install directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return None


def _should_check() -> bool:
    """Only check once per 24 hours."""
    install = _install_dir()
    if install is None:
        return False
    marker = install / CHECK_FILE
    if marker.exists():
        try:
            last = float(marker.read_text().strip())
            if (__import__("time").time() - last) < CHECK_INTERVAL:
                return False
        except (ValueError, OSError):
            pass
    return True


def _mark_checked() -> None:
    """Write timestamp after a check."""
    install = _install_dir()
    if install is None:
        return
    marker = install / CHECK_FILE
    try:
        marker.write_text(str(__import__("time").time()))
    except OSError:
        pass


def get_latest_version() -> tuple[str, str] | None:
    """Hit GitHub API, return (version_tag, exe_download_url) or None."""
    try:
        req = urllib.request.Request(REPO_API, headers={"User-Agent": "RedClaw"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        tag = data.get("tag_name", "").lstrip("v")
        for asset in data.get("assets", []):
            if asset.get("name") == "redclaw.exe":
                return tag, asset["browser_download_url"]
    except Exception:
        pass
    return None


def check_for_update() -> None:
    """Check for update, prompt, download, and swap. Only runs on frozen exe."""
    if not getattr(sys, "frozen", False):
        return  # skip when running from source

    from redclaw import __version__

    if not _should_check():
        return

    result = get_latest_version()
    _mark_checked()

    if result is None:
        return  # can't reach API, silent

    latest_tag, download_url = result

    if latest_tag <= __version__:
        return  # up to date

    console.print(
        f"\n[yellow]Update available: v{latest_tag}[/] (current: v{__version__})"
    )

    try:
        answer = console.input("[bold]Download and install? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return

    if answer in ("n", "no"):
        console.print("[dim]Skipped update.[/]\n")
        return

    _do_update(download_url, latest_tag)


def _do_update(download_url: str, new_version: str) -> None:
    """Download new exe and perform atomic swap."""
    exe_path = Path(sys.executable)
    install_dir = exe_path.parent
    new_exe = install_dir / "redclaw_new.exe"

    # Download
    console.print(f"[dim]Downloading v{new_version}...[/]")
    try:
        urllib.request.urlretrieve(download_url, str(new_exe))
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/]\n")
        return

    size_mb = new_exe.stat().st_size / (1024 * 1024)
    if size_mb < 1:
        # Probably an error page, not an exe
        new_exe.unlink(missing_ok=True)
        console.print("[red]Downloaded file too small — update aborted.[/]\n")
        return

    console.print(f"[dim]Downloaded {size_mb:.1f} MB[/]")

    # Swap: spawn a cmd that waits for us to exit, then replaces the exe
    # ping -n 3 waits ~2 seconds
    swap_cmd = (
        f'ping -n 3 127.0.0.1 >nul 2>&1 & '
        f'move /y "{new_exe}" "{exe_path}" >nul 2>&1'
    )

    try:
        subprocess.Popen(
            f'cmd /c "{swap_cmd}"',
            shell=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        console.print(f"[red]Failed to start update: {e}[/]\n")
        new_exe.unlink(missing_ok=True)
        return

    console.print(f"[bold green]Update to v{new_version} installed.[/]")
    console.print("[dim]Restart RedClaw to use the new version.[/]\n")
    sys.exit(0)
