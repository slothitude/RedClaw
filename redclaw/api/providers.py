"""Provider registry — adapter layer for different LLM APIs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from redclaw.api.types import (
    InputMessage,
    MessageRequest,
    StreamEvent,
    StreamEventType,
    ToolDefinition,
    Usage,
)


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: str
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    stream_path: str = "/v1/chat/completions"
    message_format: str = "openai"  # "openai" | "anthropic"

    def get_api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


# ── Built-in providers ───────────────────────────────────────

PROVIDERS: dict[str, ProviderConfig] = {
    "openai": ProviderConfig(
        name="openai",
        base_url="https://api.openai.com",
        api_key_env="OPENAI_API_KEY",
        stream_path="/v1/chat/completions",
        message_format="openai",
    ),
    "anthropic": ProviderConfig(
        name="anthropic",
        base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        auth_header="x-api-key",
        auth_prefix="",
        stream_path="/v1/messages",
        message_format="anthropic",
    ),
    "ollama": ProviderConfig(
        name="ollama",
        base_url="http://localhost:11434",
        api_key_env="",
        stream_path="/v1/chat/completions",
        message_format="openai",
    ),
    "openrouter": ProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api",
        api_key_env="OPENROUTER_API_KEY",
        stream_path="/v1/chat/completions",
        message_format="openai",
    ),
    "groq": ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai",
        api_key_env="GROQ_API_KEY",
        stream_path="/v1/chat/completions",
        message_format="openai",
    ),
    "deepseek": ProviderConfig(
        name="deepseek",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        stream_path="/v1/chat/completions",
        message_format="openai",
    ),
    "zai": ProviderConfig(
        name="zai",
        base_url="https://api.z.ai/api/coding/paas/v4",
        api_key_env="ZAI_API_KEY",
        stream_path="/chat/completions",
        message_format="openai",
    ),
}


def get_provider(name: str, base_url: str | None = None) -> ProviderConfig:
    """Get provider config by name, or create an 'any' provider with custom base_url."""
    if name in PROVIDERS:
        cfg = PROVIDERS[name]
        if base_url:
            # Determine stream path: if base_url already contains a version path, use /chat/completions
            url_stripped = base_url.rstrip("/")
            sp = cfg.stream_path
            if not url_stripped.endswith("/v1") and "/v1/" not in url_stripped:
                sp = "/chat/completions"
            cfg = ProviderConfig(
                name=cfg.name,
                base_url=url_stripped,
                api_key_env=cfg.api_key_env,
                auth_header=cfg.auth_header,
                auth_prefix=cfg.auth_prefix,
                stream_path=sp,
                message_format=cfg.message_format,
            )
        return cfg
    # "any" provider — custom base_url required
    if not base_url:
        raise ValueError(f"Unknown provider '{name}'. Provide --base-url for custom providers.")
    url_stripped = base_url.rstrip("/")
    sp = "/chat/completions" if not url_stripped.endswith("/v1") and "/v1/" not in url_stripped else "/v1/chat/completions"
    return ProviderConfig(
        name=name,
        base_url=url_stripped,
        api_key_env=f"{name.upper()}_API_KEY",
        stream_path=sp,
        message_format="openai",
    )


# ── Request formatting ───────────────────────────────────────


def format_request(req: MessageRequest, provider: ProviderConfig) -> dict[str, Any]:
    """Format a MessageRequest into the provider-specific JSON body."""
    if provider.message_format == "anthropic":
        return _format_anthropic(req)
    return _format_openai(req)


def _format_openai(req: MessageRequest) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for msg in req.messages:
        if msg.role.value == "tool":
            # tool result -> separate message
            for block in msg.content:
                if hasattr(block, "tool_use_id"):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": block.tool_use_id,
                        "content": block.content if hasattr(block, "content") else "",
                    })
        else:
            # Flatten content blocks
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in msg.content:
                from redclaw.api.types import TextBlock, ToolUseBlock, ToolResultBlock
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append({
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input),
                        },
                    })
                elif isinstance(block, ToolResultBlock):
                    # Shouldn't appear in non-tool role, skip
                    pass
            entry: dict[str, Any] = {"role": msg.role.value}
            if tool_calls:
                entry["tool_calls"] = tool_calls
                entry["content"] = "".join(text_parts) or None
            else:
                entry["content"] = "".join(text_parts)
            messages.append(entry)

    body: dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "stream": req.stream,
    }
    if req.max_tokens:
        body["max_tokens"] = req.max_tokens
    if req.system:
        body["messages"] = [{"role": "system", "content": req.system}] + messages
    if req.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in req.tools
        ]
    return body


def _format_anthropic(req: MessageRequest) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for msg in req.messages:
        blocks: list[dict[str, Any]] = []
        for block in msg.content:
            from redclaw.api.types import TextBlock, ToolUseBlock, ToolResultBlock
            if isinstance(block, TextBlock):
                blocks.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                blocks.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
            elif isinstance(block, ToolResultBlock):
                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                })
        messages.append({"role": msg.role.value, "content": blocks})

    body: dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "max_tokens": req.max_tokens,
        "stream": req.stream,
    }
    if req.system:
        body["system"] = req.system
    if req.tools:
        body["tools"] = [t.to_dict() for t in req.tools]
    return body


# ── Stream event parsing ────────────────────────────────────


def parse_sse_event(
    event_type: str, data: str, provider: ProviderConfig
) -> StreamEvent | None:
    """Parse an SSE frame into a StreamEvent (or None to skip)."""
    if provider.message_format == "anthropic":
        return _parse_anthropic_event(event_type, data)
    return _parse_openai_event(event_type, data)


def _parse_openai_event(event_type: str, data: str) -> StreamEvent | None:
    if data == "[DONE]":
        return StreamEvent(type=StreamEventType.MESSAGE_STOP)
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None

    if "error" in obj:
        return StreamEvent(
            type=StreamEventType.ERROR,
            data={"message": obj["error"].get("message", str(obj["error"]))},
        )

    choice = obj.get("choices", [{}])[0] if obj.get("choices") else {}
    delta = choice.get("delta", {})

    # Text content
    if delta.get("content"):
        return StreamEvent(
            type=StreamEventType.TEXT_DELTA, data={"text": delta["content"]}
        )

    # Tool calls — handle both incremental and single-chunk patterns
    if delta.get("tool_calls"):
        tc = delta["tool_calls"][0]
        func = tc.get("function", {})
        has_name = bool(func.get("name"))
        has_args = bool(func.get("arguments"))

        # Single-chunk: name + complete arguments together (Ollama/GLM style)
        if has_name and has_args:
            return StreamEvent(
                type=StreamEventType.TOOL_USE_BEGIN,
                data={
                    "id": tc.get("id", ""),
                    "name": func["name"],
                    "input_json_delta": func["arguments"],
                },
            )
        # Incremental: name first
        if has_name:
            return StreamEvent(
                type=StreamEventType.TOOL_USE_BEGIN,
                data={
                    "id": tc.get("id", ""),
                    "name": func["name"],
                },
            )
        # Incremental: argument delta
        if has_args:
            return StreamEvent(
                type=StreamEventType.TOOL_USE_DELTA,
                data={"input_json_delta": func["arguments"]},
            )

    # Usage (o1/gpt-4o style)
    if "usage" in obj:
        u = obj["usage"]
        return StreamEvent(
            type=StreamEventType.USAGE,
            data={
                "usage": {
                    "input_tokens": u.get("prompt_tokens", 0),
                    "output_tokens": u.get("completion_tokens", 0),
                }
            },
        )

    # Finish
    if choice.get("finish_reason"):
        return StreamEvent(type=StreamEventType.MESSAGE_STOP)

    return None


def _parse_anthropic_event(event_type: str, data: str) -> StreamEvent | None:
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None

    etype = obj.get("type", event_type)

    if etype == "content_block_delta":
        delta = obj.get("delta", {})
        if delta.get("type") == "text_delta":
            return StreamEvent(
                type=StreamEventType.TEXT_DELTA, data={"text": delta.get("text", "")}
            )
        if delta.get("type") == "input_json_delta":
            return StreamEvent(
                type=StreamEventType.TOOL_USE_DELTA,
                data={"input_json_delta": delta.get("partial_json", "")},
            )

    if etype == "content_block_start":
        block = obj.get("content_block", {})
        if block.get("type") == "tool_use":
            return StreamEvent(
                type=StreamEventType.TOOL_USE_BEGIN,
                data={"id": block.get("id", ""), "name": block.get("name", "")},
            )

    if etype == "message_delta":
        delta = obj.get("delta", {})
        if delta.get("stop_reason"):
            usage = obj.get("usage", {})
            return StreamEvent(
                type=StreamEventType.USAGE,
                data={
                    "usage": {
                        "output_tokens": usage.get("output_tokens", 0),
                    }
                },
            )

    if etype == "message_start":
        usage = obj.get("message", {}).get("usage", {})
        return StreamEvent(
            type=StreamEventType.USAGE,
            data={
                "usage": {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                }
            },
        )

    if etype == "message_stop":
        return StreamEvent(type=StreamEventType.MESSAGE_STOP)

    if etype == "error":
        return StreamEvent(
            type=StreamEventType.ERROR,
            data={"message": obj.get("error", {}).get("message", data)},
        )

    return None
