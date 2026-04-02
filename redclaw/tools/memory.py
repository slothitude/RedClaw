"""Persistent memory tool with frozen snapshot pattern.

- Loads MEMORY.md + USER.md at session start → frozen snapshot injected into system prompt
- Live mutations via tool calls persist to disk immediately
- Snapshot never changes mid-session (preserves prefix cache)
- Format: markdown with # Section headers as categories, bullet entries
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from redclaw.tools.content_scan import scan_for_injection, scan_for_exfiltration, scan_for_invisible_unicode

logger = logging.getLogger(__name__)


class MemoryManager:
    """Manages persistent memory with frozen snapshot pattern."""

    def __init__(self, memory_dir: str | None = None) -> None:
        self.memory_dir = Path(memory_dir) if memory_dir else Path.home() / ".redclaw" / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Load and freeze snapshot at init time
        self._snapshot = self._load_snapshot()

    @property
    def snapshot(self) -> str:
        """Frozen snapshot for system prompt injection (never changes mid-session)."""
        return self._snapshot

    def _load_snapshot(self) -> str:
        """Load MEMORY.md and USER.md into a frozen snapshot."""
        parts: list[str] = []

        memory_path = self.memory_dir / "MEMORY.md"
        if memory_path.is_file():
            content = memory_path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)

        user_path = self.memory_dir / "USER.md"
        if user_path.is_file():
            content = user_path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)

        return "\n\n".join(parts) if parts else ""

    def _memory_path(self) -> Path:
        return self.memory_dir / "MEMORY.md"

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write file atomically."""
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".redclaw_", suffix=".md"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _read_memory(self) -> str:
        path = self._memory_path()
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def _scan_content(self, content: str) -> list[str]:
        """Run security scans on content before storing."""
        warnings: list[str] = []
        warnings.extend(scan_for_injection(content))
        warnings.extend(scan_for_exfiltration(content))
        warnings.extend(scan_for_invisible_unicode(content))
        return warnings

    # ── Tool sub-operations ──────────────────────────────────

    async def recall(self, query: str = "") -> str:
        """Recall memories, optionally filtered by query."""
        content = self._read_memory()
        if not content:
            return "No memories stored yet."

        if not query:
            return content

        # Simple substring/keyword search across sections
        sections = re.split(r"(?=^# )", content, flags=re.MULTILINE)
        matches = []
        query_lower = query.lower()
        for section in sections:
            if query_lower in section.lower():
                matches.append(section.strip())

        if not matches:
            return f"No memories matching '{query}'."
        return "\n\n".join(matches)

    async def store(self, content: str, category: str = "General") -> str:
        """Store a new memory entry. Content is scanned for security issues first."""
        # Security scan
        warnings = self._scan_content(content)
        if warnings:
            return f"Refused to store — security concern detected:\n" + "\n".join(f"  - {w}" for w in warnings)

        current = self._read_memory()
        header = f"# {category}"

        # Find or create the category section
        sections = re.split(r"(?=^# )", current, flags=re.MULTILINE) if current.strip() else []
        found = False
        for i, section in enumerate(sections):
            if section.strip().startswith(header):
                # Append to existing section
                sections[i] = section.rstrip() + f"\n- {content}\n"
                found = True
                break

        if not found:
            sections.append(f"\n{header}\n- {content}\n")

        new_content = "".join(sections)
        self._atomic_write(self._memory_path(), new_content)
        return f"Stored memory in category '{category}'."

    async def search(self, query: str) -> str:
        """Search memories for a keyword or phrase."""
        return await self.recall(query)


# ── Tool execute function (registered with ToolExecutor) ─────

_memory_manager: MemoryManager | None = None


def get_memory_manager(memory_dir: str | None = None) -> MemoryManager:
    """Get or create the global MemoryManager."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager(memory_dir)
    return _memory_manager


async def execute_memory(
    action: str,
    content: str = "",
    category: str = "General",
    query: str = "",
    memory_dir: str | None = None,
) -> str:
    """Memory tool — store, recall, and search persistent memories.

    action: 'recall', 'store', 'search'
    """
    mgr = get_memory_manager(memory_dir)

    if action == "store":
        return await mgr.store(content, category)
    elif action == "recall":
        return await mgr.recall(query or content)
    elif action == "search":
        return await mgr.search(query or content)
    else:
        return f"Error: Unknown action '{action}'. Use recall, store, or search."
