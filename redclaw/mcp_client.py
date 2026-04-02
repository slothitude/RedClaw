"""MCP SSE client — connects to MCP tool servers over Server-Sent Events."""

from __future__ import annotations

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
    url: str  # e.g. http://100.84.161.63:8006/sse


class MCPClient:
    """Client for MCP SSE servers.

    Uses the MCP SSE transport protocol:
    1. GET /sse to open event stream and receive session endpoint
    2. POST to session endpoint with JSON-RPC messages
    """

    def __init__(self, servers: list[MCPServerConfig] | None = None) -> None:
        self.servers = servers or []
        self._tools: dict[str, MCPTool] = {}
        self._server_for_tool: dict[str, MCPServerConfig] = {}
        self._session_urls: dict[str, str] = {}  # server.name -> messages URL
        self._http = httpx.AsyncClient(timeout=60.0)

    async def _get_session_url(self, server: MCPServerConfig) -> str | None:
        """Connect to SSE endpoint and extract the session messages URL."""
        if server.name in self._session_urls:
            return self._session_urls[server.name]

        sse_url = server.url
        try:
            async with self._http.stream("GET", sse_url) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        path = line[6:].strip()
                        # Build full URL from the SSE base
                        base = server.url.rstrip("/").removesuffix("/sse")
                        url = f"{base}{path}"
                        self._session_urls[server.name] = url
                        return url
        except Exception as e:
            logger.error(f"Failed to get session from {server.name} ({sse_url}): {e}")
        return None

    async def _jsonrpc(self, session_url: str, method: str, params: dict | None = None, req_id: int = 1) -> dict | None:
        """Send a JSON-RPC request to the MCP server session."""
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        try:
            resp = await self._http.post(session_url, json=payload)
            if resp.status_code == 202:
                # Accepted — response comes via SSE stream, poll briefly
                return None
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                logger.error(f"JSON-RPC error: {data['error']}")
                return None
            return data.get("result")
        except Exception as e:
            logger.error(f"JSON-RPC call failed: {e}")
            return None

    async def discover(self) -> list[MCPTool]:
        """Discover tools from all configured MCP servers."""
        for server in self.servers:
            try:
                session_url = await self._get_session_url(server)
                if not session_url:
                    logger.error(f"No session URL for {server.name}")
                    continue

                result = await self._jsonrpc(session_url, "tools/list", {})
                if not result:
                    continue

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

        session_url = await self._get_session_url(server)
        if not session_url:
            return f"Error: no session for {server.name}"

        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        try:
            resp = await self._http.post(session_url, json=payload)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                return f"MCP error: {data['error'].get('message', data['error'])}"

            result = data.get("result", {})
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

        except Exception as e:
            return f"Error calling MCP tool '{tool_name}': {e}"

    @property
    def tools(self) -> list[MCPTool]:
        return list(self._tools.values())

    async def close(self) -> None:
        await self._http.aclose()
