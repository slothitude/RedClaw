"""SSE (Server-Sent Events) line-buffered parser."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_BUFFER_SIZE = 10_000_000  # 10MB


class SseParser:
    """Incremental SSE frame parser.

    Feed chunks of bytes from the HTTP stream. Yields complete events
    as (event, data) tuples once a double-newline boundary is hit.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """Feed a chunk and return any complete events."""
        self._buffer += chunk
        if len(self._buffer) > MAX_BUFFER_SIZE:
            logger.warning("SSE buffer exceeded %d bytes, truncating", MAX_BUFFER_SIZE)
            self._buffer = self._buffer[-MAX_BUFFER_SIZE:]
        events: list[tuple[str, str]] = []
        while "\n\n" in self._buffer:
            raw, self._buffer = self._buffer.split("\n\n", 1)
            event, data = self._parse_frame(raw)
            if data is not None:
                events.append((event, data))
        return events

    def flush(self) -> list[tuple[str, str]]:
        """Flush any remaining buffered data."""
        if self._buffer.strip():
            event, data = self._parse_frame(self._buffer)
            self._buffer = ""
            if data is not None:
                return [(event, data)]
        self._buffer = ""
        return []

    @staticmethod
    def _parse_frame(raw: str) -> tuple[str, str]:
        event = "message"
        data_lines: list[str] = []
        for line in raw.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
            # ignore comments (lines starting with ':') and others
        return event, "\n".join(data_lines)
