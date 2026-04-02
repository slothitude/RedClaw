"""Subagent delegation — isolated nested ConversationRuntime for subtasks.

Creates a restricted runtime with:
- Limited toolset (strips delegate, memory, interactive tools)
- Depth limiting (max 2 levels)
- Turn limit per subagent (default 5)
- Timeout (default 60s)
- Batch mode: up to 3 concurrent subtasks
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from redclaw.tools.toolsets import resolve_toolset

if TYPE_CHECKING:
    from redclaw.api.client import LLMClient
    from redclaw.api.providers import ProviderConfig
    from redclaw.runtime.permissions import PermissionPolicy
    from redclaw.tools.registry import ToolExecutor

logger = logging.getLogger(__name__)

# Tools that subagents should NOT have access to
_SUBAGENT_EXCLUDED = {"subagent", "memory", "skills_list", "skill_view", "skill_manage"}


@dataclass
class SubagentResult:
    """Result from a subagent execution."""
    success: bool
    output: str
    error: str | None = None
    tool_calls: int = 0


class SubagentSpawner:
    """Creates isolated subagent runtimes for delegated tasks."""

    def __init__(
        self,
        client: LLMClient,
        provider: ProviderConfig,
        model: str,
        tools: ToolExecutor,
        permission_policy: PermissionPolicy | None = None,
        max_depth: int = 2,
        max_turns: int = 5,
        timeout: float = 60.0,
        max_concurrent: int = 3,
    ) -> None:
        self.client = client
        self.provider = provider
        self.model = model
        self.tools = tools
        self.permission_policy = permission_policy
        self.max_depth = max_depth
        self.max_turns = max_turns
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self._depth = 0
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _build_restricted_tools(self) -> ToolExecutor:
        """Build a ToolExecutor with only the allowed tools for subagents."""
        from redclaw.tools.registry import ToolExecutor as TE

        restricted = TE()
        for name, spec in self.tools.specs.items():
            if name not in _SUBAGENT_EXCLUDED:
                restricted.specs[name] = spec
        return restricted

    async def run_subagent(self, task: str, working_dir: str | None = None) -> SubagentResult:
        """Run a single subagent task."""
        if self._depth >= self.max_depth:
            return SubagentResult(
                success=False,
                output="",
                error=f"Maximum subagent depth ({self.max_depth}) reached.",
            )

        async with self._semaphore:
            return await self._execute(task, working_dir)

    async def run_batch(self, tasks: list[str], working_dir: str | None = None) -> list[SubagentResult]:
        """Run multiple subtasks concurrently (up to max_concurrent)."""
        if len(tasks) > self.max_concurrent:
            return [SubagentResult(
                success=False,
                output="",
                error=f"Too many tasks ({len(tasks)}). Maximum is {self.max_concurrent}.",
            )] * len(tasks)

        coros = [self.run_subagent(t, working_dir) for t in tasks]
        return await asyncio.gather(*coros)

    async def _execute(self, task: str, working_dir: str | None = None) -> SubagentResult:
        """Execute a subagent task with timeout."""
        try:
            result = await asyncio.wait_for(
                self._run_inner(task, working_dir),
                timeout=self.timeout,
            )
            return result
        except asyncio.TimeoutError:
            return SubagentResult(
                success=False,
                output="",
                error=f"Subagent timed out after {self.timeout}s.",
            )
        except Exception as e:
            logger.error(f"Subagent error: {e}")
            return SubagentResult(
                success=False,
                output="",
                error=str(e),
            )

    async def _run_inner(self, task: str, working_dir: str | None = None) -> SubagentResult:
        """Inner execution of a subagent task."""
        from redclaw.runtime.conversation import ConversationCallbacks, ConversationRuntime
        from redclaw.runtime.session import Session
        from redclaw.runtime.usage import UsageTracker
        from redclaw.runtime.prompt import build_system_prompt

        restricted_tools = self._build_restricted_tools()

        session = Session(id=f"sub-{uuid.uuid4().hex[:6]}")
        session.working_dir = working_dir

        system_prompt = build_system_prompt(
            working_dir=working_dir,
            extra_instructions=(
                "You are a subagent executing a specific subtask. "
                "Focus only on the assigned task. Be concise. "
                "Return your final answer clearly."
            ),
        )

        rt = ConversationRuntime(
            client=self.client,
            provider=self.provider,
            model=self.model,
            session=session,
            tools=restricted_tools,
            permission_policy=self.permission_policy,
            usage_tracker=UsageTracker(),
            working_dir=working_dir,
            system_prompt=system_prompt,
            max_tool_rounds=self.max_turns,
        )

        self._depth += 1
        try:
            summary = await rt.run_turn(task)
            return SubagentResult(
                success=not summary.error,
                output=summary.text or "Subagent completed with no text output.",
                error=summary.error,
                tool_calls=summary.tool_calls,
            )
        finally:
            self._depth -= 1


# ── Tool execute function ────────────────────────────────────

_spawner: SubagentSpawner | None = None


def get_subagent_spawner(
    client: LLMClient,
    provider: ProviderConfig,
    model: str,
    tools: ToolExecutor,
    **kwargs: Any,
) -> SubagentSpawner:
    """Get or create the global SubagentSpawner."""
    global _spawner
    if _spawner is None:
        _spawner = SubagentSpawner(client, provider, model, tools, **kwargs)
    return _spawner


async def execute_subagent(
    task: str,
    tasks: str = "",
    working_dir: str | None = None,
    spawner: SubagentSpawner | None = None,
) -> str:
    """Subagent tool — delegate tasks to isolated sub-agents.

    Provide either 'task' (single) or 'tasks' (newline-separated for batch).
    """
    s = spawner or _spawner
    if s is None:
        return "Error: Subagent system not initialized."

    if tasks.strip():
        task_list = [t.strip() for t in tasks.strip().split("\n") if t.strip()]
        if task_list:
            results = await s.run_batch(task_list, working_dir)
            lines = []
            for i, r in enumerate(results):
                status = "OK" if r.success else "FAILED"
                lines.append(f"Task {i+1} [{status}]: {r.output[:500]}")
                if r.error:
                    lines.append(f"  Error: {r.error}")
            return "\n".join(lines)

    result = await s.run_subagent(task, working_dir)
    if result.error:
        return f"Subagent error: {result.error}"
    return result.output
