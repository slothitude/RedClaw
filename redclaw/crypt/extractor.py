"""Lesson extractor — extract meaningful lessons from subagent results.

Analyzes actual output content, tool patterns, and task context to produce
actionable insights rather than generic boilerplate.
"""

from __future__ import annotations

import re
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


def _extract_tools_used(output: str) -> list[str]:
    """Extract which tools were actually used from the output text."""
    tools = []
    tool_patterns = {
        "grep_search": r"(?:grep_search|grep for|searching for|search.*pattern)",
        "glob_search": r"(?:glob_search|glob.*find|find.*files matching)",
        "read_file": r"(?:read_file|read.*file|reading|viewing file)",
        "edit_file": r"(?:edit_file|editing|replacing|modifying.*file)",
        "write_file": r"(?:write_file|writing|creating.*file)",
        "bash": r"(?:bash|running|executing|command:|shell)",
        "web_search": r"(?:web_search|searching the web|searching online)",
        "web_reader": r"(?:web_reader|fetching.*url|reading.*page)",
    }
    output_lower = output.lower()
    for tool, pattern in tool_patterns.items():
        if re.search(pattern, output_lower):
            tools.append(tool)
    return tools


def _extract_file_types(output: str) -> list[str]:
    """Extract what file types were being worked on."""
    extensions = re.findall(r'\b\w+\.(py|js|ts|jsx|tsx|java|rb|go|rs|c|cpp|h|md|txt|json|yaml|yml|toml|cfg|ini|sh)\b', output)
    return list(set(extensions))


def _extract_key_concepts(output: str) -> list[str]:
    """Extract key technical concepts from the output."""
    concepts = []
    # Look for class/function/method definitions
    concepts.extend(re.findall(r'(?:class|def|function|method)\s+(\w+)', output)[:5])
    # Look for framework-specific terms
    framework_terms = re.findall(
        r'\b(Django|Flask|React|Model|View|Controller|Serializer|Validator|'
        r'Middleware|Migration|QuerySet|Manager|Form|Template|URL|Router|'
        r'Schema|TestCase|pytest|unittest|API|REST|ORM)\b',
        output,
    )
    concepts.extend(list(set(framework_terms))[:5])
    return concepts


def _classify_failure(error: str, output: str, task: str) -> tuple[str, str]:
    """Classify a failure into a category and lesson text.

    Returns (category, lesson_text).
    """
    error_lower = error.lower()
    output_lower = output.lower()

    # Timeout — analyze what the agent was doing when it timed out
    if any(p in error_lower for p in _TIMEOUT_PATTERNS):
        tools = _extract_tools_used(output)
        files = _extract_file_types(output)
        if tools:
            tool_str = ", ".join(tools[:4])
            return "Warnings", f"Timed out while using {tool_str} — task may need decomposition into smaller steps"
        return "Warnings", "Timed out — complex tasks benefit from breaking into focused sub-tasks with clear goals"

    if any(p in error_lower for p in _SYNTAX_PATTERNS):
        return "Warnings", "Syntax errors often come from incomplete file reads — always read the full file before editing"

    if any(p in error_lower for p in _IMPORT_PATTERNS):
        return "Warnings", "Import errors suggest missing dependencies — verify environment and check for version mismatches"

    if any(p in error_lower for p in _FILE_NOT_FOUND):
        return "Warnings", "File not found — use glob_search to verify paths before attempting to read or edit"

    if any(p in error_lower for p in _PERMISSION_PATTERNS):
        return "Warnings", "Permission errors need explicit user authorization — check file and directory permissions"

    if any(p in error_lower for p in _TOOL_FAILURE):
        return "Warnings", "Tool execution failures — verify command syntax, arguments, and working directory"

    # Generic failure — try to extract what went wrong from output
    if "error" in output_lower:
        # Find the actual error in the output
        error_lines = [l.strip() for l in output.split("\n") if "error" in l.lower() and len(l.strip()) > 10]
        if error_lines:
            specific = error_lines[-1][:120]
            return "Warnings", f"Failed: {specific}"

    # Extract what the task was about for context
    concepts = _extract_key_concepts(output)
    if concepts:
        return "Warnings", f"Failed on task involving {', '.join(concepts[:3])} — may need different approach"

    return "Warnings", f"Failed: {task[:100]}"


def _classify_success(output: str, task: str, tool_calls: int) -> tuple[str, str]:
    """Classify a success into a specific, actionable lesson."""
    tools = _extract_tools_used(output)
    files = _extract_file_types(output)
    concepts = _extract_key_concepts(output)

    # Build a specific lesson based on what actually happened
    parts = []

    # What tools worked
    if tools:
        tool_str = " → ".join(tools[:5])
        parts.append(f"Tools: {tool_str}")

    # What file types
    if files:
        parts.append(f"Files: {', '.join(files[:3])}")

    # What concepts
    if concepts:
        parts.append(f"Key: {', '.join(concepts[:3])}")

    if parts:
        detail = " | ".join(parts)
        return "Successful Patterns", detail

    # Fallback: extract the actual change description from output
    # Look for "fix", "change", "update", "add" sentences
    change_sentences = re.findall(
        r'(?:fix|chang|updat|add|modif|resolv|implement|refactor)[^.]*\.',
        output[:2000],
        re.IGNORECASE,
    )
    if change_sentences:
        return "Successful Patterns", change_sentences[0][:150]

    return "Successful Patterns", f"Completed: {task[:120]}"


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

        # Tool insight: if specific tools led to success, note the pattern
        tools = _extract_tools_used(output)
        if "edit_file" in tools and "read_file" in tools:
            lessons.append(Lesson(
                text="Read-then-edit pattern: read target file before editing for reliable targeted changes",
                category="Tool Insights",
                source_type=type_name,
            ))
        elif "edit_file" in tools:
            lessons.append(Lesson(
                text="edit_file works well for targeted surgical changes to existing code",
                category="Tool Insights",
                source_type=type_name,
            ))

        if "bash" in tools and result.tool_calls > 10:
            lessons.append(Lesson(
                text="Heavy bash usage for verification — running tests/commands to validate changes before finishing",
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

        # Warning about specific failure patterns
        if result.tool_calls > 15:
            lessons.append(Lesson(
                text="High tool call count without convergence — plan approach before executing",
                category="Warnings",
                source_type=type_name,
            ))

    return lessons
