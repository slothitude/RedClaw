"""Conversation runtime — the core agent loop.

Implements: stream API call → build assistant message → tool execution → loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Awaitable

from redclaw.api.client import LLMClient
from redclaw.api.providers import ProviderConfig
from redclaw.api.types import (
    InputMessage,
    MessageRequest,
    Role,
    StreamEvent,
    StreamEventType,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from redclaw.runtime.compact import CompactionConfig, compact_session, should_compact
from redclaw.runtime.hooks import HookRunner
from redclaw.runtime.permissions import PermissionPolicy
from redclaw.runtime.prompt import build_system_prompt
from redclaw.runtime.session import Session, save_session
from redclaw.runtime.usage import UsageTracker
from redclaw.tools.registry import ToolExecutor

logger = logging.getLogger(__name__)


# ── Turn Summary ─────────────────────────────────────────────


@dataclass
class TurnSummary:
    """Summary of a single user→agent turn."""
    text: str = ""
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage.zero)
    error: str | None = None


# ── Event callbacks ──────────────────────────────────────────


@dataclass
class ConversationCallbacks:
    """Callbacks for streaming events during a turn."""
    on_text_delta: Callable[[str], Awaitable[None]] | None = None
    on_tool_begin: Callable[[str, str, str], Awaitable[None]] | None = None  # id, name, input_json
    on_tool_result: Callable[[str, str, bool], Awaitable[None]] | None = None  # tool_use_id, result, is_error
    on_usage: Callable[[Usage], Awaitable[None]] | None = None
    on_error: Callable[[str], Awaitable[None]] | None = None


# ── Conversation Runtime ────────────────────────────────────


class ConversationRuntime:
    """The core agent loop."""

    def __init__(
        self,
        client: LLMClient,
        provider: ProviderConfig,
        model: str,
        session: Session,
        tools: ToolExecutor,
        permission_policy: PermissionPolicy | None = None,
        hooks: HookRunner | None = None,
        usage_tracker: UsageTracker | None = None,
        compact_config: CompactionConfig | None = None,
        working_dir: str | None = None,
        system_prompt: str | None = None,
        max_tool_rounds: int = 50,
        memory: Any | None = None,
        subagent_spawner: Any | None = None,
        mode: str = "coder",
        assistant_context: str = "",
        soul_text: str = "",
        agi_context: str = "",
        token_saver: Any | None = None,
    ) -> None:
        self.client = client
        self.provider = provider
        self.model = model
        self.session = session
        self.tools = tools
        self.permissions = permission_policy or PermissionPolicy()
        self.hooks = hooks or HookRunner()
        self.usage = usage_tracker or UsageTracker()
        self.compact_config = compact_config or CompactionConfig()
        self.working_dir = working_dir
        self._system_prompt = system_prompt
        self.max_tool_rounds = max_tool_rounds
        self._abort = False
        self.memory = memory
        self.subagent_spawner = subagent_spawner
        self._mode = mode
        self._assistant_context = assistant_context
        self._soul_text = soul_text
        self._agi_context = agi_context
        self._token_saver = token_saver
        self._original_tools = tools
        self._original_system_prompt = system_prompt
        self._plan_mode = False

    @property
    def system_prompt(self) -> str:
        if self._system_prompt is None:
            memory_snapshot = ""
            if self.memory:
                memory_snapshot = self.memory.snapshot
            self._system_prompt = build_system_prompt(
                self.working_dir,
                memory_snapshot=memory_snapshot,
                skills_guidance=hasattr(self.tools.specs, 'get') and 'skills_list' in self.tools.specs,
                mode=self._mode,
                assistant_context=self._assistant_context,
                soul_text=self._soul_text,
                agi_context=self._agi_context,
            )
        return self._system_prompt

    @property
    def plan_mode(self) -> bool:
        return self._plan_mode

    def set_plan_mode(self, enabled: bool) -> None:
        if enabled and not self._plan_mode:
            plan_tools = {"read_file", "glob_search", "grep_search", "web_search", "web_reader", "write_file"}
            filtered = ToolExecutor.__new__(ToolExecutor)
            filtered.specs = {
                name: spec for name, spec in self._original_tools.specs.items()
                if name in plan_tools
            }
            self.tools = filtered
            self._system_prompt = self.system_prompt + (
                "\n\n[PLAN MODE] You are in plan mode. Explore the codebase, then "
                "write the full implementation plan to .redclaw.md. This file is your "
                "single source of truth — it contains the plan, the todo checklist, and "
                "any notes. You may ONLY write to .redclaw.md — do not edit any other "
                "files. Set the mode to \"planning\" in .redclaw.md. When done, tell "
                "the user to type /go to execute the plan."
            )
            self._plan_mode = True
        elif not enabled and self._plan_mode:
            # Read .redclaw.md and feed it to the agent
            rc_text = ""
            rc_path = os.path.join(self.working_dir or ".", ".redclaw.md")
            try:
                rc_text = open(rc_path, encoding="utf-8").read().strip()
            except FileNotFoundError:
                pass

            self.tools = self._original_tools

            # Update .redclaw.md mode to executing
            if rc_text:
                rc_text = rc_text.replace("## Mode: planning", "## Mode: executing")
                try:
                    with open(rc_path, "w", encoding="utf-8") as f:
                        f.write(rc_text)
                except OSError:
                    pass

            if rc_text:
                self._system_prompt = self.system_prompt + (
                    "\n\n[EXECUTE MODE] The user approved the plan. Execute it now.\n"
                    "Follow the plan in .redclaw.md step by step. Check off todo "
                    "items and update the file as you work. .redclaw.md is always "
                    "editable — keep it current.\n\n"
                    f"=== .redclaw.md ===\n{rc_text}\n=== end .redclaw.md ==="
                )
            else:
                self._system_prompt = self.system_prompt + (
                    "\n\n[EXECUTE MODE] The user approved the plan. You now have "
                    "full tools. Execute the plan you produced above. Start now."
                )
            self._plan_mode = False

    def abort(self) -> None:
        """Signal the current turn to abort."""
        self._abort = True

    async def run_turn(
        self,
        user_input: str,
        callbacks: ConversationCallbacks | None = None,
    ) -> TurnSummary:
        """Run a full user→agent turn with tool execution loop.

        1. Append user message
        2. Loop: stream API call → build assistant message → if no tool use break → execute tools
        3. Return TurnSummary
        """
        self._abort = False
        cb = callbacks or ConversationCallbacks()
        summary = TurnSummary()

        # Add user message
        self.session.add_message(InputMessage.user_text(user_input))

        # Check compaction
        if should_compact(self.session, self.compact_config):
            compact_session(self.session, self.compact_config)

        # Tool execution loop
        for _ in range(self.max_tool_rounds):
            if self._abort:
                summary.error = "Aborted"
                break

            # Stream the API call
            assistant_content: list[Any] = []
            current_text = ""
            current_tool_id = ""
            current_tool_name = ""
            current_tool_input_json = ""
            turn_usage = Usage.zero()

            request = MessageRequest(
                model=self.model,
                messages=self.session.to_input_messages(),
                tools=self._build_tool_defs(),
                system=self.system_prompt,
                stream=True,
            )

            async for event in self.client.stream_message(request):
                if self._abort:
                    break

                if event.type == StreamEventType.TEXT_DELTA:
                    current_text += event.text_delta
                    if cb.on_text_delta:
                        await cb.on_text_delta(event.text_delta)

                elif event.type == StreamEventType.TOOL_USE_BEGIN:
                    # Flush any pending text
                    if current_text:
                        assistant_content.append(TextBlock(text=current_text))
                        current_text = ""
                    current_tool_id = event.tool_id
                    current_tool_name = event.tool_name
                    # Some providers send complete args in the begin event
                    initial_delta = event.data.get("input_json_delta", "")
                    current_tool_input_json = initial_delta

                elif event.type == StreamEventType.TOOL_USE_DELTA:
                    current_tool_input_json += event.tool_input_delta

                elif event.type == StreamEventType.USAGE:
                    turn_usage = Usage(
                        input_tokens=turn_usage.input_tokens + event.usage.input_tokens,
                        output_tokens=turn_usage.output_tokens + event.usage.output_tokens,
                        cache_creation_input_tokens=turn_usage.cache_creation_input_tokens + event.usage.cache_creation_input_tokens,
                        cache_read_input_tokens=turn_usage.cache_read_input_tokens + event.usage.cache_read_input_tokens,
                    )
                    if cb.on_usage:
                        await cb.on_usage(turn_usage)

                elif event.type == StreamEventType.ERROR:
                    summary.error = event.error_message
                    if cb.on_error:
                        await cb.on_error(event.error_message)
                    break

                elif event.type == StreamEventType.MESSAGE_STOP:
                    pass

            if summary.error:
                break

            # Flush remaining text
            if current_text:
                assistant_content.append(TextBlock(text=current_text))
                summary.text += current_text

            # Flush pending tool call
            if current_tool_id:
                try:
                    tool_input = json.loads(current_tool_input_json) if current_tool_input_json else {}
                except json.JSONDecodeError:
                    tool_input = {}
                assistant_content.append(ToolUseBlock(
                    id=current_tool_id,
                    name=current_tool_name,
                    input=tool_input,
                ))

            if not assistant_content:
                break

            # Save assistant message
            self.session.add_message(InputMessage(
                role=Role.ASSISTANT,
                content=assistant_content,
            ))

            # Check if there are tool calls to execute
            tool_calls = [b for b in assistant_content if isinstance(b, ToolUseBlock)]
            if not tool_calls:
                break  # No tools — turn is done

            # Execute each tool call
            tool_results: list[ToolResultBlock] = []
            for tc in tool_calls:
                if self._abort:
                    break

                # Record tool call for token saver
                if self._token_saver:
                    self._token_saver.record_tool_call(tc.name)

                # Check permissions
                spec = self.tools.specs.get(tc.name)
                tool_level = spec.permission if spec else __import__("redclaw.api.types", fromlist=["PermissionLevel"]).PermissionLevel.DANGER_FULL_ACCESS
                allowed, reason = self.permissions.authorize(tc.name, tool_level)

                if not allowed:
                    result = f"Permission denied: {reason}"
                    tool_results.append(ToolResultBlock(
                        tool_use_id=tc.id,
                        content=result,
                        is_error=True,
                    ))
                    if cb.on_tool_result:
                        await cb.on_tool_result(tc.id, result, True)
                    continue

                if reason == "ask":
                    # Signal that user confirmation is needed
                    pass  # TODO: integrate with CLI/Godot confirmation

                # Pre-hook
                if not await self.hooks.run_pre_tool(tc.name, tc.input):
                    tool_results.append(ToolResultBlock(
                        tool_use_id=tc.id,
                        content="Blocked by pre-tool hook",
                        is_error=True,
                    ))
                    continue

                # Execute
                if cb.on_tool_begin:
                    await cb.on_tool_begin(tc.id, tc.name, json.dumps(tc.input))

                result = await self.tools.execute(tc.name, tc.input)
                summary.tool_calls += 1

                # Post-hook
                await self.hooks.run_post_tool(tc.name, result)

                is_error = result.startswith("Error:")
                tool_results.append(ToolResultBlock(
                    tool_use_id=tc.id,
                    content=result,
                    is_error=is_error,
                ))

                if cb.on_tool_result:
                    await cb.on_tool_result(tc.id, result, is_error)

            # Append tool results as a tool message
            if tool_results:
                self.session.add_message(InputMessage(
                    role=Role.TOOL,
                    content=tool_results,
                ))

        # Record usage
        self.usage.record(turn_usage)
        self.usage.increment_turn()
        summary.usage = turn_usage

        # Save session
        save_session(self.session, self.working_dir)

        return summary

    def _build_tool_defs(self) -> list[ToolDefinition]:
        """Build tool definitions for the API request."""
        defs = []
        for spec in self.tools.specs.values():
            defs.append(ToolDefinition(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
            ))
        return defs
