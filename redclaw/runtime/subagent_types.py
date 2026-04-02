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

SUBAGENT_PROMPTS: dict[SubagentType, str] = {
    SubagentType.CODER: _CODER_PROMPT,
    SubagentType.SEARCHER: _SEARCHER_PROMPT,
    SubagentType.GENERAL: _GENERAL_PROMPT,
}


# ── Type-specific toolset names ─────────────────────────────

SUBAGENT_TOOLSETS: dict[SubagentType, list[str]] = {
    SubagentType.CODER: ["core", "shell"],
    SubagentType.SEARCHER: ["core", "web"],
    SubagentType.GENERAL: ["core", "shell", "web"],
}


def get_subagent_prompt(subagent_type: SubagentType) -> str:
    """Get the system prompt for a subagent type."""
    return SUBAGENT_PROMPTS.get(subagent_type, _GENERAL_PROMPT)


def get_subagent_toolset_names(subagent_type: SubagentType) -> list[str]:
    """Get the toolset names for a subagent type."""
    return SUBAGENT_TOOLSETS.get(subagent_type, ["core", "shell", "web"])
