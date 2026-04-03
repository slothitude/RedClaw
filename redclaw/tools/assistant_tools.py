"""Agent-facing tools for tasks, notes, and reminders."""

from __future__ import annotations

import json
from typing import Any

from redclaw.assistant.tasks import TaskStore
from redclaw.assistant.notes import NoteStore
from redclaw.assistant.reminders import ReminderStore


# ── Shared store instances (lazy) ─────────────────────────────

_tasks: TaskStore | None = None
_notes: NoteStore | None = None
_reminders: ReminderStore | None = None


def _get_tasks() -> TaskStore:
    global _tasks
    if _tasks is None:
        _tasks = TaskStore()
    return _tasks


def _get_notes() -> NoteStore:
    global _notes
    if _notes is None:
        _notes = NoteStore()
    return _notes


def _get_reminders() -> ReminderStore:
    global _reminders
    if _reminders is None:
        _reminders = ReminderStore()
    return _reminders


def set_stores(
    tasks: TaskStore | None = None,
    notes: NoteStore | None = None,
    reminders: ReminderStore | None = None,
) -> None:
    """Override the default stores (used by TelegramSession to share instances)."""
    global _tasks, _notes, _reminders
    if tasks is not None:
        _tasks = tasks
    if notes is not None:
        _notes = notes
    if reminders is not None:
        _reminders = reminders


# ── Task tool ─────────────────────────────────────────────────


async def execute_task(
    action: str = "list",
    title: str = "",
    task_id: str = "",
    description: str = "",
    priority: str = "medium",
    due: str = "",
    tags: str = "",
    status: str = "",
    query: str = "",
) -> str:
    """Task management tool.

    action: add, list, update, delete, search
    """
    store = _get_tasks()

    if action == "add":
        if not title:
            return "Error: title is required for add"
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        task = store.add(title=title, description=description, priority=priority, due=due, tags=tag_list)
        return json.dumps({"id": task.id, "title": task.title, "priority": task.priority, "due": task.due})

    elif action == "list":
        tasks = store.list_tasks(status=status or None, tag=query or None)
        if not tasks:
            return "No tasks found."
        lines = []
        for t in tasks:
            marker = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}.get(t.status, "[ ]")
            lines.append(f"{marker} {t.id} | {t.priority} | {t.title}" + (f" (due {t.due})" if t.due else ""))
        return "\n".join(lines)

    elif action == "update":
        if not task_id:
            return "Error: task_id is required for update"
        kwargs: dict[str, Any] = {}
        if title:
            kwargs["title"] = title
        if description:
            kwargs["description"] = description
        if priority:
            kwargs["priority"] = priority
        if due:
            kwargs["due"] = due
        if status:
            kwargs["status"] = status
        if tags:
            kwargs["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        result = store.update(task_id, **kwargs)
        if result is None:
            return f"Error: task '{task_id}' not found"
        return json.dumps({"id": result.id, "title": result.title, "status": result.status})

    elif action == "delete":
        if not task_id:
            return "Error: task_id is required for delete"
        if store.delete(task_id):
            return f"Task {task_id} deleted."
        return f"Error: task '{task_id}' not found"

    elif action == "search":
        if not query:
            return "Error: query is required for search"
        results = store.search(query)
        if not results:
            return f"No tasks matching '{query}'."
        lines = [f"- {t.id} | {t.title} [{t.status}]" for t in results]
        return "\n".join(lines)

    else:
        return f"Error: Unknown action '{action}'. Use: add, list, update, delete, search"


# ── Note tool ─────────────────────────────────────────────────


async def execute_note(
    action: str = "list",
    title: str = "",
    note_id: str = "",
    content: str = "",
    tags: str = "",
    source: str = "",
    query: str = "",
) -> str:
    """Note management tool.

    action: add, list, update, delete, search
    """
    store = _get_notes()

    if action == "add":
        if not title:
            return "Error: title is required for add"
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        note = store.add(title=title, content=content, tags=tag_list, source=source)
        return json.dumps({"id": note.id, "title": note.title})

    elif action == "list":
        notes = store.list_notes(tag=query or None)
        if not notes:
            return "No notes found."
        lines = [f"- {n.id} | {n.title}" + (f" [{', '.join(n.tags)}]" if n.tags else "") for n in notes]
        return "\n".join(lines)

    elif action == "update":
        if not note_id:
            return "Error: note_id is required for update"
        kwargs: dict[str, Any] = {}
        if title:
            kwargs["title"] = title
        if content:
            kwargs["content"] = content
        if tags:
            kwargs["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        if source:
            kwargs["source"] = source
        result = store.update(note_id, **kwargs)
        if result is None:
            return f"Error: note '{note_id}' not found"
        return json.dumps({"id": result.id, "title": result.title})

    elif action == "delete":
        if not note_id:
            return "Error: note_id is required for delete"
        if store.delete(note_id):
            return f"Note {note_id} deleted."
        return f"Error: note '{note_id}' not found"

    elif action == "search":
        if not query:
            return "Error: query is required for search"
        results = store.search(query)
        if not results:
            return f"No notes matching '{query}'."
        lines = [f"- {n.id} | {n.title}" for n in results]
        return "\n".join(lines)

    else:
        return f"Error: Unknown action '{action}'. Use: add, list, update, delete, search"


# ── Reminder tool ─────────────────────────────────────────────


async def execute_reminder(
    action: str = "list",
    text: str = "",
    trigger_at: str = "",
    recurrence: str = "",
    reminder_id: str = "",
) -> str:
    """Reminder management tool.

    action: add, list, delete
    """
    store = _get_reminders()

    if action == "add":
        if not text or not trigger_at:
            return "Error: text and trigger_at are required for add"
        reminder = store.add(text=text, trigger_at=trigger_at, recurrence=recurrence)
        return json.dumps({"id": reminder.id, "text": reminder.text, "trigger_at": reminder.trigger_at})

    elif action == "list":
        pending = store.get_pending()
        if not pending:
            return "No pending reminders."
        lines = [f"- {r.id} | {r.text} (at {r.trigger_at})" + (f" [{r.recurrence}]" if r.recurrence else "") for r in pending]
        return "\n".join(lines)

    elif action == "delete":
        if not reminder_id:
            return "Error: reminder_id is required for delete"
        if store.delete(reminder_id):
            return f"Reminder {reminder_id} deleted."
        return f"Error: reminder '{reminder_id}' not found"

    else:
        return f"Error: Unknown action '{action}'. Use: add, list, delete"
