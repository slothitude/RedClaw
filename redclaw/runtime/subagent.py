"""Subagent delegation — isolated nested ConversationRuntime for subtasks.

Creates a restricted runtime with:
- Limited toolset (strips delegate, memory, interactive tools)
- Depth limiting (max 2 levels)
- Turn limit per subagent (default 5)
- Timeout (default 60s)
- Batch mode: up to 3 concurrent subtasks
- Subagent types (bloodlines) with tailored prompts and toolsets
- Retry-with-reflection: accumulates failure context across retries
- Wisdom inheritance: crypt integration for bloodline wisdom
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from redclaw.runtime.subagent_types import (
    SubagentType,
    get_subagent_prompt,
    get_subagent_toolset_names,
)
from redclaw.tools.toolsets import resolve_toolset

if TYPE_CHECKING:
    from redclaw.api.client import LLMClient
    from redclaw.api.providers import ProviderConfig
    from redclaw.crypt.crypt import Crypt
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
    attempts: int = 1


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
        max_retries: int = 3,
        crypt: Crypt | None = None,
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
        self.max_retries = max_retries
        self.crypt = crypt
        self._depth = 0
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _build_restricted_tools(
        self,
        subagent_type: SubagentType = SubagentType.GENERAL,
    ) -> ToolExecutor:
        """Build a ToolExecutor with only the allowed tools for the given type."""
        from redclaw.tools.registry import ToolExecutor as TE

        # Resolve the type-specific toolset
        toolset_names = get_subagent_toolset_names(subagent_type)
        allowed: set[str] = set()
        for name in toolset_names:
            allowed |= resolve_toolset(name)

        restricted = TE()
        for name, spec in self.tools.specs.items():
            if name not in _SUBAGENT_EXCLUDED and name in allowed:
                restricted.specs[name] = spec
        return restricted

    async def run_subagent(
        self,
        task: str,
        working_dir: str | None = None,
        subagent_type: SubagentType = SubagentType.GENERAL,
    ) -> SubagentResult:
        """Run a single subagent task with retry-with-reflection."""
        if self._depth >= self.max_depth:
            return SubagentResult(
                success=False,
                output="",
                error=f"Maximum subagent depth ({self.max_depth}) reached.",
            )

        async with self._semaphore:
            return await self._execute_with_retry(task, working_dir, subagent_type)

    async def run_batch(
        self,
        tasks: list[str],
        working_dir: str | None = None,
        subagent_type: SubagentType = SubagentType.GENERAL,
    ) -> list[SubagentResult]:
        """Run multiple subtasks concurrently (up to max_concurrent)."""
        if len(tasks) > self.max_concurrent:
            return [SubagentResult(
                success=False,
                output="",
                error=f"Too many tasks ({len(tasks)}). Maximum is {self.max_concurrent}.",
            )] * len(tasks)

        coros = [self.run_subagent(t, working_dir, subagent_type) for t in tasks]
        return await asyncio.gather(*coros)

    async def _execute_with_retry(
        self,
        task: str,
        working_dir: str | None,
        subagent_type: SubagentType,
    ) -> SubagentResult:
        """Execute with retry-with-reflection loop."""
        previous_failures: list[str] = []

        for attempt in range(1, self.max_retries + 1):
            # Build task prompt with accumulated failure context
            enhanced_task = task
            if previous_failures:
                failure_context = "\n".join(
                    f"- Attempt {i+1}: {err}" for i, err in enumerate(previous_failures)
                )
                enhanced_task = (
                    f"{task}\n\n"
                    f"Previous attempts:\n{failure_context}\n"
                    f"Try a DIFFERENT approach."
                )

            # Escalate timeout on later retries (desperation escalation)
            timeout = self.timeout + (attempt - 1) * 30.0

            result = await self._execute(
                enhanced_task, working_dir, subagent_type, timeout
            )
            result.attempts = attempt

            if result.success:
                # Entomb successful result
                if self.crypt:
                    self.crypt.entomb(result, task, subagent_type)
                    # Leak wisdom back to the main agent
                    wisdom = self.crypt.load_bloodline_wisdom(subagent_type)
                    if wisdom:
                        result.output += f"\n\n[Bloodline Wisdom Gained]\n{wisdom[:500]}"
                    dharma = self.crypt.load_dharma()
                    if dharma:
                        result.output += f"\n\n[Cross-cutting Patterns]\n{dharma[:300]}"
                return result

            # Track failure for reflection
            error_msg = result.error or result.output[:200] or "Unknown error"
            previous_failures.append(error_msg)
            logger.info(
                "Subagent attempt %d/%d failed: %s",
                attempt, self.max_retries, error_msg[:100],
            )

        # All retries exhausted — entomb failure
        final_result = SubagentResult(
            success=False,
            output=result.output,
            error=f"Failed after {self.max_retries} attempts. Last error: {result.error}",
            tool_calls=result.tool_calls,
            attempts=self.max_retries,
        )
        if self.crypt:
            self.crypt.entomb(final_result, task, subagent_type)
            # Leak warnings back even on failure
            wisdom = self.crypt.load_bloodline_wisdom(subagent_type)
            if wisdom:
                final_result.output += f"\n\n[Bloodline Warnings Learned]\n{wisdom[:500]}"
        return final_result

    async def _execute(
        self,
        task: str,
        working_dir: str | None = None,
        subagent_type: SubagentType = SubagentType.GENERAL,
        timeout: float | None = None,
    ) -> SubagentResult:
        """Execute a subagent task with timeout."""
        effective_timeout = timeout or self.timeout
        try:
            result = await asyncio.wait_for(
                self._run_inner(task, working_dir, subagent_type),
                timeout=effective_timeout,
            )
            return result
        except asyncio.TimeoutError:
            return SubagentResult(
                success=False,
                output="",
                error=f"Subagent timed out after {effective_timeout}s.",
            )
        except Exception as e:
            logger.error("Subagent error: %s", e)
            return SubagentResult(
                success=False,
                output="",
                error=str(e),
            )

    async def _run_inner(
        self,
        task: str,
        working_dir: str | None = None,
        subagent_type: SubagentType = SubagentType.GENERAL,
    ) -> SubagentResult:
        """Inner execution of a subagent task."""
        from redclaw.runtime.conversation import ConversationCallbacks, ConversationRuntime
        from redclaw.runtime.session import Session
        from redclaw.runtime.usage import UsageTracker
        from redclaw.runtime.prompt import build_system_prompt

        restricted_tools = self._build_restricted_tools(subagent_type)

        session = Session(id=f"sub-{uuid.uuid4().hex[:6]}")
        session.working_dir = working_dir

        # Build type-specific system prompt
        extra = get_subagent_prompt(subagent_type)

        # Inject bloodline wisdom from crypt
        if self.crypt:
            wisdom = self.crypt.load_bloodline_wisdom(subagent_type)
            if wisdom:
                extra += f"\n\nBloodline wisdom from previous runs:\n{wisdom}"

        system_prompt = build_system_prompt(
            working_dir=working_dir,
            extra_instructions=extra,
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
    subagent_type: str = "general",
    spawner: SubagentSpawner | None = None,
) -> str:
    """Subagent tool — delegate tasks to isolated sub-agents.

    Provide either 'task' (single) or 'tasks' (newline-separated for batch).
    subagent_type: 'coder', 'searcher', or 'general' (default).
    """
    s = spawner or _spawner
    if s is None:
        return "Error: Subagent system not initialized."

    try:
        sa_type = SubagentType(subagent_type)
    except ValueError:
        sa_type = SubagentType.GENERAL

    if tasks.strip():
        task_list = [t.strip() for t in tasks.strip().split("\n") if t.strip()]
        if task_list:
            results = await s.run_batch(task_list, working_dir, sa_type)
            lines = []
            for i, r in enumerate(results):
                status = "OK" if r.success else "FAILED"
                attempts = f" (attempts: {r.attempts})" if r.attempts > 1 else ""
                lines.append(f"Task {i+1} [{status}]{attempts}: {r.output[:500]}")
                if r.error:
                    lines.append(f"  Error: {r.error}")
            return "\n".join(lines)

    result = await s.run_subagent(task, working_dir, sa_type)
    if result.error:
        attempts_info = f" after {result.attempts} attempt(s)" if result.attempts > 1 else ""
        return f"Subagent error{attempts_info}: {result.error}"
    return result.output
