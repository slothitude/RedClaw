"""Lesson extractor — extract lessons from subagent results.

Uses pattern matching on error messages and tool call history, not LLM analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redclaw.runtime.subagent import SubagentResult
    from redclaw.runtime.subagent_types import SubagentType


@dataclass
class Lesson:
    """A lesson extracted from a subagent run."""
    text: str
    category: str  # "Successful Patterns", "Warnings", "Tool Insights"
    source_type: str


# ── Error pattern matching ──────────────────────────────────

_TIMEOUT_PATTERNS = ("timed out", "timeout", "TimeoutError")
_SYNTAX_PATTERNS = ("syntax error", "SyntaxError", "IndentationError")
_IMPORT_PATTERNS = ("import error", "ImportError", "ModuleNotFoundError", "no module named")
_FILE_NOT_FOUND = ("file not found", "No such file", "does not exist", "not found")
_PERMISSION_PATTERNS = ("permission denied", "PermissionError", "Access denied")
_TOOL_FAILURE = ("tool error", "bash error", "subprocess")


def _classify_failure(error: str) -> tuple[str, str]:
    """Classify a failure into a category and lesson text.

    Returns (category, lesson_text).
    """
    error_lower = error.lower()

    if any(p in error_lower for p in _TIMEOUT_PATTERNS):
        return "Warnings", "Tasks that timeout may be too broad — break into smaller subtasks"

    if any(p in error_lower for p in _SYNTAX_PATTERNS):
        return "Warnings", "Syntax errors often come from incomplete file reads — read the full file first"

    if any(p in error_lower for p in _IMPORT_PATTERNS):
        return "Warnings", "Import errors suggest missing dependencies — verify environment first"

    if any(p in error_lower for p in _FILE_NOT_FOUND):
        return "Warnings", "File not found errors suggest incorrect paths — use glob_search to verify"

    if any(p in error_lower for p in _PERMISSION_PATTERNS):
        return "Warnings", "Permission errors need explicit user authorization"

    if any(p in error_lower for p in _TOOL_FAILURE):
        return "Warnings", "Tool failures may indicate command issues — verify syntax and arguments"

    return "Warnings", f"Generic failure pattern: {error[:120]}"


def _classify_success(output: str, tool_calls: int) -> tuple[str, str]:
    """Classify a success into a lesson."""
    if tool_calls <= 2:
        return "Successful Patterns", "Simple tasks with few tool calls complete reliably"
    if tool_calls <= 5:
        return "Successful Patterns", "Moderate complexity tasks complete well within turn limits"
    return "Successful Patterns", "Complex tasks benefit from structured multi-step approaches"


def extract_lessons(
    result: SubagentResult,
    task: str,
    subagent_type: SubagentType,
) -> list[Lesson]:
    """Extract lessons from a completed subagent run.

    Returns a list of lessons to potentially store in the bloodline.
    """
    lessons: list[Lesson] = []
    type_name = subagent_type.value

    if result.success:
        category, text = _classify_success(result.output, result.tool_calls)
        lessons.append(Lesson(
            text=text,
            category=category,
            source_type=type_name,
        ))
    else:
        error = result.error or "Unknown error"
        category, text = _classify_failure(error)
        lessons.append(Lesson(
            text=text,
            category=category,
            source_type=type_name,
        ))

    # Tool insight: if the output mentions specific tools
    output_lower = (result.output or "").lower()
    if "edit_file" in output_lower and result.success:
        lessons.append(Lesson(
            text="edit_file used successfully for targeted changes",
            category="Tool Insights",
            source_type=type_name,
        ))
    if "write_file" in output_lower and result.success:
        lessons.append(Lesson(
            text="write_file used — verify this was for a new file, not replacing existing",
            category="Tool Insights",
            source_type=type_name,
        ))

    return lessons
