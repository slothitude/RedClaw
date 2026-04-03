"""MCP SSE client — connects to MCP tool servers over Server-Sent Events.

MCP SSE protocol flow:
1. GET /sse -> persistent SSE stream, first event gives session endpoint
2. POST {session_url} with initialize JSON-RPC -> 202 Accepted
3. Read initialize response from SSE stream
4. POST notifications/initialized -> 202
5. POST tools/list -> 202, read response from SSE stream
6. POST tools/call -> 202, read response from SSE stream
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""
    name: str
    url: str


class _SSEConnection:
    """Manages a persistent SSE connection to an MCP server.

    Uses a single continuous reader task with httpx aiter_lines()
    for both endpoint discovery and JSON-RPC response reading.
    """

    def __init__(self, server_url: str, http_client: httpx.AsyncClient) -> None:
        self.server_url = server_url
        self._http = http_client
        self.session_url: str | None = None
        self._response_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._cm: Any = None
        self._stream: httpx.Response | None = None
        self._stopped = False
        self._endpoint_event = asyncio.Event()

    async def connect(self) -> bool:
        """Open SSE stream and wait for session endpoint."""
        try:
            logger.debug(f"Opening SSE connection to {self.server_url}")
            self._cm = self._http.stream("GET", self.server_url)
            self._stream = await self._cm.__aenter__()
            self._stream.raise_for_status()
            logger.debug(f"SSE connection established, status {self._stream.status_code}")

            # Start single continuous reader
            self._reader_task = asyncio.create_task(self._reader_loop())

            # Wait for the endpoint event from the reader
            try:
                await asyncio.wait_for(self._endpoint_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for SSE endpoint")
                await self.close()
                return False

            if not self.session_url:
                logger.error("No session endpoint received from SSE")
                await self.close()
                return False

            return True
        except Exception as e:
            logger.error(f"SSE connect failed for {self.server_url}: {e}")
            return False

    async def _reader_loop(self) -> None:
        """Single continuous reader: discovers endpoint + queues JSON-RPC responses."""
        try:
            current_event = None
            async for line in self._stream.aiter_lines():
                if self._stopped:
                    break
                line = line.strip()
                if not line:
                    continue

                if line.startswith("event: "):
                    current_event = line[7:].strip()
                elif line.startswith("data: "):
                    data_str = line[6:].strip()
                    if not data_str:
                        current_event = None
                        continue

                    # Endpoint discovery
                    if current_event == "endpoint" and data_str.startswith("/"):
                        base = self.server_url.rstrip("/").removesuffix("/sse")
                        self.session_url = f"{base}{data_str}"
                        logger.debug(f"Session URL: {self.session_url}")
                        self._endpoint_event.set()
                        current_event = None
                        continue

                    # JSON-RPC response
                    try:
                        msg = json.loads(data_str)
                        if "id" in msg:
                            logger.debug(f"Queueing response for id={msg['id']}")
                            await self._response_queue.put(msg)
                    except json.JSONDecodeError:
                        pass
                    current_event = None
        except Exception as e:
            logger.debug(f"SSE reader ended for {self.server_url}: {e}")
        finally:
            # Unblock connect() if reader dies before finding endpoint
            self._endpoint_event.set()

    async def send_and_wait(self, payload: dict, timeout: float = 30.0) -> dict | None:
        """POST a JSON-RPC message and wait for the response via SSE."""
        if not self.session_url:
            return None
        req_id = payload.get("id")
        try:
            resp = await self._http.post(self.session_url, json=payload)
            if resp.status_code not in (200, 202):
                logger.error(f"POST returned {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"POST failed: {e}")
            return None
        # Notification (no id) — no response expected
        if req_id is None:
            return None

        # Wait for response with matching id from SSE stream
        try:
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    logger.error(f"Timeout waiting for response to id={req_id}")
                    return None
                try:
                    msg = await asyncio.wait_for(
                        self._response_queue.get(), timeout=min(remaining, 5.0)
                    )
                except asyncio.TimeoutError:
                    continue
                if msg.get("id") == req_id:
                    return msg
        except Exception as e:
            logger.error(f"Error waiting for response: {e}")
            return None

    async def close(self) -> None:
        self._stopped = True
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._cm:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm = None
            self._stream = None


class MCPClient:
    """Client for MCP SSE servers."""

    def __init__(self, servers: list[MCPServerConfig] | None = None) -> None:
        self.servers = servers or []
        self._tools: dict[str, MCPTool] = {}
        self._server_for_tool: dict[str, MCPServerConfig] = {}
        self._connections: dict[str, _SSEConnection] = {}
        self._http = httpx.AsyncClient(timeout=60.0)
        self._next_id = 1

    def _next_req_id(self) -> int:
        rid = self._next_id
        self._next_id += 1
        return rid

    async def _get_connection(self, server: MCPServerConfig) -> _SSEConnection | None:
        """Get or create an SSE connection to the server."""
        if server.name in self._connections:
            conn = self._connections[server.name]
            if conn.session_url:
                return conn
        conn = _SSEConnection(server.url, self._http)
        if not await conn.connect():
            return None
        # MCP initialize handshake
        init_id = self._next_req_id()
        init_resp = await conn.send_and_wait({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "redclaw", "version": "1.0"},
            },
        })
        if not init_resp or "error" in init_resp:
            err = init_resp.get("error") if init_resp else "no response"
            logger.error(f"MCP initialize failed for {server.name}: {err}")
            await conn.close()
            return None

        # Send initialized notification (no id = notification)
        await conn.send_and_wait({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        self._connections[server.name] = conn
        logger.info(f"MCP connected to {server.name} ({server.url})")
        return conn

    async def discover(self) -> list[MCPTool]:
        """Discover tools from all configured MCP servers."""
        for server in self.servers:
            try:
                conn = await self._get_connection(server)
                if not conn:
                    continue
                req_id = self._next_req_id()
                resp = await conn.send_and_wait({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/list",
                    "params": {},
                })
                if not resp or "error" in resp:
                    err = resp.get("error") if resp else "no response"
                    logger.error(f"tools/list failed for {server.name}: {err}")
                    continue
                result = resp.get("result", {})
                tool_list = result.get("tools", [])
                for t in tool_list:
                    name = t.get("name", "")
                    if not name:
                        continue
                    tool = MCPTool(
                        name=name,
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", {"type": "object", "properties": {}}),
                    )
                    self._tools[name] = tool
                    self._server_for_tool[name] = server
                logger.info(f"MCP {server.name}: {len(tool_list)} tools discovered")
            except Exception as e:
                logger.error(f"Failed to discover tools from {server.name} ({server.url}): {e}")
        return list(self._tools.values())

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call an MCP tool by name with the given arguments."""
        server = self._server_for_tool.get(tool_name)
        if server is None:
            return f"Error: MCP tool '{tool_name}' not found"
        conn = await self._get_connection(server)
        if not conn:
            return f"Error: no connection to {server.name}"
        req_id = self._next_req_id()
        resp = await conn.send_and_wait({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        if not resp:
            return f"Error: no response for MCP tool '{tool_name}'"
        if "error" in resp:
            return f"MCP error: {resp['error'].get('message', resp['error'])}"
        result = resp.get("result", {})
        content = result.get("content", [])
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    texts.append(block)
            return "\n".join(texts) if texts else json.dumps(result)
        return json.dumps(result)

    @property
    def tools(self) -> list[MCPTool]:
        return list(self._tools.values())

    async def close(self) -> None:
        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
        await self._http.aclose()
