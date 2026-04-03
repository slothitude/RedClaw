"""Autonomous Executive — background goal-pursuing loop.

The executive runs as a background asyncio task that:
1. Loads goals from the goal queue
2. Decomposes goals into PlanSteps via LLM
3. Executes steps via SubagentSpawner
4. Evaluates completion via LLM
5. Publishes events for each milestone
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redclaw.runtime.event_bus import AGIEvent, EVENT_GOAL_CREATED, EVENT_GOAL_PROGRESS, EVENT_GOAL_COMPLETED

if TYPE_CHECKING:
    from redclaw.api.client import LLMClient
    from redclaw.api.providers import ProviderConfig
    from redclaw.crypt.crypt import Crypt
    from redclaw.crypt.dna import DNAManager
    from redclaw.crypt.dream import DreamSynthesizer
    from redclaw.runtime.event_bus import EventBus
    from redclaw.runtime.subagent import SubagentSpawner
    from redclaw.tools.registry import ToolExecutor

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────

@dataclass
class PlanStep:
    """A single step in a goal plan."""
    task: str
    subagent_type: str = "general"  # coder, searcher, general
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"  # pending, active, completed, failed
    result: str = ""


@dataclass
class Goal:
    """An autonomous goal."""
    id: str = ""
    description: str = ""
    status: str = "pending"  # pending, active, completed, failed, parked
    priority: int = 5  # 1-10, higher = more important
    completion_criteria: str = ""
    decomposed_steps: list[dict[str, Any]] = field(default_factory=list)
    progress: float = 0.0  # 0.0 to 1.0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"goal-{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
            self.updated_at = self.created_at


# ── Storage ──────────────────────────────────────────────────

def _load_goals(path: Path) -> list[Goal]:
    """Load goals from JSONL file."""
    if not path.is_file():
        return []
    goals = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            steps = data.pop("decomposed_steps", [])
            g = Goal(**data)
            g.decomposed_steps = steps
            goals.append(g)
        except (json.JSONDecodeError, TypeError):
            continue
    return goals


def _save_goals(goals: list[Goal], path: Path) -> None:
    """Save goals to JSONL atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for g in goals:
        d = asdict(g)
        lines.append(json.dumps(d))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".goals_", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Autonomous Executive ────────────────────────────────────

