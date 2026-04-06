"""File operation tools — read, write, edit."""

from __future__ import annotations

import os
import tempfile
from difflib import get_close_matches
from pathlib import Path

MAX_FILE_SIZE = 10_000_000  # 10MB write limit
MAX_READ_LINES = 5000


def _resolve(path: str, cwd: str) -> Path:
    """Resolve a path relative to cwd, preventing traversal outside."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd) / p
    return p.resolve()


def _file_not_found_hint(resolved: Path) -> str:
    """Build a helpful error with directory listing and fuzzy matches."""
    msg = f"Error: File not found: {resolved}"
    parent = resolved.parent
    if parent.is_dir():
        try:
            siblings = sorted(f.name for f in parent.iterdir() if f.is_file())
        except OSError:
            return msg
        if not siblings:
            return msg + f"\nDirectory {parent} is empty."
        name = resolved.name
        matches = get_close_matches(name, siblings, n=3, cutoff=0.4)
        if matches:
            msg += f"\nSimilar files in {parent}: {', '.join(matches)}"
        elif len(siblings) <= 15:
            msg += f"\nFiles in {parent}: {', '.join(siblings)}"
        else:
            msg += f"\n{len(siblings)} files in {parent}, e.g.: {', '.join(siblings[:10])}, ..."
    return msg


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
        return _file_not_found_hint(resolved)
    except IsADirectoryError:
        return f"Error: Path is a directory: {resolved}"
    except PermissionError:
        return f"Error: Permission denied: {resolved}"

    # 1-indexed for display
    start = (offset or 0)
    max_end = start + (limit or MAX_READ_LINES)
    end = min(max_end, start + MAX_READ_LINES) if limit is None else max_end
    end = min(end, len(lines))

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
    """Write content to a file atomically, creating directories if needed."""
    if len(content) > MAX_FILE_SIZE:
        return f"Error: Content exceeds {MAX_FILE_SIZE:,} byte limit ({len(content):,} bytes)"
    resolved = _resolve(path, cwd or str(Path.cwd()))
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(resolved.parent), prefix=".redclaw_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, resolved)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
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
        return _file_not_found_hint(resolved)

    count = content.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {resolved}"
    if count > 1:
        return f"Error: old_string found {count} times in {resolved}. Provide more context to make it unique."

    new_content = content.replace(old_string, new_string, 1)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(resolved.parent), prefix=".redclaw_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, resolved)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return f"Replaced 1 occurrence in {resolved}"
