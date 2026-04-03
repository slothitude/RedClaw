"""Base class for RedClaw skills."""

from __future__ import annotations

import abc
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class SkillTool:
    """A tool provided by a skill."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    execute: Callable[..., Awaitable[str]]


@dataclass
class SkillManifest:
    """Parsed skill.yaml or SKILL.md manifest."""
    name: str
    description: str
    version: str = "1.0"
    tools: list[dict[str, Any]] = field(default_factory=list)
    instructions: str = ""
    # Usage tracking fields
    usage_count: int = 0
    success_count: int = 0
    last_used: str | None = None


def load_skill_metrics(skill_dir: str | Path) -> dict[str, Any]:
    """Load .metrics.json for a skill. Returns empty dict if not found."""
    metrics_path = Path(skill_dir) / ".metrics.json"
    if metrics_path.is_file():
        try:
            return json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_skill_metrics(skill_dir: str | Path, metrics: dict[str, Any]) -> None:
    """Persist metrics to .metrics.json atomically."""
    skill_dir = Path(skill_dir)
    metrics_path = skill_dir / ".metrics.json"
    fd, tmp_path = tempfile.mkstemp(dir=str(skill_dir), prefix=".metrics_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)
        os.replace(tmp_path, metrics_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def record_skill_usage(skill_dir: str | Path, success: bool) -> None:
    """Record a skill usage event. Creates/updates .metrics.json."""
    metrics = load_skill_metrics(skill_dir)
    metrics["usage_count"] = metrics.get("usage_count", 0) + 1
    if success:
        metrics["success_count"] = metrics.get("success_count", 0) + 1
    metrics["last_used"] = datetime.now(timezone.utc).isoformat()
    save_skill_metrics(skill_dir, metrics)
    logger.debug("Recorded skill usage in %s: success=%s", skill_dir, success)


class SkillBase(abc.ABC):
    """Abstract base class for skills."""

    def __init__(self, manifest: SkillManifest, skill_dir: str) -> None:
        self.manifest = manifest
        self.skill_dir = skill_dir
        self._tools: list[SkillTool] = []

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def description(self) -> str:
        return self.manifest.description

    @property
    def tools(self) -> list[SkillTool]:
        return self._tools

    def add_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        execute: Callable[..., Awaitable[str]],
    ) -> None:
        self._tools.append(SkillTool(
            name=name,
            description=description,
            parameters=parameters,
            execute=execute,
        ))

    async def setup(self) -> None:
        """Override to perform async initialization."""
        pass
