import asyncio
import json as jsonlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .tools import (
        ToolResult,  # annotation-only; imported lazily at runtime to avoid a tools<->mcp cycle
    )


class MCPHTTPError(Exception):
    """A transport-level HTTP failure from an MCP server."""

    def __init__(self, text: str):
        self.text = text
        super().__init__(text)


class MCPRPCError(Exception):
    """A JSON-RPC error returned by an MCP server."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


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


@dataclass
class MCPPrompt:
    name: str
    description: str = ""
    arguments: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.arguments,
        }


@dataclass
class MCPResource:
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str | None = None

    def to_dict(self) -> dict:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


class _MCPResponse:
    """Minimal httpx-shaped response for the stdio transport."""

    def __init__(self, status_code: int, payload: dict, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = jsonlib.dumps(payload)
        self.headers = headers or {"content-type": "application/json"}

    def json(self) -> dict:
        return self._payload


def _resolve_env(values: dict | None) -> dict:
    """Resolve ``env:NAME`` markers without mutating the stored config."""
    resolved: dict = {}
    for key, value in (values or {}).items():
        if isinstance(value, str) and value.startswith("env:"):
            resolved[key] = os.environ.get(value[4:], "")
        else:
            resolved[key] = value
    return resolved


def _oauth_provider(config: dict | None) -> str:
    config = config or {}
    provider = config.get("oauth_provider")
    if not provider and isinstance(config.get("auth"), dict):
        provider = config["auth"].get("provider")
    return str(provider or "")


def _transport_name(config: dict | None) -> str:
    config = config or {}
    if config.get("command"):
        return "stdio"
    if str(config.get("transport", "")).strip().lower() == "sse":
        return "sse"
    return "http"


def _fingerprint(config: dict) -> str:
    """Identity used by reload to decide whether a server must reconnect.

    A URL alone is not enough: stdio servers have no URL, and an OAuth provider
    or command change must also re-establish the connection.
    """
    identity = {
        "url": config.get("url"),
        "command": config.get("command"),
        "transport": config.get("transport"),
        "oauth_provider": _oauth_provider(config),
        "cwd": config.get("cwd"),
    }
    for key in ("args", "headers", "env", "auth"):
        value = config.get(key)
        identity[key] = jsonlib.dumps(value, sort_keys=True, default=str) if value is not None else None
    return jsonlib.dumps(identity, sort_keys=True, default=str)


def _decode_sse(text: str) -> dict:
    """Return the last JSON-RPC object carried in an SSE response body."""
    frames: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            current.append(line[5:].lstrip())
        elif not line.strip() and current:
            frames.append("\n".join(current))
            current = []
    if current:
        frames.append("\n".join(current))

    for frame in reversed(frames):
        try:
            data = jsonlib.loads(frame)
        except (TypeError, ValueError):  # noqa: S110 - a frame may be a keepalive
            continue
        if isinstance(data, dict):
            return data
    raise MCPRPCError("SSE response contained no JSON-RPC message")


def _decode_response(response: Any) -> dict:
    headers = getattr(response, "headers", None) or {}
    content_type = ""
    if hasattr(headers, "get"):
        content_type = str(headers.get("content-type", ""))
    if "text/event-stream" in content_type.lower():
        return _decode_sse(str(getattr(response, "text", "")))

    data = response.json()
    if not isinstance(data, dict):
        raise MCPRPCError("MCP server returned a non-object JSON-RPC response")
    return data


class _StdioMCPTransport:
    """JSON-RPC over newline-delimited stdio, with the httpx post/aclose shape."""

    def __init__(self, name: str, command: str, args: list, env: dict | None,
                 cwd: str | None = None, timeout: float = 30.0):
        self.name = name
        self.command = str(command)
        self.args = [str(a) for a in (args or [])]
        self.env = _resolve_env(env)
        self.cwd = str(cwd) if cwd else None
        self.timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[Any, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._stderr_reader: asyncio.Task | None = None

    async def start(self):
        process_env = None
        if self.env:
            process_env = {
                **os.environ,
                **{str(key): str(value) for key, value in self.env.items()},
            }
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
            cwd=self.cwd,
        )
        self._reader = asyncio.create_task(
            self._read_loop(), name=f"mcp-stdio-{self.name}"
        )
        self._stderr_reader = asyncio.create_task(
            self._read_stderr(), name=f"mcp-stdio-{self.name}-stderr"
        )
        log.info("Started stdio MCP server '%s' (pid=%s)", self.name, self._proc.pid)

    async def _read_loop(self):
        proc = self._proc
        if proc is None or proc.stdout is None:  # pragma: no cover - defensive
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    message = jsonlib.loads(text)
                except (TypeError, ValueError):
                    log.warning(
                        "Ignoring non-JSON line from MCP server '%s': %s", self.name, text[:200]
                    )
                    continue
                if not isinstance(message, dict):
                    continue
                future = self._pending.get(message.get("id"))
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("Stdio MCP reader for '%s' crashed: %s", self.name, e)
        finally:
            # Never leave a caller waiting forever if the server exits mid-call.
            for message_id, future in list(self._pending.items()):
                if not future.done():
                    future.set_result({
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "error": {
                            "code": -32000,
                            "message": f"MCP server '{self.name}' closed its output",
                        },
                    })

    async def _read_stderr(self):
        proc = self._proc
        if proc is None or proc.stderr is None:  # pragma: no cover - defensive
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if text:
                    log.debug("MCP '%s' stderr: %s", self.name, text)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("Stdio MCP stderr reader for '%s' stopped: %s", self.name, e)

    async def post(self, _url, json):
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError(f"MCP server '{self.name}' is not running")

        message_id = json.get("id")
        future = None
        if message_id is not None:
            future = asyncio.get_running_loop().create_future()
            self._pending[message_id] = future

        payload = (jsonlib.dumps(json) + "\n").encode("utf-8")
        try:
            proc.stdin.write(payload)
            await proc.stdin.drain()
        except Exception:
            if message_id is not None:
                self._pending.pop(message_id, None)
            raise

        # Notifications have no id and therefore no response to await.
        if future is None:
            return _MCPResponse(202, {"accepted": True})

        try:
            response = await asyncio.wait_for(future, timeout=self.timeout)
        finally:
            self._pending.pop(message_id, None)
        return _MCPResponse(200, response)

    async def aclose(self):
        for task in (self._reader, self._stderr_reader):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._reader = None
        self._stderr_reader = None

        proc = self._proc
        self._proc = None
        if proc is None:
            return

        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
            try:
                await proc.stdin.wait_closed()
            except Exception:  # pragma: no cover - best-effort close  # noqa: BLE001, S110
                pass
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except TimeoutError:  # pragma: no cover - stubborn process
                proc.kill()
                await proc.wait()
        log.info("Stopped stdio MCP server '%s'", self.name)


class MCPClient:
    def __init__(self):
        self._clients: dict[str, Any] = {}
        self._urls: dict[str, str] = {}
        self._configs: dict[str, dict] = {}
        self._fingerprints: dict[str, str] = {}
        self._tools: dict[str, MCPTool] = {}
        self._prompts: dict[str, list[MCPPrompt]] = {}
        self._resources: dict[str, list[MCPResource]] = {}
        self._instructions: dict[str, str] = {}
        self._status: dict[str, dict] = {}
        self._initialized = False

    async def initialize(self, servers_config: dict[str, dict]) -> list[str]:
        """Initialize connections to all configured MCP servers. Returns list of server names."""
        initialized = []
        for name, config in servers_config.items():
            self._status[name] = self._status_template(name, config)
            self._configs[name] = dict(config)
            self._fingerprints[name] = _fingerprint(config)
            try:
                await self._connect_server(name, config)
                initialized.append(name)
                log.info("Connected to MCP server: %s", name)
            except Exception as e:  # noqa: BLE001
                self._set_error(name, config, e)
                log.error("Failed to connect to MCP server '%s': %s", name, e)

        self._initialized = True
        return initialized

    async def reload(self, servers_config: dict[str, dict]) -> list[str]:
        """Reconcile live connections with a new server set.

        Servers removed from the set are disconnected and their tools pruned;
        servers whose connection details changed are reconnected; new servers
        connect. Servers already connected with unchanged details are left
        running. Returns the names that were (re)connected.
        """
        current = set(self._clients)
        desired = set(servers_config)
        for name in current - desired:
            await self._disconnect(name)

        to_connect = [
            name
            for name in desired
            if name not in current
            or self._fingerprints.get(name) != _fingerprint(servers_config[name])
        ]
        initialized = []
        for name in to_connect:
            config = servers_config[name]
            self._status[name] = self._status_template(name, config)
            self._configs[name] = dict(config)
            self._fingerprints[name] = _fingerprint(config)
            try:
                await self._connect_server(name, config)
                initialized.append(name)
                log.info("Connected to MCP server: %s", name)
            except Exception as e:  # noqa: BLE001
                self._set_error(name, config, e)
                log.error("Failed to connect to MCP server '%s': %s", name, e)
        self._initialized = True
        return initialized

    def _status_template(self, name: str, config: dict | None) -> dict:
        config = config or self._configs.get(name, {})
        return {
            "name": name,
            "connected": False,
            "transport": _transport_name(config),
            "error": "",
            "tools": [],
            "prompts": [],
            "resources": [],
            "instructions": "",
            "oauth_provider": _oauth_provider(config),
        }

    def _set_error(self, name: str, config: dict, error: Exception) -> None:
        status = self._status_template(name, config)
        status["error"] = str(error) or type(error).__name__
        self._status[name] = status

    def _append_error(self, name: str, message: str) -> None:
        status = self._status.setdefault(name, self._status_template(name))
        status["error"] = f"{status['error']}; {message}" if status["error"] else message

    async def _disconnect(self, name: str):
        """Close a server connection and prune everything it contributed."""
        client = self._clients.pop(name, None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover - best-effort close  # noqa: BLE001, S110
                pass
        for store in (self._urls, self._configs, self._fingerprints,
                      self._prompts, self._resources, self._instructions,
                      self._status):
            store.pop(name, None)
        prefix = f"mcp_{name}_"
        for tool_name in [t for t in self._tools if t.startswith(prefix)]:
            del self._tools[tool_name]
        log.info("Disconnected MCP server: %s", name)

    async def _oauth_headers(self, config: dict) -> dict:
        provider = _oauth_provider(config)
        if not provider:
            return {}
        from .oauth import oauth_manager

        token = await oauth_manager.get_token(provider)
        if not token or not token.access_token:
            raise RuntimeError(
                f"OAuth provider '{provider}' has no access token; "
                "complete the OAuth flow before connecting this MCP server"
            )
        return {"Authorization": f"{token.token_type or 'Bearer'} {token.access_token}"}

    async def _apply_oauth(self, name: str) -> None:
        """Refresh the bearer token before each request, never storing it in config."""
        provider = _oauth_provider(self._configs.get(name))
        if not provider:
            return
        headers = getattr(self._clients.get(name), "headers", None)
        if headers is None:
            return  # stdio transports carry no HTTP headers
        from .oauth import oauth_manager

        token = await oauth_manager.get_token(provider)
        if token and token.access_token:
            headers["Authorization"] = f"{token.token_type or 'Bearer'} {token.access_token}"
        else:
            headers.pop("Authorization", None)

    async def _connect_server(self, name: str, config: dict):
        """Connect to a single MCP server over stdio or HTTP/SSE."""
        command = config.get("command")
        url = config.get("url")
        transport_name = _transport_name(config)

        try:
            if command:
                timeout = config.get("timeout", 30.0)
                try:
                    timeout = float(timeout)
                except (TypeError, ValueError):
                    timeout = 30.0
                transport = _StdioMCPTransport(
                    name=name,
                    command=command,
                    args=config.get("args", []),
                    env=config.get("env", {}),
                    cwd=config.get("cwd"),
                    timeout=timeout,
                )
                await transport.start()
                self._clients[name] = transport
                self._urls[name] = ""
            elif url:
                headers = {
                    str(key): str(value)
                    for key, value in _resolve_env(config.get("headers", {})).items()
                }
                try:
                    timeout = float(config.get("timeout", 30.0))
                except (TypeError, ValueError):
                    timeout = 30.0
                auth_headers = await self._oauth_headers(config)
                headers.update(auth_headers)
                if transport_name == "sse":
                    headers.setdefault("Accept", "application/json, text/event-stream")
                headers["Content-Type"] = "application/json"
                self._clients[name] = httpx.AsyncClient(headers=headers, timeout=timeout)
                self._urls[name] = str(url)
            else:
                raise ValueError(
                    f"Server '{name}' needs either 'command' (stdio) or 'url' (HTTP/SSE)"
                )

            self._configs[name] = dict(config)
            self._fingerprints[name] = _fingerprint(config)
            status = self._status_template(name, config)
            status["transport"] = transport_name
            self._status[name] = status

            init_data = await self._post(name, {
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
            })
            result = init_data.get("result") or {}
            if isinstance(result, dict) and result.get("instructions"):
                self._instructions[name] = str(result["instructions"])

            # Notification: no id, so stdio does not wait for a response.
            await self._post(name, {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            })

            await self._discover_prompts(name)
            await self._discover_resources(name)
            await self._discover_tools(name)
        except Exception:
            # A half-open connection must not survive a failed handshake.
            await self._disconnect(name)
            raise

    async def _post(self, server_name: str, payload: dict) -> dict:
        client = self._clients.get(server_name)
        if client is None:
            raise RuntimeError(f"MCP server '{server_name}' not connected")
        await self._apply_oauth(server_name)
        response = await client.post(
            self._get_server_url(server_name),
            json=payload,
        )
        status_code = getattr(response, "status_code", 200)
        if status_code not in (200, 202):
            raise MCPHTTPError(str(getattr(response, "text", "")))
        if status_code == 202:
            return {}
        data = _decode_response(response)
        if "error" in data:
            detail = data["error"]
            message = (
                detail.get("message") if isinstance(detail, dict) else str(detail)
            )
            raise MCPRPCError(str(message))
        return data

    async def _discover_tools(self, server_name: str):
        """Discover tools from an MCP server."""
        try:
            data = await self._post(server_name, {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            })
            tools = (data.get("result") or {}).get("tools", [])

            for tool in tools:
                mcp_tool = MCPTool(
                    name=f"mcp_{server_name}_{tool['name']}",
                    description=tool.get("description", ""),
                    parameters=tool.get("inputSchema", {}),
                    server_name=server_name,
                )
                self._tools[mcp_tool.name] = mcp_tool
                log.debug("Discovered MCP tool: %s from %s", mcp_tool.name, server_name)
        except Exception as e:  # noqa: BLE001
            self._append_error(server_name, f"tools/list failed: {e}")
            log.error("Failed to discover tools from %s: %s", server_name, e)

    async def _discover_prompts(self, server_name: str):
        """Discover prompts so the admin status panel can show them."""
        try:
            data = await self._post(server_name, {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "prompts/list",
                "params": {},
            })
            prompts = (data.get("result") or {}).get("prompts", [])
            self._prompts[server_name] = [
                MCPPrompt(
                    name=str(prompt.get("name", "")),
                    description=str(prompt.get("description", "")),
                    arguments=prompt.get("arguments", []) or [],
                )
                for prompt in prompts
                if isinstance(prompt, dict) and prompt.get("name")
            ]
        except Exception as e:  # noqa: BLE001
            self._append_error(server_name, f"prompts/list failed: {e}")
            log.error("Failed to discover prompts from %s: %s", server_name, e)

    async def _discover_resources(self, server_name: str):
        """Discover resources so the admin status panel can show them."""
        try:
            data = await self._post(server_name, {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "resources/list",
                "params": {},
            })
            resources = (data.get("result") or {}).get("resources", [])
            self._resources[server_name] = [
                MCPResource(
                    uri=str(resource.get("uri", "")),
                    name=str(resource.get("name", "")),
                    description=str(resource.get("description", "")),
                    mime_type=resource.get("mimeType"),
                )
                for resource in resources
                if isinstance(resource, dict) and resource.get("uri")
            ]
        except Exception as e:  # noqa: BLE001
            self._append_error(server_name, f"resources/list failed: {e}")
            log.error("Failed to discover resources from %s: %s", server_name, e)

    def get_tools(self) -> list[MCPTool]:
        """Get all discovered MCP tools."""
        return list(self._tools.values())

    def get_tool_schemas(self) -> list[dict]:
        """Get schemas for all MCP tools."""
        return [tool.schema() for tool in self._tools.values()]

    def get_status(self) -> list[dict]:
        """Connection/discovery status for every known server, including failures."""
        statuses = []
        for name in sorted(set(self._configs) | set(self._status) | set(self._clients)):
            status = dict(self._status.get(name) or self._status_template(name))
            status["connected"] = name in self._clients
            status["tools"] = [tool.name for tool in self._tools.values()
                               if tool.server_name == name]
            status["prompts"] = [prompt.to_dict()
                                 for prompt in self._prompts.get(name, [])]
            status["resources"] = [resource.to_dict()
                                   for resource in self._resources.get(name, [])]
            status["instructions"] = self._instructions.get(name, "")
            statuses.append(status)
        return statuses

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call an MCP tool."""
        tool = self._tools.get(tool_name)
        if not tool:
            return f"Error: unknown MCP tool '{tool_name}'"

        if tool.server_name not in self._clients:
            return f"Error: MCP server '{tool.server_name}' not connected"

        try:
            data = await self._post(tool.server_name, {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {
                    "name": tool.name.removeprefix(f"mcp_{tool.server_name}_"),
                    "arguments": arguments,
                },
            })
        except MCPHTTPError as e:
            return f"Error: MCP tool call failed: {e.text}"
        except MCPRPCError as e:
            return f"Error: MCP tool '{tool_name}' failed: {e.message}"
        except Exception as e:
            log.exception("MCP tool call failed")
            return f"Error calling MCP tool '{tool_name}': {e}"

        content = (data.get("result") or {}).get("content", [])
        if isinstance(content, list):
            return "\n".join(
                item.get("text", "") for item in content if item.get("type") == "text"
            )
        elif isinstance(content, str):
            return content
        else:
            return jsonlib.dumps((data.get("result") or {}))

    def _get_server_url(self, server_name: str) -> str:
        """Get the URL for an MCP server (empty string for stdio transports)."""
        return self._urls.get(server_name, "")

    async def close(self):
        """Close all MCP client connections."""
        for client in list(self._clients.values()):
            await client.aclose()
        self._clients.clear()
        self._urls.clear()
        self._configs.clear()
        self._fingerprints.clear()
        self._tools.clear()
        self._prompts.clear()
        self._resources.clear()
        self._instructions.clear()
        self._status.clear()
        self._initialized = False


class MCPToolAdapter:
    """Adapts MCP tools to work with the CodeAssist tool system."""

    def __init__(self, mcp_client: MCPClient):
        self.mcp_client = mcp_client

    async def execute(self, name: str, arguments: dict) -> "ToolResult":
        """Execute an MCP tool through the adapter.

        Wraps the raw text in a ToolResult: the agent reads `result.output`, so
        returning a bare str here aborted the turn. MCPToolWrapper is the
        per-tool path actually registered with the registry; this adapter is
        kept consistent with it.
        """
        from .tools import ToolResult

        output = await self.mcp_client.call_tool(name, arguments)
        return ToolResult(output=output, error=output.startswith("Error:"))

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
        from .tools import ToolResult
        output = await self._mcp_client.call_tool(self.name, kwargs)
        is_error = output.startswith("Error:")
        return ToolResult(output=output, error=is_error)
