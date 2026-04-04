"""Lesson extractor — extract meaningful lessons from subagent results.

Analyzes actual output content, tool patterns, and task context to produce
actionable insights rather than generic boilerplate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
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


def _extract_edited_files(output: str) -> list[str]:
    """Extract file names that were actually edited, from tool results in output."""
    paths = re.findall(r'Replaced \d+ occurrence in (.+)', output)
    if not paths:
        paths = re.findall(r'Wrote \d+ bytes to (.+)', output)
    return list({Path(p).name for p in paths[:5]})


_REASONING_MARKERS = (
    "let me", "now let", "i need to", "i should", "i'll ", "i will ",
    "first,", "next,", "then i", "okay,", "sure,", "good.",
    "checking", "verifying", "looking at", "let's see",
)


def _extract_change_description(output: str) -> str | None:
    """Extract a sentence describing what was changed, from the LLM's reasoning."""
    patterns = [
        r'(?:The fix|The change) (?:is|was|involves) [^.]*\.',
        r'I (?:fixed|changed|updated|modified|added|removed|replaced) [^.]*\.',
        r'(?:To fix this|To resolve|To address) (?:this|the) [^.]*\.',
        r'(?:Changed|Modified|Updated|Replaced|Added|Removed) \S+ (?:to|from|so that|in order)[^.]*\.',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, output[:3000], re.IGNORECASE)
        for m in matches:
            m = m.strip()
            if not (30 < len(m) < 200):
                continue
            # Reject reasoning chain-of-thought, not actual descriptions
            if any(marker in m.lower() for marker in _REASONING_MARKERS):
                continue
            # Reject if it looks like a run-on with multiple sentences joined by colons
            if m.count(":") > 1:
                continue
            return m
    return None


def _classify_failure(error: str, output: str, task: str) -> tuple[str, str]:
    """Classify a failure into a category and concise lesson text.

    Returns (category, lesson_text).
    """
    error_lower = error.lower()

    if any(p in error_lower for p in _TIMEOUT_PATTERNS):
        return "Warnings", "Timeout — decompose complex tasks into smaller focused steps"

    if any(p in error_lower for p in _SYNTAX_PATTERNS):
        return "Warnings", "Syntax error — always read the full file before editing"

    if any(p in error_lower for p in _IMPORT_PATTERNS):
        return "Warnings", "Import error — verify environment and dependency versions"

    if any(p in error_lower for p in _FILE_NOT_FOUND):
        return "Warnings", "File not found — use glob_search to verify paths before reading"

    if any(p in error_lower for p in _PERMISSION_PATTERNS):
        return "Warnings", "Permission denied — check file and directory permissions"

    if any(p in error_lower for p in _TOOL_FAILURE):
        return "Warnings", "Tool execution failure — verify command syntax and working directory"

    # Generic failure — record the task title concisely, not raw error dump
    task_line = task.split("\n")[0].strip()[:100]
    return "Warnings", f"Failed: {task_line}"


def _classify_success(output: str, task: str, tool_calls: int) -> tuple[str, str]:
    """Classify a success into a specific, actionable lesson."""
    edited = _extract_edited_files(output)

    # Best: the LLM described what it changed
    desc = _extract_change_description(output)
    if desc:
        if edited:
            return "Successful Patterns", f"[{', '.join(edited)}] {desc}"[:220]
        return "Successful Patterns", desc[:220]

    # Good: we know which files were edited + the task context
    if edited:
        task_line = task.split("\n")[0].strip()[:80]
        return "Successful Patterns", f"[{', '.join(edited)}] {task_line}"[:200]

    # Fallback: just the task title
    task_line = task.split("\n")[0].strip()[:140]
    return "Successful Patterns", f"Completed: {task_line}"


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
    output = result.output or ""

    if result.success:
        category, text = _classify_success(output, task, result.tool_calls)
        lessons.append(Lesson(
            text=text,
            category=category,
            source_type=type_name,
        ))

        # Tool insight: note effective tool combos (concise, dedup handles repeats)
        if "edit_file" in output and "read_file" in output:
            lessons.append(Lesson(
                text="Read-then-edit pattern works reliably for targeted surgical changes",
                category="Tool Insights",
                source_type=type_name,
            ))

    else:
        error = result.error or "Unknown error"
        category, text = _classify_failure(error, output, task)
        lessons.append(Lesson(
            text=text,
            category=category,
            source_type=type_name,
        ))

        if result.tool_calls > 20:
            lessons.append(Lesson(
                text="High tool call count without convergence — plan before executing",
                category="Warnings",
                source_type=type_name,
            ))

    return lessons
