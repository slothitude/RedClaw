"""Bash tool — execute subprocess commands with timeout."""

from __future__ import annotations

import asyncio
import subprocess


async def execute_bash(
    command: str,
    timeout: int = 120,
    cwd: str | None = None,
) -> str:
    """Execute a bash command and return stdout + stderr."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        return f"[Timed out after {timeout}s]\nCommand: {command}"

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    parts = []
    if out:
        parts.append(out)
    if err:
        parts.append(f"[stderr]\n{err}")
    if proc.returncode != 0:
        parts.append(f"[exit code: {proc.returncode}]")

    return "\n".join(parts) if parts else "[no output]"
