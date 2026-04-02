"""MCP SSE client — connects to MCP tool servers over Server-Sent Events."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
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

    Connects to SSE endpoints, lists available tools, and calls them.
    """

    def __init__(self, servers: list[MCPServerConfig] | None = None) -> None:
        self.servers = servers or []
        self._tools: dict[str, MCPTool] = {}
        self._server_for_tool: dict[str, MCPServerConfig] = {}
        self._http = httpx.AsyncClient(timeout=60.0)

    async def discover(self) -> list[MCPTool]:
        """Discover tools from all configured MCP servers."""
        for server in self.servers:
            try:
                tools = await self._list_tools(server)
                for tool in tools:
                    self._tools[tool.name] = tool
                    self._server_for_tool[tool.name] = server
                logger.info(f"MCP server {server.name}: {len(tools)} tools discovered")
            except Exception as e:
                logger.error(f"Failed to discover tools from {server.name} ({server.url}): {e}")
        return list(self._tools.values())

    async def _list_tools(self, server: MCPServerConfig) -> list[MCPTool]:
        """List tools from a single MCP server via SSE."""
        # MCP SSE protocol: GET /sse opens event stream, then send JSON-RPC via POST
        # For listing tools, we try the /tools endpoint directly
        base_url = server.url.rstrip("/").removesuffix("/sse")
        tools_url = f"{base_url}/tools"

        try:
            resp = await self._http.get(tools_url)
            resp.raise_for_status()
            data = resp.json()

            tools = []
            tool_list = data if isinstance(data, list) else data.get("tools", [])
            for t in tool_list:
                name = t.get("name", "")
                if not name:
                    continue
                tools.append(MCPTool(
                    name=name,
                    description=t.get("description", ""),
                    input_schema=t.get("parameters", t.get("inputSchema", {"type": "object", "properties": {}})),
                ))
            return tools
        except Exception:
            pass

        # Fallback: try JSON-RPC via the messages endpoint
        messages_url = f"{base_url}/messages"
        try:
            resp = await self._http.post(messages_url, json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            })
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", {})
            tool_list = result.get("tools", [])
            tools = []
            for t in tool_list:
                name = t.get("name", "")
                if not name:
                    continue
                tools.append(MCPTool(
                    name=name,
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {"type": "object", "properties": {}}),
                ))
            return tools
        except Exception as e:
            logger.error(f"Failed to list tools from {server.name}: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call an MCP tool by name with the given arguments."""
        server = self._server_for_tool.get(tool_name)
        if server is None:
            return f"Error: MCP tool '{tool_name}' not found"

        base_url = server.url.rstrip("/").removesuffix("/sse")
        messages_url = f"{base_url}/messages"

        try:
            resp = await self._http.post(messages_url, json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            })
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                return f"MCP error: {data['error'].get('message', data['error'])}"

            result = data.get("result", {})
            # MCP returns content as a list of content blocks
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
