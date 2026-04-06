"""Bash tool — execute subprocess commands with timeout."""

from __future__ import annotations

import asyncio

MAX_OUTPUT_SIZE = 50_000  # 50KB truncation limit


def _truncate(text: str, limit: int = MAX_OUTPUT_SIZE) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more bytes]"


async def execute_bash(
    command: str,
    timeout: int = 120,
    cwd: str | None = None,
) -> str:
    """Execute a bash command and return stdout + stderr."""
    proc = None
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
        if proc is not None:
            proc.kill()
            await proc.wait()
        return f"[Timed out after {timeout}s]\nCommand: {command}"
    except (OSError, PermissionError) as e:
        return f"Error: Cannot execute command: {e}"

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    parts = []
    if out:
        parts.append(out)
    if err:
        parts.append(f"[stderr]\n{err}")
    if proc.returncode != 0:
        parts.append(f"[exit code: {proc.returncode}]")

    return _truncate("\n".join(parts)) if parts else "[no output]"
