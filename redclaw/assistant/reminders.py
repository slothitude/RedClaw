"""Reminder management with JSON persistence and recurrence support."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_REMINDERS_PATH = Path.home() / ".redclaw" / "assistant" / "reminders.json"


@dataclass
class Reminder:
    """A single reminder."""
    id: str = ""
    text: str = ""
    trigger_at: str = ""  # ISO 8601
    recurrence: str = ""  # daily, weekly, monthly, weekdays, or ""
    delivered: bool = False
    task_id: str = ""  # optional link to a task
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:8]
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


class ReminderStore:
    """Persistent reminder store backed by JSON file."""

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_REMINDERS_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._reminders: dict[str, Reminder] = {}
        self._load()

    def _load(self) -> None:
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for item in data:
                    r = Reminder(**{k: v for k, v in item.items() if k in Reminder.__dataclass_fields__})
                    self._reminders[r.id] = r
            except Exception as e:
                logger.warning(f"Failed to load reminders: {e}")

    def _save(self) -> None:
        data = [asdict(r) for r in self._reminders.values()]
        _atomic_write(self._path, json.dumps(data, indent=2, ensure_ascii=False))

    def add(
        self,
        text: str,
        trigger_at: str,
        recurrence: str = "",
        task_id: str = "",
    ) -> Reminder:
        """Add a new reminder and persist."""
        reminder = Reminder(
            text=text,
            trigger_at=trigger_at,
            recurrence=recurrence,
            task_id=task_id,
        )
        self._reminders[reminder.id] = reminder
        self._save()
        return reminder

    def delete(self, reminder_id: str) -> bool:
        """Delete a reminder by ID."""
        if reminder_id in self._reminders:
            del self._reminders[reminder_id]
            self._save()
            return True
        return False

    def get(self, reminder_id: str) -> Reminder | None:
        return self._reminders.get(reminder_id)

    def get_pending(self) -> list[Reminder]:
        """Get all undelivered reminders."""
        return [r for r in self._reminders.values() if not r.delivered]

    def get_due(self, now: datetime | None = None) -> list[Reminder]:
        """Get reminders that are due (trigger_at <= now)."""
        if now is None:
            now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        results = []
        for r in self._reminders.values():
            if r.delivered:
                continue
            try:
                trigger_dt = datetime.fromisoformat(r.trigger_at)
                if trigger_dt.timestamp() <= now_ts:
                    results.append(r)
            except (ValueError, TypeError):
                pass
        return results

    def mark_delivered(self, reminder_id: str) -> Reminder | None:
        """Mark a reminder as delivered. If recurring, compute next occurrence."""
        reminder = self._reminders.get(reminder_id)
        if reminder is None:
            return None

        if reminder.recurrence:
            next_time = self.get_next_occurrence(reminder)
            if next_time:
                reminder.trigger_at = next_time.isoformat()
                reminder.updated_at = datetime.now(timezone.utc).isoformat()
            else:
                reminder.delivered = True
        else:
            reminder.delivered = True

        self._save()
        return reminder

    @staticmethod
    def get_next_occurrence(reminder: Reminder) -> datetime | None:
        """Calculate the next trigger time for a recurring reminder."""
        try:
            trigger_dt = datetime.fromisoformat(reminder.trigger_at)
        except (ValueError, TypeError):
            return None

        rec = reminder.recurrence.lower()
        if rec == "daily":
            return trigger_dt + timedelta(days=1)
        elif rec == "weekly":
            return trigger_dt + timedelta(weeks=1)
        elif rec == "monthly":
            # Add ~30 days (simplified)
            return trigger_dt + timedelta(days=30)
        elif rec == "weekdays":
            next_dt = trigger_dt + timedelta(days=1)
            # Skip weekends (5=Saturday, 6=Sunday)
            while next_dt.weekday() >= 5:
                next_dt += timedelta(days=1)
            return next_dt
        return None


def _atomic_write(path: Path, content: str) -> None:
    """Write file atomically."""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".redclaw_", suffix=".json")
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
