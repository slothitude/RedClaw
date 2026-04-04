"""Crypt manager — bloodline wisdom, entombment, dharma.

The Crypt accumulates lessons from subagent runs so future subagents inherit
accumulated wisdom about what works and what fails.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redclaw.crypt.extractor import extract_lessons
from redclaw.crypt.metrics import CryptMetrics, load_metrics, save_metrics

if TYPE_CHECKING:
    from redclaw.runtime.subagent import SubagentResult
    from redclaw.runtime.subagent_types import SubagentType

logger = logging.getLogger(__name__)


class Crypt:
    """Manages bloodline wisdom, entombment, and dharma."""

    def __init__(self, crypt_dir: Path | None = None, dna_manager: Any | None = None, dream_synthesizer: Any | None = None) -> None:
        self.crypt_dir = crypt_dir or Path.home() / ".redclaw" / "crypt"
        self.crypt_dir.mkdir(parents=True, exist_ok=True)

        # Ensure subdirectories exist
        self.bloodlines_dir = self.crypt_dir / "bloodlines"
        self.entombed_dir = self.crypt_dir / "entombed"
        self.bloodlines_dir.mkdir(exist_ok=True)
        self.entombed_dir.mkdir(exist_ok=True)

        # Load metrics
        self._metrics_path = self.crypt_dir / "metrics.json"
        self._metrics = load_metrics(self._metrics_path)

        # DNA manager (optional — used when AGI mode is active)
        self._dna_manager = dna_manager

        # Dream synthesizer (optional — triggered after entombment)
        self._dream_synthesizer = dream_synthesizer

    @property
    def metrics(self) -> CryptMetrics:
        return self._metrics

    # ── Bloodline wisdom ─────────────────────────────────────

    def load_bloodline_wisdom(self, subagent_type: SubagentType) -> str:
        """Load accumulated wisdom for a bloodline. Returns empty string if none."""
        path = self.bloodlines_dir / f"{subagent_type.value}.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def update_bloodline(self, subagent_type: SubagentType, lesson: str, category: str) -> None:
        """Append a lesson to the bloodline file under the given category.

        Deduplicates against existing lessons — skips if an identical or
        very similar lesson already exists.
        """
        path = self.bloodlines_dir / f"{subagent_type.value}.md"
        content = ""
        if path.is_file():
            content = path.read_text(encoding="utf-8")

        # Dedup: check if this lesson (or a near-duplicate) already exists
        lesson_lower = lesson.lower().strip()
        existing_lines = content.split("\n")
        for line in existing_lines:
            stripped = line.lstrip("- *").strip().lower()
            if stripped and (stripped == lesson_lower or stripped in lesson_lower or lesson_lower in stripped):
                logger.debug("Skipping duplicate lesson: %s", lesson[:80])
                return

        header = f"## {category}"
        lines = content.split("\n")

        # Find the category section
        found = False
        for i, line in enumerate(lines):
            if line.strip() == header:
                # Insert after the header
                lines.insert(i + 1, f"- {lesson}")
                found = True
                break

        if not found:
            # Add new section
            if content and not content.endswith("\n"):
                lines.append("")
            lines.append(header)
            lines.append(f"- {lesson}")

        self._atomic_write(path, "\n".join(lines))

    def _init_bloodline(self, subagent_type: SubagentType) -> None:
        """Initialize a bloodline file with the standard structure."""
        path = self.bloodlines_dir / f"{subagent_type.value}.md"
        if path.is_file():
            return
        content = (
            f"# {subagent_type.value.title()} Bloodline Wisdom\n"
            "\n"
            "## Successful Patterns\n"
            "\n"
            "## Warnings\n"
            "\n"
            "## Tool Insights\n"
        )
        self._atomic_write(path, content)

    # ── Dharma ────────────────────────────────────────────────

    def load_dharma(self) -> str:
        """Load the living dharma document."""
        path = self.crypt_dir / "dharma.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def update_dharma(self, lesson: str) -> None:
        """Append a lesson to the living dharma document."""
        path = self.crypt_dir / "dharma.md"
        content = ""
        if path.is_file():
            content = path.read_text(encoding="utf-8")

        if not content.strip():
            content = "# Dharma — Accumulated Patterns\n\n"

        # Append under a timestamp
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        content = content.rstrip() + f"\n- [{ts}] {lesson}\n"
        self._atomic_write(path, content)

    # ── Entombment ────────────────────────────────────────────

    def entomb(
        self,
        result: SubagentResult,
        task: str,
        subagent_type: SubagentType,
    ) -> None:
        """Record a completed subagent run. Saves record, updates bloodline and dharma."""
        sub_id = f"sub-{uuid.uuid4().hex[:8]}"

        # Extract lessons
        lessons = extract_lessons(result, task, subagent_type)

        # Build entombed record
        record: dict[str, Any] = {
            "id": sub_id,
            "task": task[:500],
            "type": subagent_type.value,
            "success": result.success,
            "output_preview": (result.output or "")[:300],
            "error": result.error,
            "tool_calls": result.tool_calls,
            "lessons": [{"text": l.text, "category": l.category} for l in lessons],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Save entombed record
        record_path = self.entombed_dir / f"{sub_id}.json"
        record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

        # Update bloodline with lessons
        self._init_bloodline(subagent_type)
        for lesson in lessons:
            self.update_bloodline(subagent_type, lesson.text, lesson.category)

        # Update dharma with cross-cutting lessons
        if result.success:
            self.update_dharma(f"[{subagent_type.value}] Success: {task[:100]}")
        else:
            self.update_dharma(f"[{subagent_type.value}] Failed: {task[:100]} — {result.error or 'unknown'}")

        # Update metrics
        self._metrics.record(subagent_type.value, result.success, task[:200])
        save_metrics(self._metrics, self._metrics_path)

        # Evolve DNA traits (if DNA manager is wired)
        if self._dna_manager:
            type_stats = self._metrics.by_type.get(subagent_type.value, {})
            total = type_stats.get("total", 1)
            success_count = type_stats.get("success", 0)
            success_rate = success_count / total if total > 0 else 0.5
            avg_tool_calls = result.tool_calls
            timeout_rate = 0.0
            if result.error and "timeout" in (result.error or "").lower():
                timeout_rate = 1.0
            self._dna_manager.evolve(subagent_type, success_rate, avg_tool_calls, timeout_rate)

        # Trigger dream synthesis if conditions met (background task)
        if self._dream_synthesizer:
            total_entombed = len(list(self.entombed_dir.glob("sub-*.json")))
            if self._dream_synthesizer.should_dream(total_entombed):
                try:
                    import asyncio
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._dream_synthesizer.dream(self))
                except RuntimeError:
                    # No running loop — try to run synchronously in a new one
                    logger.info("No running event loop, running dream synchronously")
                    try:
                        asyncio.run(self._dream_synthesizer.dream(self))
                    except Exception as e:
                        logger.warning("Synchronous dream failed: %s", e)

        logger.info("Entombed %s: success=%s type=%s", sub_id, result.success, subagent_type.value)

    # ── Cleanup ───────────────────────────────────────────────

    def prune_entombed(self, max_records: int = 500) -> int:
        """Remove oldest entombed records if exceeding max_records.

        Returns the number of records removed.
        """
        records = sorted(self.entombed_dir.glob("sub-*.json"), key=lambda p: p.stat().st_mtime)
        if len(records) <= max_records:
            return 0

        to_remove = records[:len(records) - max_records]
        for path in to_remove:
            path.unlink()
        return len(to_remove)

    # ── Immediate lesson injection ─────────────────────────────

    def append_bloodline_lesson(self, subagent_type: SubagentType, lesson: str, category: str) -> None:
        """Append a lesson to the bloodline immediately, without waiting for dream synthesis.

        Used between SWE-bench instances so instance N+1 can learn from instance N.
        Deduplicates against existing entries.
        """
        self._init_bloodline(subagent_type)
        self.update_bloodline(subagent_type, lesson, category)

    # ── Utilities ─────────────────────────────────────────────

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write file atomically."""
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".crypt_", suffix=".md"
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
