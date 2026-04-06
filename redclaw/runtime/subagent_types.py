"""Subagent types (bloodlines) — typed workers with tailored prompts and toolsets.

Each SubagentType maps to:
- A system prompt optimized for that task category
- A specific toolset restricting available tools
"""

from __future__ import annotations

from enum import Enum


class SubagentType(str, Enum):
    """Bloodline types for subagent workers."""
    CODER = "coder"
    SEARCHER = "searcher"
    GENERAL = "general"
    SIMULATOR = "simulator"


# ── Type-specific system prompts ─────────────────────────────

_CODER_PROMPT = (
    "You are a coding subagent. Focus on code changes. "
    "Read the entire file before editing. Prefer edit_file over write_file for existing files. "
    "Make minimal targeted changes. Verify your edits are correct. "
    "Return your final answer clearly."
)

_SEARCHER_PROMPT = (
    "You are a search subagent. Focus on finding information. "
    "Use glob_search and grep_search to locate files and code. "
    "Use web_search and web_reader for external information. "
    "Return comprehensive findings with file paths and line references."
)

_GENERAL_PROMPT = (
    "You are a subagent executing a specific subtask. "
    "Focus only on the assigned task. Be concise. "
    "Return your final answer clearly."
)

_SIMULATOR_PROMPT = (
    "You are a simulation subagent. Focus on spawning and tuning entities in a 2D physics world. "
    "Use spawn_entity to create particles, orbs, fields, and constraints. "
    "Use set_sim_parameter to adjust gravity, damping, and other physics settings. "
    "Use query_state to inspect entity positions and velocities. "
    "Use apply_force to nudge entities toward stable configurations. "
    "Aim for stable, balanced configurations with low average velocity. "
    "Return a summary of what you created and the resulting stability."
)

SUBAGENT_PROMPTS: dict[SubagentType, str] = {
    SubagentType.CODER: _CODER_PROMPT,
    SubagentType.SEARCHER: _SEARCHER_PROMPT,
    SubagentType.GENERAL: _GENERAL_PROMPT,
    SubagentType.SIMULATOR: _SIMULATOR_PROMPT,
}


# ── Type-specific toolset names ─────────────────────────────

SUBAGENT_TOOLSETS: dict[SubagentType, list[str]] = {
    SubagentType.CODER: ["core", "shell"],
    SubagentType.SEARCHER: ["core", "web"],
    SubagentType.GENERAL: ["core", "shell", "web"],
    SubagentType.SIMULATOR: ["core", "simulator"],
}


def get_subagent_prompt(subagent_type: SubagentType) -> str:
    """Get the system prompt for a subagent type."""
    return SUBAGENT_PROMPTS.get(subagent_type, _GENERAL_PROMPT)


def get_subagent_toolset_names(subagent_type: SubagentType) -> list[str]:
    """Get the toolset names for a subagent type."""
    return SUBAGENT_TOOLSETS.get(subagent_type, ["core", "shell", "web"])
