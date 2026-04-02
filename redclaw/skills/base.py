"""Base class for RedClaw skills."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class SkillTool:
    """A tool provided by a skill."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    execute: Callable[..., Awaitable[str]]


@dataclass
class SkillManifest:
    """Parsed skill.yaml manifest."""
    name: str
    description: str
    version: str = "1.0"
    tools: list[dict[str, Any]] = field(default_factory=list)


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
