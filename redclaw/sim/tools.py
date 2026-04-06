"""Tool registration for simulation tools.

Registers spawn_entity, set_sim_parameter, query_state, apply_force
as agent-callable ToolSpecs.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from redclaw.api.types import PermissionLevel
from redclaw.tools.registry import ToolSpec

if TYPE_CHECKING:
    from redclaw.sim.engine import SimEngine
    from redclaw.tools.registry import ToolExecutor


def register_sim_tools(tools: ToolExecutor, engine: SimEngine) -> None:
    """Register simulation tool specs with the tool executor."""

    async def _spawn_entity(
        entity_type: str = "particle",
        x: float = 0.0,
        y: float = 0.0,
        properties: str = "{}",
    ) -> str:
        try:
            props = json.loads(properties) if isinstance(properties, str) else properties
        except json.JSONDecodeError:
            props = {}
        entity = engine.spawn_entity(entity_type, float(x), float(y), props)
        return json.dumps({
            "entity_id": entity.entity_id,
            "type": entity.entity_type,
            "x": entity.x,
            "y": entity.y,
            "mass": entity.mass,
            "radius": entity.radius,
        })

    async def _set_sim_parameter(name: str = "", value: float = 0.0) -> str:
        param = engine.set_parameter(name, float(value))
        return json.dumps({
            "name": param.name,
            "value": param.value,
            "min": param.min_val,
            "max": param.max_val,
            "description": param.description,
        })

    async def _query_state(
        filter_type: str = "",
        entity_id: str = "",
    ) -> str:
        ft = filter_type if filter_type else None
        eid = entity_id if entity_id else None
        return json.dumps(engine.query_state(filter_type=ft, entity_id=eid))

    async def _apply_force(entity_id: str = "", fx: float = 0.0, fy: float = 0.0) -> str:
        ok = engine.apply_force(entity_id, float(fx), float(fy))
        if ok:
            entity = engine.entities.get(entity_id)
            return json.dumps({"applied": True, "entity_id": entity_id, "new_vx": entity.vx, "new_vy": entity.vy})
        return json.dumps({"applied": False, "error": f"Entity '{entity_id}' not found"})

    tools.register_tool(ToolSpec(
        name="spawn_entity",
        description="Spawn an entity in the simulation world. Types: particle, orb, field, constraint.",
        input_schema={
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "description": "Entity type: particle, orb, field, constraint", "default": "particle"},
                "x": {"type": "number", "description": "X position", "default": 0.0},
                "y": {"type": "number", "description": "Y position", "default": 0.0},
                "properties": {"type": "string", "description": "JSON object with optional: mass, radius, vx, vy, entity_id", "default": "{}"},
            },
            "required": ["entity_type"],
        },
        permission=PermissionLevel.WORKSPACE_WRITE,
        execute=_spawn_entity,
    ))

    tools.register_tool(ToolSpec(
        name="set_sim_parameter",
        description="Set a simulation parameter. Built-in: gravity, damping, bounds_restitution, tick_rate.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Parameter name"},
                "value": {"type": "number", "description": "Parameter value"},
            },
            "required": ["name", "value"],
        },
        permission=PermissionLevel.WORKSPACE_WRITE,
        execute=_set_sim_parameter,
    ))

    tools.register_tool(ToolSpec(
        name="query_state",
        description="Query simulation state. Filter by entity type or get a specific entity.",
        input_schema={
            "type": "object",
            "properties": {
                "filter_type": {"type": "string", "description": "Filter by entity type (empty = all)"},
                "entity_id": {"type": "string", "description": "Get specific entity by ID"},
            },
        },
        permission=PermissionLevel.READ_ONLY,
        execute=_query_state,
    ))

    tools.register_tool(ToolSpec(
        name="apply_force",
        description="Apply an instantaneous force to an entity. Modifies velocity by fx/mass, fy/mass.",
        input_schema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity to apply force to"},
                "fx": {"type": "number", "description": "Force in X direction"},
                "fy": {"type": "number", "description": "Force in Y direction"},
            },
            "required": ["entity_id", "fx", "fy"],
        },
        permission=PermissionLevel.WORKSPACE_WRITE,
        execute=_apply_force,
    ))
