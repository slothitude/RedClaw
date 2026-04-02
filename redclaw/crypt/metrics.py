"""Crypt metrics — aggregate tracking and persistence."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CryptMetrics:
    """Aggregate stats for the crypt."""

    tasks_total: int = 0
    tasks_success: int = 0
    tasks_failed: int = 0
    by_type: dict[str, dict[str, int]] = field(default_factory=dict)
    recent_failures: list[dict[str, Any]] = field(default_factory=list)

    def record(self, subagent_type: str, success: bool, task_preview: str = "") -> None:
        """Record a subagent result."""
        self.tasks_total += 1
        if success:
            self.tasks_success += 1
        else:
            self.tasks_failed += 1
            if task_preview:
                self.recent_failures.append({"type": subagent_type, "task": task_preview[:200]})
                # Keep only last 50 failures
                self.recent_failures = self.recent_failures[-50:]

        # Per-type tracking
        if subagent_type not in self.by_type:
            self.by_type[subagent_type] = {"total": 0, "success": 0, "failed": 0}
        self.by_type[subagent_type]["total"] += 1
        if success:
            self.by_type[subagent_type]["success"] += 1
        else:
            self.by_type[subagent_type]["failed"] += 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CryptMetrics:
        return cls(
            tasks_total=data.get("tasks_total", 0),
            tasks_success=data.get("tasks_success", 0),
            tasks_failed=data.get("tasks_failed", 0),
            by_type=data.get("by_type", {}),
            recent_failures=data.get("recent_failures", []),
        )


def load_metrics(path: Path) -> CryptMetrics:
    """Load metrics from JSON file, or return empty if not found."""
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CryptMetrics.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to load crypt metrics: %s", e)
    return CryptMetrics()


def save_metrics(metrics: CryptMetrics, path: Path) -> None:
    """Persist metrics to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics.to_dict(), indent=2), encoding="utf-8")
