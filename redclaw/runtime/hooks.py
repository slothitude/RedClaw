"""Hook runner — execute pre/post tool shell hooks."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HookConfig:
    pre_tool: list[str] = field(default_factory=list)   # shell commands
    post_tool: list[str] = field(default_factory=list)   # shell commands


class HookRunner:
    """Runs pre/post tool hooks as subprocesses."""

    def __init__(self, config: HookConfig | None = None) -> None:
        self.config = config or HookConfig()

    async def run_pre_tool(self, tool_name: str, tool_input: dict[str, Any]) -> bool:
        """Run pre-tool hooks. Returns False to block the tool call."""
        for cmd in self.config.pre_tool:
            env_vars = {
                "REDCLAW_TOOL": tool_name,
                "REDCLAW_TOOL_INPUT": str(tool_input),
            }
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**__import__("os").environ, **env_vars},
                )
                await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode != 0:
                    logger.info("Pre-hook blocked %s: %s", tool_name, cmd)
                    return False
            except Exception as exc:
                logger.warning("Pre-hook error: %s", exc)
        return True

    async def run_post_tool(self, tool_name: str, result: str) -> None:
        """Run post-tool hooks."""
        for cmd in self.config.post_tool:
            try:
                env_vars = {
                    "REDCLAW_TOOL": tool_name,
                    "REDCLAW_RESULT": result[:4096],
                }
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**__import__("os").environ, **env_vars},
                )
                await asyncio.wait_for(proc.communicate(), timeout=30)
            except Exception as exc:
                logger.warning("Post-hook error: %s", exc)