class AutonomousExecutive:
    """Background autonomous goal-pursuing loop."""

    def __init__(
        self,
        client: LLMClient,
        provider: ProviderConfig,
        model: str,
        tools: ToolExecutor,
        spawner: SubagentSpawner,
        crypt: Crypt,
        dna_manager: DNAManager,
        dream_synthesizer: DreamSynthesizer,
        event_bus: EventBus,
        soul_text: str,
        working_dir: str | None = None,
        interval: int = 60,
        max_steps_per_goal: int = 10,
        max_eval_rounds: int = 3,
    ) -> None:
        self.client = client
        self.provider = provider
        self.model = model
        self.tools = tools
        self.spawner = spawner
        self.crypt = crypt
        self.dna_manager = dna_manager
        self.dream_synthesizer = dream_synthesizer
        self.event_bus = event_bus
        self.soul_text = soul_text
        self.working_dir = working_dir
        self.interval = interval
        self.max_steps = max_steps_per_goal
        self.max_eval_rounds = max_eval_rounds

        self._goals_path = Path.home() / ".redclaw" / "agi" / "goals.jsonl"
        self._goals_path.parent.mkdir(parents=True, exist_ok=True)
        self._running = False
        self._reflection_cache: str = ""
        self._reflection_time: float = 0.0

    # ── Public API ──────────────────────────────────────────

    def add_goal(self, description: str, priority: int = 5, completion_criteria: str = "") -> Goal:
        """Add a new goal to the queue."""
        goal = Goal(
            description=description,
            priority=min(10, max(1, priority)),
            completion_criteria=completion_criteria,
        )
        goals = _load_goals(self._goals_path)
        goals.append(goal)
        _save_goals(goals, self._goals_path)

        asyncio.create_task(self.event_bus.publish(AGIEvent(
            type=EVENT_GOAL_CREATED,
            data={"id": goal.id, "description": description[:200]},
            source="executive",
        )))
        return goal

    def get_goals(self) -> list[Goal]:
        """Get all goals."""
        return _load_goals(self._goals_path)

    async def get_status_summary(self) -> str:
        """Get a brief status summary for AGI context injection."""
        goals = _load_goals(self._goals_path)
        active = [g for g in goals if g.status in ("pending", "active")]
        completed = [g for g in goals if g.status == "completed"]
        failed = [g for g in goals if g.status in ("failed", "parked")]

        lines = [f"Goals: {len(active)} active, {len(completed)} completed, {len(failed)} failed/parked"]
        for g in active[:3]:
            lines.append(f"  - [{g.status}] {g.description[:60]} (progress: {g.progress:.0%})")

        # DNA trait summary
        from redclaw.runtime.subagent_types import SubagentType
        for sa_type in SubagentType:
            traits = self.dna_manager.load_traits(sa_type)
            lines.append(f"  DNA {sa_type.value}: gen={traits.generation} acc={traits.accuracy:.2f}")

        return "\n".join(lines)

    # ── Main loop ────────────────────────────────────────────

    async def run(self) -> None:
        """Background loop: load goals → plan → execute → evaluate."""
        self._running = True
        logger.info("AGI Executive started (interval=%ds)", self.interval)

        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("AGI Executive tick error: %s", e)

            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        """One iteration of the executive loop."""
        goals = _load_goals(self._goals_path)

        # Find highest-priority pending/active goal
        pending = [g for g in goals if g.status in ("pending", "active")]
        if not pending:
            return

        pending.sort(key=lambda g: g.priority, reverse=True)
        goal = pending[0]

        # Decompose if no steps yet
        if not goal.decomposed_steps:
            steps = await self._decompose(goal)
            if not steps:
                goal.status = "parked"
                _save_goals(goals, self._goals_path)
                return
            goal.decomposed_steps = [asdict(s) for s in steps]
            goal.status = "active"

        # Execute next pending step
        step_dicts = goal.decomposed_steps
        pending_steps = [s for s in step_dicts if s["status"] == "pending"]

        if not pending_steps:
            # All steps done — evaluate
            await self._evaluate(goal, goals)
            return

        # Execute one step
        step = pending_steps[0]
        step["status"] = "active"
        _save_goals(goals, self._goals_path)

        await self.event_bus.publish(AGIEvent(
            type=EVENT_GOAL_PROGRESS,
            data={"goal_id": goal.id, "step": step["task"][:100]},
            source="executive",
        ))

        result = await self._execute_step(step)

        step["status"] = "completed" if result.success else "failed"
        step["result"] = result.output[:500]

        # Update progress
        completed_count = sum(1 for s in step_dicts if s["status"] in ("completed", "failed"))
        goal.progress = completed_count / len(step_dicts) if step_dicts else 0.0
        goal.updated_at = datetime.now(timezone.utc).isoformat()

        _save_goals(goals, self._goals_path)

    async def _decompose(self, goal: Goal) -> list[PlanStep]:
        """Decompose a goal into PlanSteps via LLM."""
        prompt = (
            f"Decompose this goal into concrete steps (max {self.max_steps}):\n"
            f"Goal: {goal.description}\n"
            f"Criteria: {goal.completion_criteria or 'Task is complete when all steps succeed.'}\n\n"
            "For each step, specify:\n"
            "- task: what to do\n"
            "- type: 'coder', 'searcher', or 'general'\n\n"
            "Output one step per line: TYPE | task description"
        )

        try:
            from redclaw.api.types import InputMessage, MessageRequest, Role
            request = MessageRequest(
                model=self.model,
                messages=[InputMessage(role=Role.USER, content=prompt)],
                system="You are a task decomposition engine. Be concise.",
                max_tokens=512,
                stream=False,
            )
            response = await self.client.send_message(request)
            text = response.text if hasattr(response, "text") else str(response)
        except Exception as e:
            logger.error("Goal decomposition failed: %s", e)
            return []

        # Parse steps
        steps: list[PlanStep] = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if "|" not in line:
                continue
            parts = line.split("|", 1)
            if len(parts) != 2:
                continue
            type_str = parts[0].strip().lower()
            task = parts[1].strip()
            if not task:
                continue
            if type_str not in ("coder", "searcher", "general"):
                type_str = "general"
            steps.append(PlanStep(task=task, subagent_type=type_str))
            if len(steps) >= self.max_steps:
                break

        return steps

    async def _execute_step(self, step: dict[str, Any]):
        """Execute a single plan step via subagent."""
        from redclaw.runtime.subagent_types import SubagentType
        try:
            sa_type = SubagentType(step.get("subagent_type", "general"))
        except ValueError:
            sa_type = SubagentType.GENERAL

        return await self.spawner.run_subagent(
            task=step["task"],
            working_dir=self.working_dir,
            subagent_type=sa_type,
        )

    async def _evaluate(self, goal: Goal, goals: list[Goal]) -> None:
        """Evaluate if a goal is complete via LLM."""
        steps_summary = "\n".join(
            f"- [{'OK' if s['status'] == 'completed' else 'FAIL'}] {s['task'][:80]}: {s.get('result', '')[:100]}"
            for s in goal.decomposed_steps
        )

        prompt = (
            f"Is this goal satisfied?\n"
            f"Goal: {goal.description}\n"
            f"Criteria: {goal.completion_criteria or 'All steps completed successfully.'}\n\n"
            f"Steps:\n{steps_summary}\n\n"
            "Answer YES or NO with a brief reason."
        )

        try:
            from redclaw.api.types import InputMessage, MessageRequest, Role
            request = MessageRequest(
                model=self.model,
                messages=[InputMessage(role=Role.USER, content=prompt)],
                system="You are a goal evaluation engine.",
                max_tokens=256,
                stream=False,
            )
            response = await self.client.send_message(request)
            text = response.text if hasattr(response, "text") else str(response)
        except Exception as e:
            logger.error("Goal evaluation failed: %s", e)
            return

        if text.strip().upper().startswith("YES"):
            goal.status = "completed"
            goal.progress = 1.0
        else:
            # Check if we have eval rounds left
            goal.status = "parked"  # Don't retry endlessly

        goal.updated_at = datetime.now(timezone.utc).isoformat()
        _save_goals(goals, self._goals_path)

        await self.event_bus.publish(AGIEvent(
            type=EVENT_GOAL_COMPLETED,
            data={"id": goal.id, "status": goal.status, "description": goal.description[:100]},
            source="executive",
        ))

    # ── Self-reflection (Phase 6) ───────────────────────────

    async def self_reflect(self) -> str:
        """LLM-powered self-reflection on current AGI state. Cached 5min."""
        import time
        now = time.time()
        if self._reflection_cache and (now - self._reflection_time) < 300:
            return self._reflection_cache

        status = await self.get_status_summary()
        dharma = self.crypt.load_dharma()[:500]

        prompt = (
            "Reflect on your current state as an autonomous AI agent. Be concise (3-5 sentences).\n\n"
            f"Current state:\n{status}\n\n"
            f"Wisdom accumulated:\n{dharma}\n\n"
            "What are your strengths? What needs improvement? What should you focus on next?"
        )

        try:
            from redclaw.api.types import InputMessage, MessageRequest, Role
            request = MessageRequest(
                model=self.model,
                messages=[InputMessage(role=Role.USER, content=prompt)],
                system="You are reflecting on your own operation.",
                max_tokens=256,
                stream=False,
            )
            response = await self.client.send_message(request)
            text = response.text if hasattr(response, "text") else str(response)
        except Exception as e:
            logger.error("Self-reflection failed: %s", e)
            return "Reflection unavailable."

        self._reflection_cache = text.strip()
        self._reflection_time = now
        return self._reflection_cache

    # ── Shutdown ─────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Stop the executive loop."""
        self._running = False
        logger.info("AGI Executive shutting down")
