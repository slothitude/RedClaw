"""Provider-agnostic LLM client with streaming support."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx
from redclaw.api.providers import ProviderConfig, format_request, parse_sse_event
from redclaw.api.sse import SseParser
from redclaw.api.types import MessageRequest, StreamEvent, StreamEventType

logger = logging.getLogger(__name__)
MAX_RETRIES = 5
INITIAL_BACKOFF = 2.0
BACKOFF_MULTIPLIER = 2.0
REQUEST_TIMEOUT = 300.0  # 5 min for long generations


class LLMClient:
    """Async LLM client that streams responses via SSE."""
    def __init__(self, provider: ProviderConfig) -> None:
        self.provider = provider
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=10.0),
        )

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        api_key = self.provider.get_api_key()
        if api_key:
            headers[self.provider.auth_header] = f"{self.provider.auth_prefix}{api_key}"
        # Anthropic requires version header
        if self.provider.message_format == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        return headers

    async def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        """Stream a message request, yielding StreamEvents.
        Includes retry with exponential backoff for 429 (rate limit) errors).
        """
        body = format_request(request, self.provider)
        url = f"{self.provider.base_url}{self.provider.stream_path}"
        headers = self._build_headers()
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                async with self._http.stream(
                    "POST", url, json=body, headers=headers
                ) as response:
                    if response.status_code == 429:
                        # Rate limited — retry with backoff
                        error_body = await response.aread()
                        backoff = min(INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt), 30.0)
                        logger.warning(
                            "Rate limited (429), retry %d/%d in %.1fs: %s",
                            attempt + 1, MAX_RETRIES, backoff,
                            error_body.decode(errors="replace")[:150],
                        )
                        await asyncio.sleep(backoff)
                        continue

                    if response.status_code >= 400:
                        error_body = await response.aread()
                        msg = f"API error {response.status_code}: {error_body.decode(errors='replace')}"
                        yield StreamEvent(
                            type=StreamEventType.ERROR, data={"message": msg}
                        )
                        return
                    parser = SseParser()
                    async for chunk in response.aiter_text():
                        for event_type, data in parser.feed(chunk):
                            evt = parse_sse_event(event_type, data, self.provider)
                            if evt is not None:
                                yield evt

                    # Flush remaining
                    for event_type, data in parser.flush():
                        evt = parse_sse_event(event_type, data, self.provider)
                        if evt is not None:
                            yield evt

                return  # success
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    delay = INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt)
                    logger.warning("Retry %d/%d after %s: %s", attempt + 1, MAX_RETRIES, type(exc).__name__, exc)
                    await asyncio.sleep(delay)
            except Exception as exc:
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    data={"message": f"Unexpected error: {exc}"},
                )
                return
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data={"message": f"Failed after {MAX_RETRIES} retries: {last_exc}"},
        )

    async def close(self) -> None:
        await self._http.aclose()
