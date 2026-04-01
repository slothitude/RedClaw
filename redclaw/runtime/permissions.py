"""Permission system for tool execution."""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass

from redclaw.api.types import PermissionLevel


class PermissionMode(Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL_ACCESS = "danger_full_access"
    # Ask every time
    ASK = "ask"


# Hierarchy: READ_ONLY < WORKSPACE_WRITE < DANGER_FULL_ACCESS
_LEVEL_ORDER = {
    PermissionLevel.READ_ONLY: 0,
    PermissionLevel.WORKSPACE_WRITE: 1,
    PermissionLevel.DANGER_FULL_ACCESS: 2,
}

_MODE_LEVEL = {
    PermissionMode.READ_ONLY: PermissionLevel.READ_ONLY,
    PermissionMode.WORKSPACE_WRITE: PermissionLevel.WORKSPACE_WRITE,
    PermissionMode.DANGER_FULL_ACCESS: PermissionLevel.DANGER_FULL_ACCESS,
}


@dataclass
class PermissionPolicy:
    mode: PermissionMode = PermissionMode.ASK

    def authorize(self, tool_name: str, tool_level: PermissionLevel) -> tuple[bool, str]:
        """Check if a tool call is authorized.

        Returns (allowed, reason).
        """
        if self.mode == PermissionMode.ASK:
            # In ASK mode, we allow but signal that confirmation is needed
            return True, "ask"

        allowed_level = _MODE_LEVEL[self.mode]
        if _LEVEL_ORDER[tool_level] <= _LEVEL_ORDER[allowed_level]:
            return True, "allowed"

        return False, f"Tool '{tool_name}' requires {tool_level.value} permission (current mode: {self.mode.value})"
