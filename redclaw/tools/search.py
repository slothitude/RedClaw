"""Search tools — glob and grep."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path


def _resolve(path: str | None, cwd: str) -> Path:
    if path is None:
        return Path(cwd)
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd) / p
    return p.resolve()


async def execute_glob_search(
    pattern: str,
    path: str | None = None,
    cwd: str | None = None,
) -> str:
    """Search for files matching a glob pattern."""
    base = _resolve(path, cwd or str(Path.cwd()))
    matches = sorted(str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file())
    if not matches:
        return f"No files matching '{pattern}' in {base}"
    header = f"Found {len(matches)} file(s) matching '{pattern}' in {base}:"
    return header + "\n" + "\n".join(matches[:200])


async def execute_grep_search(
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    ignore_case: bool = False,
    cwd: str | None = None,
) -> str:
    """Search file contents for a regex pattern."""
    base = _resolve(path, cwd or str(Path.cwd()))

    if base.is_file():
        files = [base]
    else:
        glob_pat = glob or "**/*"
        files = [p for p in base.glob(glob_pat) if p.is_file()]

    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: Invalid regex: {e}"

    results: list[str] = []
    for fpath in files[:500]:  # limit file scan
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if regex.search(line):
                        rel = str(fpath.relative_to(base)) if base.is_dir() else fpath.name
                        results.append(f"{rel}:{i}: {line.rstrip()}")
                        if len(results) >= 200:
                            break
        except (PermissionError, OSError):
            continue
        if len(results) >= 200:
            break

    if not results:
        return f"No matches for '{pattern}' in {base}"
    header = f"Found {len(results)} match(es) for '{pattern}' in {base}:"
    return header + "\n" + "\n".join(results)
