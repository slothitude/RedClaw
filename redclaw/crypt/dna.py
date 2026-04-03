"""DNA Trait System — evolving bloodline traits based on subagent outcomes.

Traits influence subagent behavior: timeout, max_turns, prompt style.
Traits evolve via weighted moving average (alpha=0.3) after each entombment.
"""

from __future__ import annotations

import json
import logging
import tempfile
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redclaw.runtime.subagent_types import SubagentType

logger = logging.getLogger(__name__)


@dataclass
class TraitProfile:
    """Evolving trait values for a bloodline."""
    speed: float = 0.5
    accuracy: float = 0.5
    creativity: float = 0.5
    persistence: float = 0.5
    generation: int = 0

    def clamp(self) -> TraitProfile:
        """Clamp all values to [0.0, 1.0]."""
        self.speed = max(0.0, min(1.0, self.speed))
        self.accuracy = max(0.0, min(1.0, self.accuracy))
        self.creativity = max(0.0, min(1.0, self.creativity))
        self.persistence = max(0.0, min(1.0, self.persistence))
        return self


@dataclass
class TraitModifiers:
    """Concrete runtime parameters derived from traits."""
    timeout_multiplier: float = 1.0
    max_turns_modifier: int = 0  # additive
    prompt_style: str = "balanced"  # cautious, balanced, aggressive, creative


# Default profiles per bloodline type
_DEFAULT_PROFILES: dict[str, TraitProfile] = {
    "coder": TraitProfile(speed=0.3, accuracy=0.8, creativity=0.3, persistence=0.7),
    "searcher": TraitProfile(speed=0.8, accuracy=0.5, creativity=0.4, persistence=0.4),
    "general": TraitProfile(speed=0.5, accuracy=0.5, creativity=0.5, persistence=0.5),
}


class DNAManager:
    """Manages evolving trait profiles per bloodline."""

    def __init__(self, dna_dir: Path | None = None) -> None:
        self._dir = dna_dir or Path.home() / ".redclaw" / "crypt" / "dna"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, TraitProfile] = {}

    def load_traits(self, bloodline: str | SubagentType) -> TraitProfile:
        """Load traits for a bloodline, falling back to defaults."""
        name = bloodline.value if hasattr(bloodline, "value") else bloodline
        if name in self._cache:
            return self._cache[name]

        path = self._dir / f"{name}.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                profile = TraitProfile(
                    speed=data.get("speed", 0.5),
                    accuracy=data.get("accuracy", 0.5),
                    creativity=data.get("creativity", 0.5),
                    persistence=data.get("persistence", 0.5),
                    generation=data.get("generation", 0),
                )
                self._cache[name] = profile
                return profile
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load DNA for %s: %s", name, e)

        default = _DEFAULT_PROFILES.get(name, TraitProfile())
        self._cache[name] = default
        return default

    def evolve(
        self,
        bloodline: str | SubagentType,
        success_rate: float,
        avg_tool_calls: float,
        timeout_rate: float,
    ) -> TraitProfile:
        """Evolve traits based on recent performance metrics.

        Uses weighted moving average with alpha=0.3.
        """
        name = bloodline.value if hasattr(bloodline, "value") else bloodline
        current = self.load_traits(name)
        alpha = 0.3

        # Speed: increase if low tool calls + high success, decrease if timeouts
        speed_signal = (1.0 - min(avg_tool_calls / 8.0, 1.0)) * success_rate - timeout_rate * 0.5
        current.speed = current.speed * (1 - alpha) + max(0.0, speed_signal) * alpha

        # Accuracy: increase on success, decrease on failure
        current.accuracy = current.accuracy * (1 - alpha) + success_rate * alpha

        # Creativity: slightly increase on success with high tool calls (novel solutions)
        creativity_signal = success_rate * min(avg_tool_calls / 5.0, 1.0)
        current.creativity = current.creativity * (1 - alpha) + creativity_signal * alpha

        # Persistence: increase when tasks succeed despite many retries
        current.persistence = current.persistence * (1 - alpha) + (1.0 - timeout_rate) * alpha

        current.generation += 1
        current.clamp()

        self._save(name, current)
        self._cache[name] = current
        return current

    def get_modifiers(self, bloodline: str | SubagentType) -> TraitModifiers:
        """Convert traits to concrete runtime parameters."""
        name = bloodline.value if hasattr(bloodline, "value") else bloodline
        t = self.load_traits(name)

        # Timeout multiplier: high persistence → longer timeout
        timeout_mult = 0.7 + t.persistence * 0.6  # range [0.7, 1.3]

        # Max turns modifier: high accuracy → fewer turns needed
        turns_mod = int((t.speed - 0.5) * 4)  # range [-2, 2]

        # Prompt style
        if t.accuracy > 0.7:
            style = "cautious"
        elif t.speed > 0.7:
            style = "aggressive"
        elif t.creativity > 0.6:
            style = "creative"
        else:
            style = "balanced"

        return TraitModifiers(
            timeout_multiplier=timeout_mult,
            max_turns_modifier=turns_mod,
            prompt_style=style,
        )

    def get_prompt_guidance(self, bloodline: str | SubagentType) -> str:
        """Get a brief prompt hint based on trait style."""
        modifiers = self.get_modifiers(bloodline)
        guidance_map = {
            "cautious": "Be extra careful. Read files fully before editing. Verify each change.",
            "balanced": "",
            "aggressive": "Work quickly. Focus on the most direct solution.",
            "creative": "Consider alternative approaches. Think outside the box.",
        }
        return guidance_map.get(modifiers.prompt_style, "")

    def _save(self, name: str, profile: TraitProfile) -> None:
        """Persist a trait profile to disk atomically."""
        path = self._dir / f"{name}.json"
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), prefix=".dna_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(profile), f, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
