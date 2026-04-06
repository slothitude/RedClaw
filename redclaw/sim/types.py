"""Data structures for the simulation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SimEntity:
    """An entity in the simulation world."""
    entity_id: str
    entity_type: str  # particle, orb, field, constraint
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    mass: float = 1.0
    radius: float = 10.0
    properties: dict = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class SimParameter:
    """A tunable simulation parameter."""
    name: str
    value: float
    min_val: float = 0.0
    max_val: float = 100.0
    description: str = ""


@dataclass
class SimMetrics:
    """Aggregate simulation metrics."""
    total_entities: int = 0
    total_ticks: int = 0
    stability_score: float = 0.0
    entity_types: dict = field(default_factory=dict)
