"""Tool registry — specs, dispatch, and execution."""

from __future__ import annotations

import json
import subprocess
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Awaitable

from redclaw.api.types import PermissionLevel


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    permission: PermissionLevel
    execute: Callable[..., Awaitable[str]]


# ── Tool Definitions ─────────────────────────────────────────


def mvp_tool_specs(working_dir: str | None = None, search_url: str | None = None, reader_url: str | None = None) -> list[ToolSpec]:
    """Return the MVP tool specs plus optional web_search and web_reader."""
    from redclaw.tools.bash import execute_bash
    from redclaw.tools.file_ops import execute_read_file, execute_write_file, execute_edit_file
    from redclaw.tools.search import execute_glob_search, execute_grep_search, execute_web_search, execute_web_reader

    cwd = working_dir or str(Path.cwd())

    specs = [
        ToolSpec(
            name="bash",
            description="Execute a bash command with a timeout. Returns stdout, stderr, and exit code.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)", "default": 120},
                },
                "required": ["command"],
            },
            permission=PermissionLevel.DANGER_FULL_ACCESS,
            execute=lambda **kw: execute_bash(cwd=cwd, **kw),
        ),
        ToolSpec(
            name="read_file",
            description="Read the contents of a file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to read"},
                    "offset": {"type": "integer", "description": "Line offset to start from (0-based)"},
                    "limit": {"type": "integer", "description": "Max number of lines to read"},
                },
                "required": ["path"],
            },
            permission=PermissionLevel.READ_ONLY,
            execute=lambda **kw: execute_read_file(cwd=cwd, **kw),
        ),
        ToolSpec(
            name="write_file",
            description="Write content to a file. Creates the file if it doesn't exist.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to write"},
                    "content": {"type": "string", "description": "The content to write"},
                },
                "required": ["path", "content"],
            },
            permission=PermissionLevel.WORKSPACE_WRITE,
            execute=lambda **kw: execute_write_file(cwd=cwd, **kw),
        ),
        ToolSpec(
            name="edit_file",
            description="Edit a file by replacing an exact string match with new text.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to edit"},
                    "old_string": {"type": "string", "description": "Exact text to find"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            permission=PermissionLevel.WORKSPACE_WRITE,
            execute=lambda **kw: execute_edit_file(cwd=cwd, **kw),
        ),
        ToolSpec(
            name="glob_search",
            description="Search for files matching a glob pattern.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern (e.g. **/*.py)"},
                    "path": {"type": "string", "description": "Directory to search in (default: working dir)"},
                },
                "required": ["pattern"],
            },
            permission=PermissionLevel.READ_ONLY,
            execute=lambda **kw: execute_glob_search(cwd=cwd, **kw),
        ),
        ToolSpec(
            name="grep_search",
            description="Search file contents for a regex pattern.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Directory or file to search in"},
                    "glob": {"type": "string", "description": "File glob filter (e.g. *.py)"},
                    "ignore_case": {"type": "boolean", "description": "Case-insensitive search"},
                },
                "required": ["pattern"],
            },
            permission=PermissionLevel.READ_ONLY,
            execute=lambda **kw: execute_grep_search(cwd=cwd, **kw),
        ),
    ]

    # Add web_search if search_url is configured
    if search_url:
        specs.append(ToolSpec(
            name="web_search",
            description="Search the web using SearXNG. Returns search results with titles, URLs, and snippets.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "categories": {"type": "string", "description": "Categories to search (e.g. 'general', 'news', 'images')"},
                },
                "required": ["query"],
            },
            permission=PermissionLevel.READ_ONLY,
            execute=lambda **kw: execute_web_search(search_url=search_url, **kw),
        ))

    # Add web_reader if reader_url is configured
    if reader_url:
        specs.append(ToolSpec(
            name="web_reader",
            description="Read and extract content from any webpage. Returns formatted text/markdown from the given URL.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to read"},
                    "format": {"type": "string", "description": "Output format: text (markdown), clean (plain text), json (structured), stats", "default": "text"},
                },
                "required": ["url"],
            },
            permission=PermissionLevel.READ_ONLY,
            execute=lambda **kw: execute_web_reader(reader_url=reader_url, **kw),
        ))

    return specs


class ToolExecutor:
    """Dispatches tool calls to their implementations."""

    def __init__(
        self,
        working_dir: str | None = None,
        search_url: str | None = None,
        reader_url: str | None = None,
    ) -> None:
        self.specs: dict[str, ToolSpec] = {
            s.name: s for s in mvp_tool_specs(working_dir, search_url, reader_url)
        }

    def register_tool(self, spec: ToolSpec) -> None:
        """Register a dynamic tool (from skills, MCP, etc.)."""
        self.specs[spec.name] = spec

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions for the API request."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "input_schema": s.input_schema,
            }
            for s in self.specs.values()
        ]

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Execute a tool by name with the given input. Returns the result string."""
        spec = self.specs.get(tool_name)
        if spec is None:
            return f"Error: Unknown tool '{tool_name}'"

        try:
            result = await spec.execute(**tool_input)
            return result
        except Exception as exc:
            return f"Error executing {tool_name}: {exc}\n{traceback.format_exc()}"
