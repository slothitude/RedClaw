"""Lightweight named toolsets with recursive include resolution."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Toolset:
    """A named set of tool names, optionally including other toolsets."""
    name: str
    tools: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)


# ── Built-in toolsets ────────────────────────────────────────

BUILTIN_TOOLSETS: dict[str, Toolset] = {
    "core": Toolset(
        name="core",
        tools=["read_file", "write_file", "edit_file", "glob_search", "grep_search"],
    ),
    "shell": Toolset(
        name="shell",
        tools=["bash"],
    ),
    "web": Toolset(
        name="web",
        tools=["web_search", "web_reader"],
    ),
    "memory": Toolset(
        name="memory",
        tools=["memory"],
    ),
    "skills": Toolset(
        name="skills",
        tools=["skills_list", "skill_view", "skill_manage"],
    ),
    "subagent": Toolset(
        name="subagent",
        tools=["subagent"],
    ),
    "full": Toolset(
        name="full",
        includes=["core", "shell", "web"],
    ),
    "readonly": Toolset(
        name="readonly",
        tools=["read_file", "glob_search", "grep_search"],
    ),
    "assistant": Toolset(
        name="assistant",
        tools=["task", "note", "reminder"],
    ),
    "knowledge": Toolset(
        name="knowledge",
        tools=["knowledge"],
    ),
    "agi": Toolset(
        name="agi",
        tools=["execute_goal"],
    ),
    "wiki": Toolset(
        name="wiki",
        tools=["wiki"],
    ),
    "simulator": Toolset(
        name="simulator",
        tools=["spawn_entity", "set_sim_parameter", "query_state", "apply_force"],
    ),
}


def resolve_toolset(
    name: str,
    toolsets: dict[str, Toolset] | None = None,
    _seen: set[str] | None = None,
) -> set[str]:
    """Resolve a toolset name to a flat set of tool names, recursing includes."""
    all_sets = {**BUILTIN_TOOLSETS, **(toolsets or {})}
    seen = _seen or set()
    if name in seen:
        return set()  # prevent cycles
    seen.add(name)

    ts = all_sets.get(name)
    if ts is None:
        logger.warning("Unknown toolset '%s'", name)
        return set()

    result = set(ts.tools)
    for inc in ts.includes:
        result |= resolve_toolset(inc, toolsets, seen)
    return result
