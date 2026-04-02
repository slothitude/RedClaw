"""Channel abstraction — base class for all messaging channels."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class ChannelMessage:
    """Normalized message from any channel."""
    text: str
    user_id: str
    chat_id: str
    file_path: str | None = None
    file_name: str | None = None
    raw: Any = None


@dataclass
class ChannelConfig:
    """Channel configuration."""
    working_dir: str = ""
    allowed_users: list[str] = field(default_factory=list)


class ChannelBase(abc.ABC):
    """Abstract base class for messaging channels."""

    def __init__(self, config: ChannelConfig) -> None:
        self.config = config
        self._on_message: Callable[[ChannelMessage], Awaitable[str]] | None = None
        self._on_file: Callable[[ChannelMessage], Awaitable[str]] | None = None

    def set_message_handler(
        self,
        handler: Callable[[ChannelMessage], Awaitable[str]],
    ) -> None:
        self._on_message = handler

    def set_file_handler(
        self,
        handler: Callable[[ChannelMessage], Awaitable[str]],
    ) -> None:
        self._on_file = handler

    @abc.abstractmethod
    async def send_text(self, chat_id: str, text: str) -> None:
        """Send a text message to a chat."""

    @abc.abstractmethod
    async def send_file(self, chat_id: str, file_path: str, filename: str | None = None) -> None:
        """Send a file to a chat."""

    @abc.abstractmethod
    async def send_typing(self, chat_id: str) -> None:
        """Show typing indicator."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Start the channel (e.g., start polling, connect)."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop the channel."""
