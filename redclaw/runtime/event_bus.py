"""Event Bus — in-memory publish/subscribe for AGI coordination.

Provides loose coupling between AGI components. Events are published
by various subsystems and consumed by subscribers (KarmaObserver, EventLogger).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


# ── Event types ──────────────────────────────────────────────

EVENT_GOAL_CREATED = "goal_created"
EVENT_GOAL_PROGRESS = "goal_progress"
EVENT_GOAL_COMPLETED = "goal_completed"
EVENT_SUBAGENT_SPAWNED = "subagent_spawned"
EVENT_SUBAGENT_COMPLETED = "subagent_completed"
EVENT_DREAM_COMPLETED = "dream_completed"
EVENT_KARMA_ALERT = "karma_alert"
EVENT_SIM_CREATED = "sim_created"
EVENT_SIM_TICK_MILESTONE = "sim_tick_milestone"
EVENT_SIM_STABILITY_CHANGED = "sim_stability_changed"



@dataclass
class AGIEvent:
    """A single event in the AGI system."""
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ── Subscriber type ─────────────────────────────────────────

Subscriber = Callable[[AGIEvent], Awaitable[None]]


class EventBus:
    """Simple in-memory pub/sub for AGI events."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._history: list[AGIEvent] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        """Add an async subscriber."""
        self._subscribers.append(subscriber)

    async def publish(self, event: AGIEvent) -> None:
        """Publish an event to all subscribers."""
        self._history.append(event)
        # Keep last 100 events in memory
        if len(self._history) > 100:
            self._history = self._history[-100:]

        for sub in self._subscribers:
            try:
                await sub(event)
            except Exception as e:
                logger.warning("Event subscriber error: %s", e)

    async def subscribe_and_wait(
        self,
        event_type: str,
        timeout: float = 30.0,
    ) -> AGIEvent | None:
        """Wait for a specific event type."""
        future: asyncio.Future[AGIEvent] = asyncio.get_event_loop().create_future()

        async def _waiter(event: AGIEvent) -> None:
            if event.type == event_type and not future.done():
                future.set_result(event)

        self._subscribers.append(_waiter)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._subscribers.remove(_waiter)

    @property
    def history(self) -> list[AGIEvent]:
        return list(self._history)


# ── Event Logger ─────────────────────────────────────────────

_MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB


class EventLogger:
    """Subscriber that appends significant events to JSONL file."""

    _LOGGED_TYPES = {
        EVENT_GOAL_CREATED, EVENT_GOAL_COMPLETED,
        EVENT_SUBAGENT_COMPLETED, EVENT_DREAM_COMPLETED,
        EVENT_KARMA_ALERT,
        EVENT_SIM_CREATED, EVENT_SIM_TICK_MILESTONE, EVENT_SIM_STABILITY_CHANGED,
    }

    def __init__(self, log_dir: Path | None = None) -> None:
        self._path = (log_dir or Path.home() / ".redclaw" / "agi") / "events.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def __call__(self, event: AGIEvent) -> None:
        if event.type not in self._LOGGED_TYPES:
            return

        line = json.dumps({
            "type": event.type,
            "data": event.data,
            "timestamp": event.timestamp,
            "source": event.source,
        })

        # Prune if too large
        if self._path.is_file() and self._path.stat().st_size > _MAX_LOG_SIZE:
            self._prune()

        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _prune(self) -> None:
        """Keep only the last 1000 events."""
        try:
            lines = self._path.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) > 1000:
                keep = lines[-1000:]
                self._path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass
