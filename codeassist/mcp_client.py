import asyncio
import json
import logging
import uuid
from typing import Any, Callable
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)


@dataclass
class MCPTool:
    name: str
    description: str
    parameters: dict
    server_name: str

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class MCPClient:
    def __init__(self):
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._urls: dict[str, str] = {}
        self._tools: dict[str, MCPTool] = {}
        self._initialized = False

    async def initialize(self, servers_config: dict[str, dict]) -> list[str]:
        """Initialize connections to all configured MCP servers. Returns list of server names."""
        initialized = []
        for name, config in servers_config.items():
            try:
                await self._connect_server(name, config)
                initialized.append(name)
                log.info("Connected to MCP server: %s", name)
            except Exception as e:
                log.error("Failed to connect to MCP server '%s': %s", name, e)

        self._initialized = True
        return initialized

    async def _connect_server(self, name: str, config: dict):
        """Connect to a single MCP server via SSE."""
        url = config.get("url")
        if not url:
            raise ValueError(f"Server '{name}' missing 'url' configuration")

        headers = config.get("headers", {})
        env = config.get("env", {})

        # Apply environment variables
        import os
        for key, value in env.items():
            if isinstance(value, str) and value.startswith("env:"):
                env[key] = os.environ.get(value[4:], "")

        client = httpx.AsyncClient(
            headers={**headers, "Content-Type": "application/json"},
            timeout=30.0,
        )

        # Initialize connection
        init_response = await client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "codeassist",
                        "version": "1.0.0",
                    },
                },
            },
        )

        if init_response.status_code != 200:
            raise Exception(f"Initialization failed: {init_response.text}")

        # Send initialized notification
        await client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )

        self._clients[name] = client
        self._urls[name] = url

        # List tools from server
        await self._discover_tools(name, url)

    async def _discover_tools(self, server_name: str, url: str):
        """Discover tools from an MCP server."""
        try:
            response = await self._clients[server_name].post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            )

            if response.status_code == 200:
                data = response.json()
                tools = data.get("result", {}).get("tools", [])

                for tool in tools:
                    mcp_tool = MCPTool(
                        name=f"mcp_{server_name}_{tool['name']}",
                        description=tool.get("description", ""),
                        parameters=tool.get("inputSchema", {}),
                        server_name=server_name,
                    )
                    self._tools[mcp_tool.name] = mcp_tool
                    log.debug("Discovered MCP tool: %s from %s", mcp_tool.name, server_name)

        except Exception as e:
            log.error("Failed to discover tools from %s: %s", server_name, e)

    def get_tools(self) -> list[MCPTool]:
        """Get all discovered MCP tools."""
        return list(self._tools.values())

    def get_tool_schemas(self) -> list[dict]:
        """Get schemas for all MCP tools."""
        return [tool.schema() for tool in self._tools.values()]

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call an MCP tool."""
        tool = self._tools.get(tool_name)
        if not tool:
            return f"Error: unknown MCP tool '{tool_name}'"

        client = self._clients.get(tool.server_name)
        if not client:
            return f"Error: MCP server '{tool.server_name}' not connected"

        try:
            response = await client.post(
                self._get_server_url(tool.server_name),
                json={
                    "jsonrpc": "2.0",
                    "id": str(uuid.uuid4()),
                    "method": "tools/call",
                    "params": {
                        "name": tool.name.replace(f"mcp_{tool.server_name}_", ""),
                        "arguments": arguments,
                    },
                },
            )

            if response.status_code != 200:
                return f"Error: MCP tool call failed: {response.text}"

            data = response.json()
            content = data.get("result", {}).get("content", [])

            if isinstance(content, list):
                return "\n".join(
                    item.get("text", "") for item in content if item.get("type") == "text"
                )
            elif isinstance(content, str):
                return content
            else:
                return json.dumps(data.get("result", {}))

        except Exception as e:
            log.exception("MCP tool call failed")
            return f"Error calling MCP tool '{tool_name}': {e}"

    def _get_server_url(self, server_name: str) -> str:
        """Get the URL for an MCP server."""
        return self._urls.get(server_name, "")

    async def close(self):
        """Close all MCP client connections."""
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        self._urls.clear()
        self._tools.clear()
        self._initialized = False


class MCPToolAdapter:
    """Adapts MCP tools to work with the CodeAssist tool system."""

    def __init__(self, mcp_client: MCPClient):
        self.mcp_client = mcp_client

    async def execute(self, name: str, arguments: dict) -> str:
        """Execute an MCP tool through the adapter."""
        return await self.mcp_client.call_tool(name, arguments)

    def list_names(self) -> list[str]:
        """List all available MCP tool names."""
        return [tool.name for tool in self.mcp_client.get_tools()]


class MCPToolWrapper:
    """Wraps a single MCPTool as a proper Tool subclass for the registry."""

    def __init__(self, mcp_tool: MCPTool, mcp_client: MCPClient):
        self.name = mcp_tool.name
        self.description = mcp_tool.description
        self.parameters = mcp_tool.parameters
        self._mcp_client = mcp_client

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute(self, **kwargs) -> "ToolResult":
        from tools import ToolResult
        output = await self._mcp_client.call_tool(self.name, kwargs)
        is_error = output.startswith("Error:")
        return ToolResult(output=output, error=is_error)
