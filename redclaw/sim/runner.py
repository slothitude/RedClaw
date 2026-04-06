"""SimRunner — async wrapper that ticks SimEngine at ~30fps.

Emits sim_tick events via a callback for downstream consumers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable

from redclaw.sim.engine import SimEngine

logger = logging.getLogger(__name__)

# Callback type: receives tick data dict
TickCallback = Callable[[dict[str, Any]], Awaitable[None]]


class SimRunner:
    """Async tick loop wrapper around SimEngine."""

    def __init__(self, engine: SimEngine, emit_fn: TickCallback | None = None) -> None:
        self._engine = engine
        self._emit_fn = emit_fn
        self._task: asyncio.Task | None = None
        self._running = False
        self._paused = False
        self._speed: float = 1.0  # multiplier

    @property
    def engine(self) -> SimEngine:
        return self._engine

    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = max(0.1, min(10.0, value))

    def set_emit_fn(self, fn: TickCallback) -> None:
        self._emit_fn = fn

    async def start(self) -> None:
        """Start the tick loop as a background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("SimRunner started")

    async def stop(self) -> None:
        """Stop the tick loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("SimRunner stopped")

    def pause(self) -> None:
        self._paused = True
        logger.info("SimRunner paused")

    def resume(self) -> None:
        self._paused = False
        logger.info("SimRunner resumed")

    async def _tick_loop(self) -> None:
        """Main loop: tick engine, emit events, sleep."""
        while self._running:
            if not self._paused:
                positions = self._engine.step()
                if self._emit_fn and positions:
                    tick_data = {
                        "type": "sim_tick",
                        "tick": self._engine.tick_count,
                        "positions": positions,
                        "metrics": {
                            "total_entities": len(self._engine.entities),
                            "stability": self._engine.compute_stability(),
                        },
                    }
                    try:
                        await self._emit_fn(tick_data)
                    except Exception as e:
                        logger.warning("Tick emit error: %s", e)

            # Sleep based on tick rate and speed
            tick_rate = self._engine.get_parameter("tick_rate")
            rate = tick_rate.value if tick_rate else 30.0
            interval = 1.0 / (rate * self._speed)
            await asyncio.sleep(interval)

    def get_state_snapshot(self) -> dict[str, Any]:
        """Get a full state snapshot for RPC queries."""
        return {
            "running": self._running,
            "paused": self._paused,
            "speed": self._speed,
            "engine": self._engine.query_state(),
        }
