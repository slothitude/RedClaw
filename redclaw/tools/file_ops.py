"""File operation tools — read, write, edit."""

from __future__ import annotations

import os
from pathlib import Path


def _resolve(path: str, cwd: str) -> Path:
    """Resolve a path relative to cwd, preventing traversal outside."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd) / p
    return p.resolve()


async def execute_read_file(
    path: str,
    offset: int | None = None,
    limit: int | None = None,
    cwd: str | None = None,
) -> str:
    """Read file contents, optionally a line range."""
    resolved = _resolve(path, cwd or str(Path.cwd()))
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return f"Error: File not found: {resolved}"
    except IsADirectoryError:
        return f"Error: Path is a directory: {resolved}"
    except PermissionError:
        return f"Error: Permission denied: {resolved}"

    # 1-indexed for display
    start = (offset or 0)
    end = start + (limit or len(lines))

    selected = lines[start:end]
    numbered = []
    for i, line in enumerate(selected, start=start + 1):
        numbered.append(f"{i:6d}\t{line.rstrip()}")

    header = f"File: {resolved} ({len(lines)} lines)"
    if offset or limit:
        header += f" [lines {start + 1}-{min(end, len(lines))}]"
    return header + "\n" + "\n".join(numbered)


async def execute_write_file(
    path: str,
    content: str,
    cwd: str | None = None,
) -> str:
    """Write content to a file, creating directories if needed."""
    resolved = _resolve(path, cwd or str(Path.cwd()))
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Wrote {len(content)} bytes to {resolved}"


async def execute_edit_file(
    path: str,
    old_string: str,
    new_string: str,
    cwd: str | None = None,
) -> str:
    """Edit a file by replacing old_string with new_string."""
    resolved = _resolve(path, cwd or str(Path.cwd()))
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return f"Error: File not found: {resolved}"

    count = content.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {resolved}"
    if count > 1:
        return f"Error: old_string found {count} times in {resolved}. Provide more context to make it unique."

    new_content = content.replace(old_string, new_string, 1)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write(new_content)

    return f"Replaced 1 occurrence in {resolved}"
