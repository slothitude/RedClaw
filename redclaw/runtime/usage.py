"""Token usage tracking and cost estimation."""

from __future__ import annotations

from dataclasses import dataclass, field

from redclaw.api.types import Usage


# Rough cost per 1M tokens (USD) — defaults, varies by model
DEFAULT_COSTS = {
    "input": 3.0,
    "output": 15.0,
    "cache_write": 3.75,
    "cache_read": 0.30,
}


@dataclass
class UsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    turns: int = 0


class UsageTracker:
    """Accumulates token usage across a session."""

    def __init__(self, costs: dict[str, float] | None = None) -> None:
        self.costs = costs or DEFAULT_COSTS
        self.total = UsageSnapshot()

    def record(self, usage: Usage) -> None:
        """Record usage from a stream event."""
        self.total.input_tokens += usage.input_tokens
        self.total.output_tokens += usage.output_tokens
        self.total.cache_creation_tokens += usage.cache_creation_input_tokens
        self.total.cache_read_tokens += usage.cache_read_input_tokens

    def increment_turn(self) -> None:
        self.total.turns += 1

    def estimated_cost(self) -> float:
        """Estimate total cost in USD."""
        c = self.costs
        cost = (
            self.total.input_tokens * c["input"] / 1_000_000
            + self.total.output_tokens * c["output"] / 1_000_000
            + self.total.cache_creation_tokens * c["cache_write"] / 1_000_000
            + self.total.cache_read_tokens * c["cache_read"] / 1_000_000
        )
        return cost

    def summary(self) -> str:
        """Return a human-readable usage summary."""
        return (
            f"Tokens: {self.total.input_tokens:,} in / {self.total.output_tokens:,} out "
            f"| Turns: {self.total.turns} "
            f"| Est. cost: ${self.estimated_cost():.4f}"
        )
