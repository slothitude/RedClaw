"""AGI Tools — agent-facing tools for goal management.

Registered when --agi mode is active. Lets the LLM inject goals,
list goals, check status, and cancel goals during normal conversation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def register_agi_tools(tools: Any, event_bus: Any = None) -> None:
    """Register AGI goal management tools."""
    from redclaw.api.types import PermissionLevel
    from redclaw.tools.registry import ToolSpec

    goals_path = Path.home() / ".redclaw" / "agi" / "goals.jsonl"

    tools.register_tool(ToolSpec(
        name="execute_goal",
        description=(
            "Manage autonomous goals. Actions: "
            "'add' — create a new goal with description and optional priority (1-10) and completion criteria, "
            "'list' — show all goals, "
            "'status' — show details for a specific goal (requires goal_id), "
            "'cancel' — park a goal so the executive won't pursue it (requires goal_id)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action: add, list, status, cancel",
                },
                "description": {
                    "type": "string",
                    "description": "Goal description (for 'add')",
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority 1-10, higher = more important (default: 5)",
                    "default": 5,
                },
                "completion_criteria": {
                    "type": "string",
                    "description": "How to determine the goal is complete (for 'add')",
                },
                "goal_id": {
                    "type": "string",
                    "description": "Goal ID (for 'status' and 'cancel')",
                },
            },
            "required": ["action"],
        },
        permission=PermissionLevel.WORKSPACE_WRITE,
        execute=lambda **kw: _execute_goal(path=goals_path, event_bus=event_bus, **kw),
    ))


def _execute_goal(
    action: str,
    path: Path = None,
    event_bus: Any = None,
    description: str = "",
    priority: int = 5,
    completion_criteria: str = "",
    goal_id: str = "",
    **kwargs: Any,
) -> str:
    """Execute goal management actions."""
    if path is None:
        path = Path.home() / ".redclaw" / "agi" / "goals.jsonl"

    path.parent.mkdir(parents=True, exist_ok=True)

    if action == "add":
        if not description:
            return "Error: description required for 'add' action."
        return _add_goal(path, description, priority, completion_criteria, event_bus)

    elif action == "list":
        return _list_goals(path)

    elif action == "status":
        if not goal_id:
            return "Error: goal_id required for 'status' action."
        return _goal_status(path, goal_id)

    elif action == "cancel":
        if not goal_id:
            return "Error: goal_id required for 'cancel' action."
        return _cancel_goal(path, goal_id)

    else:
        return f"Error: unknown action '{action}'. Use: add, list, status, cancel."


def _load_goals(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    goals = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        line = line.strip()
        if line:
            try:
                goals.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return goals


def _save_goals(goals: list[dict], path: Path) -> None:
    import tempfile
    import os
    lines = [json.dumps(g) for g in goals]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".goals_", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _add_goal(
    path: Path, description: str, priority: int, criteria: str, event_bus: Any,
) -> str:
    """Add a new goal."""
    import uuid
    from datetime import datetime, timezone

    goal = {
        "id": f"goal-{uuid.uuid4().hex[:8]}",
        "description": description[:500],
        "status": "pending",
        "priority": min(10, max(1, priority)),
        "completion_criteria": criteria[:500],
        "decomposed_steps": [],
        "progress": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    goals = _load_goals(path)
    goals.append(goal)
    _save_goals(goals, path)

    # Publish event
    if event_bus:
        import asyncio
        try:
            from redclaw.runtime.event_bus import AGIEvent, EVENT_GOAL_CREATED
            asyncio.create_task(event_bus.publish(AGIEvent(
                type=EVENT_GOAL_CREATED,
                data={"id": goal["id"], "description": description[:200]},
                source="tool",
            )))
        except RuntimeError:
            pass

    return f"Goal created: {goal['id']} — {description[:100]}"


def _list_goals(path: Path) -> str:
    """List all goals."""
    goals = _load_goals(path)
    if not goals:
        return "No goals in queue."

    lines = []
    for g in goals:
        status = g.get("status", "?")
        desc = g.get("description", "")[:80]
        pri = g.get("priority", 5)
        lines.append(f"[{status}] (pri={pri}) {g.get('id', '?')}: {desc}")
    return "\n".join(lines)


def _goal_status(path: Path, goal_id: str) -> str:
    """Show status of a specific goal."""
    goals = _load_goals(path)
    for g in goals:
        if g.get("id") == goal_id:
            lines = [
                f"ID: {g.get('id')}",
                f"Status: {g.get('status')}",
                f"Priority: {g.get('priority')}",
                f"Description: {g.get('description')}",
                f"Progress: {g.get('progress', 0):.0%}",
                f"Criteria: {g.get('completion_criteria', 'N/A')}",
                f"Steps: {len(g.get('decomposed_steps', []))}",
            ]
            for i, step in enumerate(g.get("decomposed_steps", [])):
                lines.append(f"  {i+1}. [{step.get('status', '?')}] {step.get('task', '')[:80]}")
            return "\n".join(lines)
    return f"Goal '{goal_id}' not found."


def _cancel_goal(path: Path, goal_id: str) -> str:
    """Park a goal."""
    goals = _load_goals(path)
    for g in goals:
        if g.get("id") == goal_id:
            g["status"] = "parked"
            _save_goals(goals, path)
            return f"Goal '{goal_id}' parked."
    return f"Goal '{goal_id}' not found."
