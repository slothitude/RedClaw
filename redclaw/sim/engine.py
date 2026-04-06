"""SimEngine — synchronous, pure-math 2D physics engine.

Euler integration with damping and boundary bounce.
All coordinates in world units (float). No rendering here.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from redclaw.sim.types import SimEntity, SimParameter, SimMetrics

logger = logging.getLogger(__name__)

# World boundaries
_DEFAULT_BOUNDS = (-500.0, -500.0, 500.0, 500.0)  # min_x, min_y, max_x, max_y


class SimEngine:
    """Pure-math simulation engine. No I/O, no rendering."""

    def __init__(self) -> None:
        self._entities: dict[str, SimEntity] = {}
        self._parameters: dict[str, SimParameter] = {
            "gravity": SimParameter("gravity", 0.0, -10.0, 10.0, "Downward acceleration per tick"),
            "damping": SimParameter("damping", 0.98, 0.0, 1.0, "Velocity multiplier per tick (1.0 = no friction)"),
            "bounds_restitution": SimParameter("bounds_restitution", 0.8, 0.0, 1.0, "Bounciness on boundary collision"),
            "tick_rate": SimParameter("tick_rate", 30.0, 1.0, 120.0, "Target ticks per second"),
        }
        self._bounds = _DEFAULT_BOUNDS
        self._tick_count: int = 0

    def spawn_entity(
        self,
        entity_type: str,
        x: float,
        y: float,
        properties: dict | None = None,
    ) -> SimEntity:
        """Create a new entity in the simulation."""
        entity_id = properties.pop("entity_id", None) if properties else None
        entity_id = entity_id or f"e-{uuid.uuid4().hex[:6]}"

        mass = (properties or {}).get("mass", 1.0)
        radius = (properties or {}).get("radius", 10.0)
        vx = (properties or {}).get("vx", 0.0)
        vy = (properties or {}).get("vy", 0.0)

        entity = SimEntity(
            entity_id=entity_id,
            entity_type=entity_type,
            x=float(x),
            y=float(y),
            vx=float(vx),
            vy=float(vy),
            mass=float(mass),
            radius=float(radius),
            properties=properties or {},
        )
        self._entities[entity_id] = entity
        logger.info("Spawned %s '%s' at (%.1f, %.1f)", entity_type, entity_id, x, y)
        return entity

    def remove_entity(self, entity_id: str) -> bool:
        """Remove an entity. Returns True if found."""
        if entity_id in self._entities:
            del self._entities[entity_id]
            logger.info("Removed entity '%s'", entity_id)
            return True
        return False

    def set_parameter(self, name: str, value: float) -> SimParameter:
        """Set a simulation parameter."""
        if name in self._parameters:
            param = self._parameters[name]
            param.value = max(param.min_val, min(param.max_val, float(value)))
            return param
        # Allow custom parameters
        param = SimParameter(name=name, value=float(value))
        self._parameters[name] = param
        return param

    def get_parameter(self, name: str) -> SimParameter | None:
        return self._parameters.get(name)

    def apply_force(self, entity_id: str, fx: float, fy: float) -> bool:
        """Apply an instantaneous force to an entity (modifies velocity)."""
        entity = self._entities.get(entity_id)
        if not entity:
            return False
        # F = ma → a = F/m
        entity.vx += fx / entity.mass
        entity.vy += fy / entity.mass
        return True

    def step(self) -> dict[str, dict[str, float]]:
        """Advance one tick. Returns {entity_id: {x, y, vx, vy}}."""
        gravity = self._parameters["gravity"].value
        damping = self._parameters["damping"].value
        restitution = self._parameters["bounds_restitution"].value
        min_x, min_y, max_x, max_y = self._bounds

        positions: dict[str, dict[str, float]] = {}

        for entity in self._entities.values():
            # Apply gravity (downward = positive y in screen coords)
            entity.vy += gravity

            # Apply damping
            entity.vx *= damping
            entity.vy *= damping

            # Euler integration
            entity.x += entity.vx
            entity.y += entity.vy

            # Boundary bounce
            r = entity.radius
            if entity.x - r < min_x:
                entity.x = min_x + r
                entity.vx = abs(entity.vx) * restitution
            elif entity.x + r > max_x:
                entity.x = max_x - r
                entity.vx = -abs(entity.vx) * restitution

            if entity.y - r < min_y:
                entity.y = min_y + r
                entity.vy = abs(entity.vy) * restitution
            elif entity.y + r > max_y:
                entity.y = max_y - r
                entity.vy = -abs(entity.vy) * restitution

            positions[entity.entity_id] = {
                "x": entity.x,
                "y": entity.y,
                "vx": entity.vx,
                "vy": entity.vy,
            }

        self._tick_count += 1
        return positions

    def query_state(
        self,
        filter_type: str | None = None,
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        """Query entity state."""
        if entity_id:
            entity = self._entities.get(entity_id)
            if not entity:
                return {"error": f"Entity '{entity_id}' not found"}
            return {
                entity.entity_id: {
                    "type": entity.entity_type,
                    "x": entity.x,
                    "y": entity.y,
                    "vx": entity.vx,
                    "vy": entity.vy,
                    "mass": entity.mass,
                    "radius": entity.radius,
                    "properties": entity.properties,
                }
            }

        entities = self._entities.values()
        if filter_type:
            entities = [e for e in entities if e.entity_type == filter_type]

        return {
            "entities": {
                e.entity_id: {
                    "type": e.entity_type,
                    "x": e.x,
                    "y": e.y,
                    "vx": e.vx,
                    "vy": e.vy,
                    "mass": e.mass,
                    "radius": e.radius,
                    "properties": e.properties,
                }
                for e in entities
            },
            "tick": self._tick_count,
            "parameters": {n: p.value for n, p in self._parameters.items()},
        }

    def get_metrics(self) -> SimMetrics:
        """Compute aggregate metrics."""
        type_counts: dict[str, int] = {}
        for e in self._entities.values():
            type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1

        return SimMetrics(
            total_entities=len(self._entities),
            total_ticks=self._tick_count,
            stability_score=self.compute_stability(),
            entity_types=type_counts,
        )

    def compute_stability(self) -> float:
        """Compute a 0-1 stability score based on bounds ratio and avg velocity."""
        if not self._entities:
            return 1.0

        min_x, min_y, max_x, max_y = self._bounds
        world_area = (max_x - min_x) * (max_y - min_y)

        total_vel = 0.0
        for e in self._entities.values():
            total_vel += (e.vx ** 2 + e.vy ** 2) ** 0.5

        avg_vel = total_vel / len(self._entities)

        # Stability: high when velocity is low, entities spread out
        vel_score = 1.0 / (1.0 + avg_vel * 0.01)
        return max(0.0, min(1.0, vel_score))

    def reset(self) -> None:
        """Clear all entities and reset tick counter."""
        self._entities.clear()
        self._tick_count = 0
        logger.info("Simulation reset")

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def entities(self) -> dict[str, SimEntity]:
        return dict(self._entities)
