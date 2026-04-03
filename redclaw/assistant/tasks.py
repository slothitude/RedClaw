"""Task management with JSON persistence."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

DEFAULT_TASKS_PATH = Path.home() / ".redclaw" / "assistant" / "tasks.json"


@dataclass
class Task:
    """A single task."""
    id: str = ""
    title: str = ""
    description: str = ""
    status: str = "pending"  # pending, in_progress, done, cancelled
    priority: str = "medium"  # low, medium, high, urgent
    due: str = ""  # ISO 8601
    tags: list[str] = field(default_factory=list)
    recurrence: str = ""  # daily, weekly, monthly, or ""
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


class TaskStore:
    """Persistent task store backed by JSON file."""

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_TASKS_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, Task] = {}
        self._load()

    def _load(self) -> None:
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for item in data:
                    t = Task(**{k: v for k, v in item.items() if k in Task.__dataclass_fields__})
                    self._tasks[t.id] = t
            except Exception as e:
                logger.warning(f"Failed to load tasks: {e}")

    def _save(self) -> None:
        data = [asdict(t) for t in self._tasks.values()]
        _atomic_write(self._path, json.dumps(data, indent=2, ensure_ascii=False))

    def add(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        due: str = "",
        tags: list[str] | None = None,
        recurrence: str = "",
    ) -> Task:
        """Add a new task and persist."""
        task = Task(
            title=title,
            description=description,
            priority=priority,
            due=due,
            tags=tags or [],
            recurrence=recurrence,
        )
        self._tasks[task.id] = task
        self._save()
        return task

    def update(self, task_id: str, **kwargs: object) -> Task | None:
        """Update a task's fields."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        for k, v in kwargs.items():
            if hasattr(task, k) and k not in ("id", "created_at"):
                setattr(task, k, v)
        task.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()
        return task

    def delete(self, task_id: str) -> bool:
        """Delete a task by ID."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save()
            return True
        return False

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        status: str | None = None,
        tag: str | None = None,
        due_before: str | None = None,
    ) -> list[Task]:
        """List tasks with optional filters."""
        results = list(self._tasks.values())
        if status:
            results = [t for t in results if t.status == status]
        if tag:
            results = [t for t in results if tag in t.tags]
        if due_before:
            results = [t for t in results if t.due and t.due <= due_before]
        # Sort by priority then due date
        priority_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
        results.sort(key=lambda t: (priority_order.get(t.priority, 2), t.due or "z"))
        return results

    def get_due_tasks(self, within_minutes: int = 60) -> list[Task]:
        """Get tasks due within N minutes from now."""
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() + within_minutes * 60
        results = []
        for t in self._tasks.values():
            if t.status not in ("pending", "in_progress") or not t.due:
                continue
            try:
                due_dt = datetime.fromisoformat(t.due)
                if due_dt.timestamp() <= cutoff:
                    results.append(t)
            except (ValueError, TypeError):
                pass
        return results

    def search(self, query: str) -> list[Task]:
        """Search tasks by title or description."""
        q = query.lower()
        return [
            t for t in self._tasks.values()
            if q in t.title.lower() or q in t.description.lower()
        ]


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
