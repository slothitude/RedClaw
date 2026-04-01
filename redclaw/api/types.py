"""Core data types for the RedClaw agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Permissions ──────────────────────────────────────────────


class PermissionLevel(Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL_ACCESS = "danger_full_access"


# ── Roles ────────────────────────────────────────────────────


class Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"  # tool_result messages


# ── Content Blocks ───────────────────────────────────────────


@dataclass
class TextBlock:
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TextBlock:
        return cls(text=d["text"])


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolUseBlock:
        return cls(id=d["id"], name=d["name"], input=d.get("input", {}))


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolResultBlock:
        return cls(
            tool_use_id=d["tool_use_id"],
            content=d.get("content", ""),
            is_error=d.get("is_error", False),
        )


# Union type
ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


def parse_content_block(d: dict[str, Any]) -> ContentBlock:
    t = d.get("type", "text")
    if t == "tool_use":
        return ToolUseBlock.from_dict(d)
    if t == "tool_result":
        return ToolResultBlock.from_dict(d)
    return TextBlock.from_dict(d)


# ── Messages ─────────────────────────────────────────────────


@dataclass
class InputMessage:
    role: Role
    content: list[ContentBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "content": [b.to_dict() for b in self.content],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InputMessage:
        return cls(
            role=Role(d["role"]),
            content=[parse_content_block(b) for b in d.get("content", [])],
        )

    @classmethod
    def user_text(cls, text: str) -> InputMessage:
        return cls(role=Role.USER, content=[TextBlock(text=text)])

    def text_content(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))


# ── Tool Definition ──────────────────────────────────────────


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# ── Usage ────────────────────────────────────────────────────


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Usage:
        return cls(
            input_tokens=d.get("input_tokens", 0),
            output_tokens=d.get("output_tokens", 0),
            cache_creation_input_tokens=d.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=d.get("cache_read_input_tokens", 0),
        )

    @classmethod
    def zero(cls) -> Usage:
        return cls()


# ── Stream Events ────────────────────────────────────────────


class StreamEventType(Enum):
    TEXT_DELTA = "text_delta"
    TOOL_USE_BEGIN = "tool_use_begin"
    TOOL_USE_DELTA = "tool_use_delta"
    TOOL_USE_END = "tool_use_end"
    USAGE = "usage"
    MESSAGE_STOP = "message_stop"
    ERROR = "error"


@dataclass
class StreamEvent:
    type: StreamEventType
    data: dict[str, Any] = field(default_factory=dict)

    # Convenience accessors
    @property
    def text_delta(self) -> str:
        return self.data.get("text", "")

    @property
    def tool_id(self) -> str:
        return self.data.get("id", "")

    @property
    def tool_name(self) -> str:
        return self.data.get("name", "")

    @property
    def tool_input_delta(self) -> str:
        return self.data.get("input_json_delta", "")

    @property
    def usage(self) -> Usage:
        u = self.data.get("usage", {})
        return Usage.from_dict(u) if isinstance(u, dict) else Usage.zero()

    @property
    def error_message(self) -> str:
        return self.data.get("message", "Unknown error")


# ── Message Request ──────────────────────────────────────────


@dataclass
class MessageRequest:
    model: str
    messages: list[InputMessage]
    tools: list[ToolDefinition] = field(default_factory=list)
    system: str = ""
    max_tokens: int = 8192
    stream: bool = True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in self.messages],
            "max_tokens": self.max_tokens,
            "stream": self.stream,
        }
        if self.tools:
            d["tools"] = [t.to_dict() for t in self.tools]
        if self.system:
            d["system"] = self.system
        return d
