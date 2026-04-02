"""Compaction: summarize old messages to keep context window manageable.

Uses deterministic summarization (truncation + metadata) by default.
Optionally uses LLM-based summarization for richer context preservation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redclaw.api.types import TextBlock, ToolResultBlock, ToolUseBlock
from redclaw.runtime.session import ConversationMessage, Session

if TYPE_CHECKING:
    from redclaw.api.client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class CompactionConfig:
    max_messages: int = 100
    keep_recent: int = 20          # always keep last N messages
    max_tool_result_chars: int = 2000  # truncate tool results beyond this
    compact_threshold: float = 0.8  # compact when messages > threshold * max_messages


def should_compact(session: Session, config: CompactionConfig | None = None) -> bool:
    """Check if the session needs compaction."""
    cfg = config or CompactionConfig()
    return len(session.messages) > int(cfg.max_messages * cfg.compact_threshold)


def compact_session(session: Session, config: CompactionConfig | None = None) -> Session:
    """Compact a session by summarizing old messages.

    Keeps the first message (system context) and the most recent messages.
    Summarizes everything in between into a single compact summary message.
    """
    cfg = config or CompactionConfig()
    msgs = session.messages

    if len(msgs) <= cfg.keep_recent + 1:
        return session  # nothing to compact

    # Keep first message + last keep_recent messages
    first = msgs[0]
    old = msgs[1 : len(msgs) - cfg.keep_recent]
    recent = msgs[len(msgs) - cfg.keep_recent :]

    summary_text = _summarize_messages(old)

    summary_msg = ConversationMessage(
        role=first.role,  # preserve original role (likely user with system context)
        content=[TextBlock(text=summary_text)],
    )

    session.messages = [first, summary_msg] + recent
    return session


def _summarize_messages(messages: list[ConversationMessage]) -> str:
    """Create a deterministic summary of a message range."""
    parts: list[str] = ["[Conversation summary — older messages compacted]"]
    tool_counts: dict[str, int] = {}
    user_turns = 0
    assistant_turns = 0

    for msg in messages:
        from redclaw.api.types import Role
        if msg.role == Role.USER:
            user_turns += 1
        elif msg.role == Role.ASSISTANT:
            assistant_turns += 1

        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                tool_counts[block.name] = tool_counts.get(block.name, 0) + 1
            elif isinstance(block, ToolResultBlock):
                pass  # counted via ToolUseBlock

    parts.append(f"User turns: {user_turns}, Assistant turns: {assistant_turns}")
    if tool_counts:
        tool_summary = ", ".join(f"{name}({count})" for name, count in sorted(tool_counts.items()))
        parts.append(f"Tools used: {tool_summary}")

    # Add last few user messages for context
    last_user_texts: list[str] = []
    for msg in reversed(messages):
        from redclaw.api.types import Role
        if msg.role == Role.USER and len(last_user_texts) < 3:
            text = msg.text_content()[:200]
            if text:
                last_user_texts.append(text)

    if last_user_texts:
        parts.append("Recent user messages:")
        for t in reversed(last_user_texts):
            parts.append(f"  - {t}")

    return "\n".join(parts)


def truncate_tool_results(
    messages: list[ConversationMessage], max_chars: int = 2000
) -> list[ConversationMessage]:
    """Truncate long tool results in-place (mutates content blocks)."""
    for msg in messages:
        for i, block in enumerate(msg.content):
            if isinstance(block, ToolResultBlock) and len(block.content) > max_chars:
                msg.content[i] = ToolResultBlock(
                    tool_use_id=block.tool_use_id,
                    content=block.content[:max_chars] + "\n... [truncated]",
                    is_error=block.is_error,
                )
    return messages


# ── LLM-based compaction ─────────────────────────────────────


def _extract_text_from_messages(messages: list[ConversationMessage], max_chars: int = 200) -> str:
    """Extract a compact text representation of messages for LLM summarization."""
    parts: list[str] = []
    for msg in messages:
        from redclaw.api.types import Role
        role = msg.role.value
        text = msg.text_content()[:max_chars]
        tools_used = [b.name for b in msg.content if isinstance(b, ToolUseBlock)]
        if text:
            parts.append(f"[{role}] {text}")
        if tools_used:
            parts.append(f"  tools: {', '.join(tools_used)}")
    return "\n".join(parts)


async def compact_session_with_llm(
    session: Session,
    client: LLMClient,
    model: str,
    config: CompactionConfig | None = None,
) -> Session:
    """Compact a session using LLM-generated summary of pruned messages.

    Strategy:
    1. Prune old large tool results (>200 chars, keep last N)
    2. Protect head (system prompt + first exchange) and tail (recent messages)
    3. Generate structured LLM summary of pruned middle content
    4. Replace pruned messages with summary
    """
    from redclaw.api.types import InputMessage, MessageRequest, Role

    cfg = config or CompactionConfig()
    msgs = session.messages

    if len(msgs) <= cfg.keep_recent + 2:
        return session  # not enough to compact

    # Protect head (first message) and tail
    first = msgs[0]
    recent = msgs[len(msgs) - cfg.keep_recent :]
    middle = msgs[1 : len(msgs) - cfg.keep_recent]

    if not middle:
        return session

    # Truncate large tool results in the middle before summarizing
    truncate_tool_results(middle, max_chars=200)

    # Build text for LLM summarization
    middle_text = _extract_text_from_messages(middle)

    if not middle_text.strip():
        # Fall back to deterministic compaction
        return compact_session(session, cfg)

    # Ask the LLM to summarize
    summary_prompt = (
        "Summarize the following conversation excerpt into a structured summary.\n"
        "Format:\n"
        "Goal: <what the user is trying to accomplish>\n"
        "Progress: <what has been done so far>\n"
        "Decisions: <key decisions made>\n"
        "Next Steps: <what remains to be done>\n\n"
        f"Conversation:\n{middle_text[:8000]}"
    )

    try:
        from redclaw.api.providers import ProviderConfig
        request = MessageRequest(
            model=model,
            messages=[
                InputMessage.user_text(summary_prompt),
            ],
            max_tokens=1024,
            stream=False,
        )
        # Collect the full response
        result_text = ""
        async for event in client.stream_message(request):
            if event.type.value == "text_delta":
                result_text += event.text_delta

        if not result_text.strip():
            return compact_session(session, cfg)

        summary_text = f"[LLM-Generated Conversation Summary]\n{result_text.strip()}"

    except Exception as e:
        logger.warning(f"LLM compaction failed, falling back to deterministic: {e}")
        return compact_session(session, cfg)

    summary_msg = ConversationMessage(
        role=first.role,
        content=[TextBlock(text=summary_text)],
    )

    session.messages = [first, summary_msg] + recent
    return session
