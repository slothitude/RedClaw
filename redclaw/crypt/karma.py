"""Karma Observer — evaluates actions against SOUL principles.

Deterministic scoring via keyword matching (no LLM needed).
Publishes KARMA_ALERT when alignment drops below threshold.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

from redclaw.runtime.event_bus import AGIEvent, EVENT_KARMA_ALERT

logger = logging.getLogger(__name__)

# ── Scoring keywords ────────────────────────────────────────

_POSITIVE_KEYWORDS = {
    "learn", "understand", "verify", "check", "confirm", "accurate",
    "honest", "transparent", "align", "user", "request", "complete",
    "thorough", "careful", "persist", "success",
    "stable", "balanced", "coherent", "orbital", "equilibrium",
    "coordinated",
}

_NEGATIVE_KEYWORDS = {
    "skip", "ignore", "guess", "assume", "fabricate", "lie", "deceive",
    "bypass", "shortcut", "unsafe", "destructive", "overwrite", "delete",
    "force", "hack", "unverified",
}

# Alignment thresholds
_ALERT_THRESHOLD = 0.5
_ALERT_STREAK = 3
_MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB


@dataclass
class KarmaRecord:
    """A single karma evaluation."""
    action: str
    alignment_score: float  # 0.0 to 1.0
    principle_scores: dict[str, float]
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class KarmaObserver:
    """Subscribes to events and evaluates alignment against SOUL principles."""

    def __init__(self, soul_text: str, event_bus: Any = None) -> None:
        self._soul_text = soul_text.lower()
        self._event_bus = event_bus
        self._karma_path = Path.home() / ".redclaw" / "crypt" / "karma.jsonl"
        self._karma_path.parent.mkdir(parents=True, exist_ok=True)
        self._low_streak = 0

        # Extract principle names from SOUL text
        self._principles = self._extract_principles()

    def _extract_principles(self) -> list[str]:
        """Extract principle names from SOUL.md text."""
        principles = []
        for line in self._soul_text.split("\n"):
            line = line.strip()
            if ">" in line:
                # e.g. "LEARNING > PERFORMANCE"
                parts = line.split(">")
                if len(parts) >= 2:
                    name = parts[0].strip().strip("0123456789. ").lower()
                    if name:
                        principles.append(name)
        return principles or ["learning", "understanding", "honesty", "alignment", "persistence"]

    async def __call__(self, event: AGIEvent) -> None:
        """Evaluate an event for alignment."""
        action = self._describe_action(event)
        if not action:
            return

        score = self._evaluate(action)
        record = KarmaRecord(
            action=action[:200],
            alignment_score=score["overall"],
            principle_scores=score["principles"],
        )

        # Persist
        self._log(record)

        # Check for alert condition
        if score["overall"] < _ALERT_THRESHOLD:
            self._low_streak += 1
            if self._low_streak >= _ALERT_STREAK and self._event_bus:
                await self._event_bus.publish(AGIEvent(
                    type=EVENT_KARMA_ALERT,
                    data={
                        "streak": self._low_streak,
                        "score": score["overall"],
                        "action": action[:100],
                    },
                    source="karma",
                ))
                logger.warning(
                    "KARMA ALERT: %d consecutive low-alignment actions (score=%.2f)",
                    self._low_streak, score["overall"],
                )
        else:
            self._low_streak = 0

    def _describe_action(self, event: AGIEvent) -> str:
        """Create a human-readable description of an event for evaluation."""
        data = event.data
        if event.type == "goal_created":
            return f"Created goal: {data.get('description', '')[:100]}"
        elif event.type == "goal_completed":
            return f"Completed goal: {data.get('description', '')[:100]}"
        elif event.type == "subagent_completed":
            status = "successfully" if data.get("success") else "unsuccessfully"
            return f"Subagent completed {status}: {data.get('task', '')[:100]}"
        elif event.type == "dream_completed":
            return f"Dream synthesis processed {data.get('records', 0)} records"
        elif event.type == "sim_created":
            return f"Created simulation entity: {data.get('entity_type', '')} (stable configuration)"
        elif event.type == "sim_tick_milestone":
            return f"Simulation tick milestone: {data.get('tick', 0)} ticks, stability={data.get('stability', 0):.2f}"
        elif event.type == "sim_stability_changed":
            return f"Simulation stability changed to {data.get('new_stability', 0):.2f}"
        return ""

    def _evaluate(self, action: str) -> dict[str, Any]:
        """Deterministic keyword-based alignment scoring."""
        action_lower = action.lower()
        pos_count = sum(1 for kw in _POSITIVE_KEYWORDS if kw in action_lower)
        neg_count = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in action_lower)

        total = pos_count + neg_count
        if total == 0:
            overall = 0.7  # Neutral = slightly positive
        else:
            overall = pos_count / total

        # Per-principle scores (simplified heuristic)
        principles: dict[str, float] = {}
        for p in self._principles:
            if p in action_lower:
                principles[p] = 0.8
            elif neg_count > 0:
                principles[p] = max(0.2, 0.7 - neg_count * 0.1)
            else:
                principles[p] = 0.7

        return {"overall": overall, "principles": principles}

    def _log(self, record: KarmaRecord) -> None:
        """Append a karma record to JSONL."""
        line = json.dumps(asdict(record))

        if self._karma_path.is_file() and self._karma_path.stat().st_size > _MAX_LOG_SIZE:
            self._prune()

        with open(self._karma_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _prune(self) -> None:
        """Keep only the last 1000 records."""
        try:
            lines = self._karma_path.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) > 1000:
                keep = lines[-1000:]
                self._karma_path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass
