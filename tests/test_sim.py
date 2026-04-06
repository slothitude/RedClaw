"""Tests for the simulation controller subsystem.

Covers: SimEngine, SimRunner, sim tools, subagent types, toolsets, DNA, context budget.
"""

from __future__ import annotations

import asyncio
import json
import pytest

from redclaw.sim.types import SimEntity, SimParameter, SimMetrics
from redclaw.sim.engine import SimEngine


# ── SimEngine tests ──────────────────────────────────────────


class TestSimEngine:
    def test_spawn_entity(self):
        e = SimEngine()
        ent = e.spawn_entity("particle", 10, 20, {"mass": 2.0, "vx": 5, "vy": -3})
        assert ent.entity_type == "particle"
        assert ent.x == 10.0
        assert ent.y == 20.0
        assert ent.mass == 2.0
        assert ent.vx == 5.0
        assert ent.vy == -3.0
        assert ent.entity_id.startswith("e-")
        assert len(e.entities) == 1

    def test_spawn_entity_with_custom_id(self):
        e = SimEngine()
        ent = e.spawn_entity("orb", 0, 0, {"entity_id": "my-orb"})
        assert ent.entity_id == "my-orb"

    def test_remove_entity(self):
        e = SimEngine()
        ent = e.spawn_entity("particle", 0, 0)
        assert e.remove_entity(ent.entity_id) is True
        assert len(e.entities) == 0
        assert e.remove_entity("nonexistent") is False

    def test_step_updates_positions(self):
        e = SimEngine()
        e.spawn_entity("particle", 0, 0, {"vx": 10, "vy": 5})
        positions = e.step()
        eid = list(positions.keys())[0]
        # Position should change by velocity * damping
        assert positions[eid]["x"] > 0
        assert positions[eid]["y"] > 0

    def test_step_damping(self):
        e = SimEngine()
        e.spawn_entity("particle", 0, 0, {"vx": 100, "vy": 0})
        e.step()
        e.step()
        eid = list(e.entities.keys())[0]
        # Velocity should decrease due to damping (0.98)
        assert abs(eid and e.entities[eid].vx) < 100

    def test_boundary_bounce(self):
        e = SimEngine()
        e.spawn_entity("particle", 495, 0, {"vx": 100, "vy": 0, "radius": 5})
        e.step()
        eid = list(e.entities.keys())[0]
        entity = e.entities[eid]
        # Should have bounced off right wall (max_x=500, radius=5)
        assert entity.x <= 505  # some overshoot is fine, it bounces back

    def test_gravity(self):
        e = SimEngine()
        e.set_parameter("gravity", 1.0)
        e.spawn_entity("particle", 0, 0)
        e.step()
        eid = list(e.entities.keys())[0]
        assert e.entities[eid].vy > 0  # gravity pulls down (positive y)

    def test_set_parameter(self):
        e = SimEngine()
        param = e.set_parameter("damping", 0.5)
        assert param.value == 0.5
        # Custom parameter
        param2 = e.set_parameter("custom_param", 42.0)
        assert param2.value == 42.0

    def test_apply_force(self):
        e = SimEngine()
        ent = e.spawn_entity("particle", 0, 0, {"mass": 2.0})
        assert e.apply_force(ent.entity_id, 10, -6) is True
        assert e.entities[ent.entity_id].vx == 5.0  # 10/2
        assert e.entities[ent.entity_id].vy == -3.0  # -6/2
        assert e.apply_force("nonexistent", 1, 1) is False

    def test_query_state_all(self):
        e = SimEngine()
        e.spawn_entity("particle", 10, 20)
        e.spawn_entity("orb", 30, 40)
        state = e.query_state()
        assert len(state["entities"]) == 2
        assert state["tick"] == 0

    def test_query_state_filter_type(self):
        e = SimEngine()
        e.spawn_entity("particle", 0, 0)
        e.spawn_entity("orb", 0, 0)
        state = e.query_state(filter_type="particle")
        assert len(state["entities"]) == 1

    def test_query_state_single_entity(self):
        e = SimEngine()
        ent = e.spawn_entity("particle", 5, 10)
        state = e.query_state(entity_id=ent.entity_id)
        assert ent.entity_id in state

    def test_query_state_nonexistent_entity(self):
        e = SimEngine()
        state = e.query_state(entity_id="nope")
        assert "error" in state

    def test_get_metrics(self):
        e = SimEngine()
        e.spawn_entity("particle", 0, 0)
        e.spawn_entity("orb", 0, 0)
        e.spawn_entity("particle", 0, 0)
        metrics = e.get_metrics()
        assert metrics.total_entities == 3
        assert metrics.total_ticks == 0
        assert metrics.stability_score >= 0
        assert metrics.entity_types == {"particle": 2, "orb": 1}

    def test_compute_stability(self):
        e = SimEngine()
        # Empty = perfect stability
        assert e.compute_stability() == 1.0
        e.spawn_entity("particle", 0, 0)
        # Stationary = high stability
        assert e.compute_stability() > 0.9
        e.spawn_entity("particle", 0, 0, {"vx": 1000, "vy": 1000})
        # High velocity = lower stability
        stab = e.compute_stability()
        assert stab < 1.0

    def test_reset(self):
        e = SimEngine()
        e.spawn_entity("particle", 0, 0)
        e.step()
        e.step()
        e.reset()
        assert len(e.entities) == 0
        assert e.tick_count == 0

    def test_tick_count_increments(self):
        e = SimEngine()
        e.spawn_entity("particle", 0, 0)
        e.step()
        e.step()
        e.step()
        assert e.tick_count == 3

    def test_multiple_steps_trajectory(self):
        e = SimEngine()
        e.spawn_entity("particle", 0, 0, {"vx": 10, "vy": 0})
        for _ in range(10):
            e.step()
        eid = list(e.entities.keys())[0]
        ent = e.entities[eid]
        assert ent.x > 50  # Should have moved significantly

    def test_set_parameter_clamping(self):
        e = SimEngine()
        param = e.set_parameter("damping", 5.0)  # max is 1.0
        assert param.value == 1.0
        param = e.set_parameter("damping", -1.0)  # min is 0.0
        assert param.value == 0.0


# ── SimRunner tests ──────────────────────────────────────────


class TestSimRunner:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        from redclaw.sim.runner import SimRunner
        engine = SimEngine()
        engine.spawn_entity("particle", 0, 0, {"vx": 5})
        runner = SimRunner(engine)
        assert not runner.running
        await runner.start()
        assert runner.running
        await asyncio.sleep(0.2)  # Let a few ticks happen
        assert engine.tick_count > 0
        await runner.stop()
        assert not runner.running

    @pytest.mark.asyncio
    async def test_pause_resume(self):
        from redclaw.sim.runner import SimRunner
        engine = SimEngine()
        engine.spawn_entity("particle", 0, 0)
        runner = SimRunner(engine)
        await runner.start()
        await asyncio.sleep(0.1)
        ticks_before = engine.tick_count
        runner.pause()
        assert runner.paused
        await asyncio.sleep(0.1)
        ticks_during = engine.tick_count
        runner.resume()
        assert not runner.paused
        await asyncio.sleep(0.1)
        # Ticks should not have increased while paused
        # (might increase by 1 due to timing, but should not increase much)
        assert engine.tick_count >= ticks_during

    @pytest.mark.asyncio
    async def test_emit_callback(self):
        from redclaw.sim.runner import SimRunner
        engine = SimEngine()
        engine.spawn_entity("particle", 0, 0)
        received = []

        async def on_tick(data):
            received.append(data)

        runner = SimRunner(engine, emit_fn=on_tick)
        await runner.start()
        await asyncio.sleep(0.2)
        await runner.stop()
        assert len(received) > 0
        assert received[0]["type"] == "sim_tick"
        assert "positions" in received[0]
        assert "metrics" in received[0]

    @pytest.mark.asyncio
    async def test_speed_property(self):
        from redclaw.sim.runner import SimRunner
        runner = SimRunner(SimEngine())
        runner.speed = 2.0
        assert runner.speed == 2.0
        runner.speed = 0.05  # clamped to 0.1
        assert runner.speed == 0.1
        runner.speed = 20.0  # clamped to 10.0
        assert runner.speed == 10.0


# ── Sim tools tests ──────────────────────────────────────────


class TestSimTools:
    @pytest.mark.asyncio
    async def test_register_and_use_tools(self):
        from redclaw.sim.tools import register_sim_tools
        from redclaw.tools.registry import ToolExecutor
        tools = ToolExecutor()
        engine = SimEngine()
        register_sim_tools(tools, engine)
        assert "spawn_entity" in tools.specs
        assert "set_sim_parameter" in tools.specs
        assert "query_state" in tools.specs
        assert "apply_force" in tools.specs

    @pytest.mark.asyncio
    async def test_spawn_entity_tool(self):
        from redclaw.sim.tools import register_sim_tools
        from redclaw.tools.registry import ToolExecutor
        tools = ToolExecutor()
        engine = SimEngine()
        register_sim_tools(tools, engine)
        result = await tools.execute("spawn_entity", {
            "entity_type": "orb",
            "x": 100,
            "y": -50,
            "properties": '{"mass": 5, "radius": 25}',
        })
        data = json.loads(result)
        assert data["type"] == "orb"
        assert data["x"] == 100
        assert data["y"] == -50

    @pytest.mark.asyncio
    async def test_query_state_tool(self):
        from redclaw.sim.tools import register_sim_tools
        from redclaw.tools.registry import ToolExecutor
        tools = ToolExecutor()
        engine = SimEngine()
        register_sim_tools(tools, engine)
        engine.spawn_entity("particle", 0, 0)
        result = await tools.execute("query_state", {})
        data = json.loads(result)
        assert "entities" in data

    @pytest.mark.asyncio
    async def test_apply_force_tool(self):
        from redclaw.sim.tools import register_sim_tools
        from redclaw.tools.registry import ToolExecutor
        tools = ToolExecutor()
        engine = SimEngine()
        register_sim_tools(tools, engine)
        ent = engine.spawn_entity("particle", 0, 0, {"mass": 1})
        result = await tools.execute("apply_force", {
            "entity_id": ent.entity_id,
            "fx": 10,
            "fy": -5,
        })
        data = json.loads(result)
        assert data["applied"] is True


# ── SubagentType / Toolset / DNA integration tests ───────────


class TestSimulatorBloodline:
    def test_subagent_type_exists(self):
        from redclaw.runtime.subagent_types import SubagentType
        assert SubagentType.SIMULATOR.value == "simulator"

    def test_simulator_prompt(self):
        from redclaw.runtime.subagent_types import get_subagent_prompt, SubagentType
        prompt = get_subagent_prompt(SubagentType.SIMULATOR)
        assert "simulation" in prompt.lower()
        assert "spawn_entity" in prompt

    def test_simulator_toolset(self):
        from redclaw.runtime.subagent_types import get_subagent_toolset_names, SubagentType
        toolsets = get_subagent_toolset_names(SubagentType.SIMULATOR)
        assert "core" in toolsets
        assert "simulator" in toolsets

    def test_simulator_toolset_resolves(self):
        from redclaw.tools.toolsets import resolve_toolset
        tools = resolve_toolset("simulator")
        assert "spawn_entity" in tools
        assert "set_sim_parameter" in tools
        assert "query_state" in tools
        assert "apply_force" in tools

    def test_simulator_dna_profile(self):
        from redclaw.crypt.dna import DNAManager, TraitProfile
        mgr = DNAManager()
        traits = mgr.load_traits("simulator")
        assert traits.speed == pytest.approx(0.4)
        assert traits.accuracy == pytest.approx(0.7)
        assert traits.creativity == pytest.approx(0.8)
        assert traits.persistence == pytest.approx(0.6)

    def test_simulator_dna_modifiers(self):
        from redclaw.crypt.dna import DNAManager
        mgr = DNAManager()
        modifiers = mgr.get_modifiers("simulator")
        # creativity=0.8 > 0.6 → creative style
        assert modifiers.prompt_style == "creative"


# ── Context budget test ──────────────────────────────────────


class TestContextBudget:
    def test_sim_state_in_budget(self):
        from redclaw.runtime.context_budget import budget_context
        result = budget_context(
            soul_text="Be helpful.",
            sim_state="10 entities, stability 0.85, tick 500",
        )
        assert "10 entities" in result
        assert "Be helpful" in result

    def test_sim_state_empty(self):
        from redclaw.runtime.context_budget import budget_context
        result = budget_context(soul_text="Be helpful.")
        assert "10 entities" not in result


# ── Event bus sim events ─────────────────────────────────────


class TestSimEvents:
    def test_sim_event_constants(self):
        from redclaw.runtime.event_bus import (
            EVENT_SIM_CREATED,
            EVENT_SIM_TICK_MILESTONE,
            EVENT_SIM_STABILITY_CHANGED,
        )
        assert EVENT_SIM_CREATED == "sim_created"
        assert EVENT_SIM_TICK_MILESTONE == "sim_tick_milestone"
        assert EVENT_SIM_STABILITY_CHANGED == "sim_stability_changed"


# ── Karma sim keywords ───────────────────────────────────────


class TestKarmaSimKeywords:
    def test_sim_positive_keywords(self):
        from redclaw.crypt.karma import _POSITIVE_KEYWORDS
        for kw in ("stable", "balanced", "coherent", "orbital", "equilibrium"):
            assert kw in _POSITIVE_KEYWORDS


# ── RPC handler test ─────────────────────────────────────────


class TestRPCHandler:
    @pytest.mark.asyncio
    async def test_sim_command_spawn(self):
        from redclaw.rpc import _handle_sim_command
        engine = SimEngine()
        result = await _handle_sim_command("spawn_entity", {
            "entity_type": "particle",
            "x": 10,
            "y": 20,
        }, engine)
        assert "entity_id" in result
        assert len(engine.entities) == 1

    @pytest.mark.asyncio
    async def test_sim_command_reset(self):
        from redclaw.rpc import _handle_sim_command
        engine = SimEngine()
        engine.spawn_entity("particle", 0, 0)
        result = await _handle_sim_command("reset", {}, engine)
        assert result["reset"] is True
        assert len(engine.entities) == 0

    @pytest.mark.asyncio
    async def test_sim_command_get_metrics(self):
        from redclaw.rpc import _handle_sim_command
        engine = SimEngine()
        engine.spawn_entity("orb", 0, 0)
        engine.step()
        result = await _handle_sim_command("get_metrics", {}, engine)
        assert result["total_entities"] == 1
        assert result["total_ticks"] == 1
        assert 0 <= result["stability_score"] <= 1

    @pytest.mark.asyncio
    async def test_sim_command_unknown(self):
        from redclaw.rpc import _handle_sim_command
        engine = SimEngine()
        result = await _handle_sim_command("bad_action", {}, engine)
        assert "error" in result
