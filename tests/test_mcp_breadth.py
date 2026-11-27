"""C3 MCP breadth: stdio transport, OAuth bearer tokens, discovery status and
tool-registry synchronisation.

The earlier client only spoke plain HTTP POST to one URL, registered wrappers
before discovery had happened, and never repaired the registry after a reload.
These tests exercise each of those paths without requiring a real MCP server or
network access.
"""
import asyncio
import json
import sys
import types

import pytest

from codeassist import mcp_client as mcp_mod
from codeassist import server
from codeassist.mcp_client import MCPClient, MCPTool
from codeassist.oauth import OAuthToken, oauth_manager
from codeassist.tools import ToolRegistry

# A tiny newline-delimited JSON-RPC MCP server. It answers the four discovery
# methods plus tools/call, so stdio support is tested end-to-end (real process,
# real pipes) rather than by mocking away the transport.
_STDIO_SERVER = r"""
import json, sys
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method, msg_id = msg.get('method'), msg.get('id')
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {},
                  'serverInfo': {'name': 'fake', 'version': '1'},
                  'instructions': 'Use the fake tool'}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'ping', 'description': 'ping',
                             'inputSchema': {'type': 'object', 'properties': {},
                                             'required': []}}]}
    elif method == 'prompts/list':
        result = {'prompts': [{'name': 'hello', 'description': 'say hello',
                               'arguments': []}]}
    elif method == 'resources/list':
        result = {'resources': [{'uri': 'file://fake/readme.md', 'name': 'readme',
                                 'description': 'a doc', 'mimeType': 'text/markdown'}]}
    elif method == 'tools/call':
        result = {'content': [{'type': 'text',
                               'text': 'pong:' + json.dumps(msg['params']['arguments'])}]}
    elif method == 'notifications/initialized':
        continue
    else:
        result = {}
    if msg_id is not None:
        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg_id,
                                     'result': result}) + '\n')
        sys.stdout.flush()
"""


def _response(method: str) -> types.SimpleNamespace:
    if method == "initialize":
        result = {"capabilities": {}, "instructions": "be helpful"}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "echo",
                             "inputSchema": {"type": "object"}}]}
    elif method == "prompts/list":
        result = {"prompts": [{"name": "summarize", "description": "summarize"}]}
    elif method == "resources/list":
        result = {"resources": [{"uri": "file://doc", "name": "doc"}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "ok"}]}
    else:
        result = {}
    body = {"jsonrpc": "2.0", "id": 1, "result": result}
    return types.SimpleNamespace(
        status_code=200,
        text=json.dumps(body),
        headers={"content-type": "application/json"},
        json=lambda: body,
    )


class _NoopClose:
    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_stdio_server_connects_discovers_and_calls():
    client = MCPClient()
    config = {"command": sys.executable, "args": ["-u", "-c", _STDIO_SERVER], "env": {}}
    try:
        assert await client.initialize({"fake": config}) == ["fake"]

        status = client.get_status()[0]
        assert status["connected"] is True
        assert status["transport"] == "stdio"
        assert status["instructions"] == "Use the fake tool"
        assert [p["name"] for p in status["prompts"]] == ["hello"]
        assert [r["uri"] for r in status["resources"]] == ["file://fake/readme.md"]
        assert [tool.name for tool in client.get_tools()] == ["mcp_fake_ping"]
        assert client._urls["fake"] == "", "stdio servers have no HTTP URL"

        output = await client.call_tool("mcp_fake_ping", {"value": 7})
        assert output.startswith("pong:")
        assert '"value": 7' in output
        assert config["command"] == sys.executable  # config was not rewritten
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_oauth_token_is_fetched_per_request_and_never_exposed(monkeypatch):
    issued = []

    async def fake_get_token(provider):
        token = OAuthToken(provider=provider, access_token=f"token-{len(issued) + 1}")
        issued.append(token.access_token)
        return token

    monkeypatch.setattr(oauth_manager, "get_token", fake_get_token)

    class FakeHTTPX:
        instances = []

        def __init__(self, headers=None, timeout=None):
            self.headers = dict(headers or {})
            self.timeout = timeout
            self.instances.append(self)

        async def post(self, url, json):
            return _response(json["method"])

        async def aclose(self):
            pass

    monkeypatch.setattr(mcp_mod.httpx, "AsyncClient", FakeHTTPX)

    client = MCPClient()
    config = {"url": "https://mcp.example.test/mcp", "oauth_provider": "github"}
    try:
        assert await client.initialize({"remote": config}) == ["remote"]
        http_client = FakeHTTPX.instances[0]
        assert http_client.headers["Authorization"].startswith("Bearer token-")
        assert len(issued) > 1, "token must be refreshed on later requests too"
        assert await client.call_tool("mcp_remote_echo", {}) == "ok"

        status = client.get_status()[0]
        assert status["oauth_provider"] == "github"
        assert "token-" not in json.dumps(status), "status must not leak bearer tokens"
        assert config == {"url": "https://mcp.example.test/mcp",
                          "oauth_provider": "github"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_missing_oauth_token_surfaces_actionable_error(monkeypatch):
    async def no_token(provider):
        return None

    monkeypatch.setattr(oauth_manager, "get_token", no_token)
    client = MCPClient()
    assert await client.initialize({"remote": {
        "url": "https://mcp.example.test/mcp", "oauth_provider": "github",
    }}) == []
    status = client.get_status()[0]
    assert "github" in status["error"]
    assert "access token" in status["error"]


@pytest.mark.asyncio
async def test_sse_responses_are_decoded(monkeypatch):
    body = (
        'event: message\n'
        'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
    )

    class SSEResponse:
        status_code = 200
        text = body
        headers = {"content-type": "text/event-stream"}

        def json(self):
            raise AssertionError("SSE bodies must be parsed from data: frames")

    class FakeHTTPX:
        def __init__(self, headers=None, timeout=None):
            self.headers = dict(headers or {})

        async def post(self, url, json):
            return SSEResponse()

        async def aclose(self):
            pass

    monkeypatch.setattr(mcp_mod.httpx, "AsyncClient", FakeHTTPX)
    client = MCPClient()
    try:
        assert await client.initialize({"stream": {
            "url": "https://mcp.example.test/sse", "transport": "sse",
        }}) == ["stream"]
        assert client.get_status()[0]["transport"] == "sse"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_reload_reconnects_when_stdio_args_change(monkeypatch):
    client = MCPClient()
    connected = []

    async def fake_connect(name, config):
        connected.append(name)
        client._clients[name] = _NoopClose()

    monkeypatch.setattr(client, "_connect_server", fake_connect)

    await client.initialize({"local": {"command": "server", "args": ["--one"]}})
    assert connected == ["local"]

    assert await client.reload({"local": {"command": "server", "args": ["--two"]}}) == ["local"]
    assert connected == ["local", "local"], "changed args must reconnect a stdio server"

    assert await client.reload({"local": {"command": "server", "args": ["--two"]}}) == []
    assert len(connected) == 2, "an unchanged stdio server must be left running"


@pytest.mark.asyncio
async def test_discovered_tools_are_registered_with_agent_registry(monkeypatch, tmp_path):
    client = MCPClient()
    client._tools["mcp_demo_ping"] = MCPTool(
        name="mcp_demo_ping", description="ping", parameters={}, server_name="demo"
    )
    registry = ToolRegistry(tmp_path)
    sentinel = types.SimpleNamespace(name="keep", parameters={})
    registry.register(sentinel)

    monkeypatch.setattr(server, "mcp_client", client)
    monkeypatch.setattr(server, "tools", registry)

    server.sync_mcp_tools()
    assert "mcp_demo_ping" in registry.list_names()
    assert "keep" in registry.list_names(), "sync must not clear built-in tools"

    client._tools.clear()
    server.sync_mcp_tools()
    assert "mcp_demo_ping" not in registry.list_names()
    assert "keep" in registry.list_names()


@pytest.mark.asyncio
async def test_init_mcp_registers_tools_discovered_after_registry_creation(monkeypatch, tmp_path):
    class FakeMCPClient:
        def __init__(self):
            self.tools = [MCPTool(
                name="mcp_late_ping", description="ping", parameters={},
                server_name="late",
            )]

        async def initialize(self, servers):
            return ["late"]

        def get_tools(self):
            return list(self.tools)

    async def no_servers(cfg):
        return {}

    registry = ToolRegistry(tmp_path)
    monkeypatch.setattr(server, "_config", server.get_config())
    monkeypatch.setattr(server, "mcp_client", FakeMCPClient())
    monkeypatch.setattr(server, "tools", registry)
    monkeypatch.setattr(server, "_merged_mcp_servers", no_servers)

    await server.init_mcp()
    assert "mcp_late_ping" in registry.list_names()


@pytest.mark.asyncio
async def test_dynamic_tool_reload_preserves_mcp_wrappers(monkeypatch, tmp_path):
    client = MCPClient()
    client._tools["mcp_keep_ping"] = MCPTool(
        name="mcp_keep_ping", description="ping", parameters={}, server_name="keep"
    )
    registry = ToolRegistry(tmp_path)
    monkeypatch.setattr(server, "mcp_client", client)
    monkeypatch.setattr(server, "tools", registry)
    server.sync_mcp_tools()
    assert "mcp_keep_ping" in registry.list_names()

    from codeassist.dynamic_tools import DynamicToolLoader

    def clear_only(self, reg):
        reg.clear()

    monkeypatch.setattr(DynamicToolLoader, "reload_registry", clear_only)
    await server.reload_all_tools()
    assert "mcp_keep_ping" in registry.list_names(), (
        "dynamic-tool reload must re-add MCP wrappers after clearing the registry"
    )


@pytest.mark.asyncio
async def test_connection_failure_is_reported_in_status(monkeypatch):
    class FailingHTTPX:
        def __init__(self, headers=None, timeout=None):
            self.headers = dict(headers or {})

        async def post(self, url, json):
            raise RuntimeError("connection refused")

        async def aclose(self):
            pass

    monkeypatch.setattr(mcp_mod.httpx, "AsyncClient", FailingHTTPX)
    client = MCPClient()
    assert await client.initialize({"bad": {"url": "http://127.0.0.1:1/mcp"}}) == []
    status = client.get_status()[0]
    assert status["connected"] is False
    assert "connection refused" in status["error"]


def test_status_route_reports_live_client(live_client, monkeypatch):
    # MCP is disabled in the test config, so the route must say so explicitly.
    assert live_client.get("/api/mcp/status").json() == {"enabled": False, "servers": []}

    class FakeClient:
        def get_status(self):
            return [{"name": "remote", "connected": False,
                     "error": "handshake failed", "tools": [], "prompts": [],
                     "resources": [], "instructions": "", "transport": "sse",
                     "oauth_provider": ""}]

        async def close(self):
            pass

    monkeypatch.setattr(server, "mcp_client", FakeClient())
    response = live_client.get("/api/mcp/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["servers"][0]["error"] == "handshake failed"


@pytest.mark.asyncio
async def test_mcp_prompt_and_resource_tools_are_registered_and_callable(monkeypatch, tmp_path):
    client = MCPClient()
    client._prompts["demo"] = [mcp_mod.MCPPrompt(name="summarize", description="sum", arguments=[])]
    client._resources["demo"] = [mcp_mod.MCPResource(uri="file://readme.md", name="readme")]
    client._clients["demo"] = _NoopClose()

    async def fake_get_prompt(server_name, prompt_name, arguments=None):
        return f"prompt:{server_name}:{prompt_name}:{arguments}"

    async def fake_read_resource(server_name, uri):
        return f"resource:{server_name}:{uri}"

    monkeypatch.setattr(client, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(client, "read_resource", fake_read_resource)

    registry = ToolRegistry(tmp_path)
    monkeypatch.setattr(server, "mcp_client", client)
    monkeypatch.setattr(server, "tools", registry)
    server.sync_mcp_tools()

    assert "mcp_get_prompt" in registry.list_names()
    assert "mcp_read_resource" in registry.list_names()
    prompt_result = await registry.execute("mcp_get_prompt", {
        "server": "demo", "name": "summarize", "arguments": {"topic": "x"},
    })
    assert not prompt_result.error
    assert "prompt:demo:summarize:{'topic': 'x'}" in prompt_result.output
    resource_result = await registry.execute("mcp_read_resource", {
        "server": "demo", "uri": "file://readme.md",
    })
    assert not resource_result.error
    assert resource_result.output == "resource:demo:file://readme.md"


@pytest.mark.asyncio
async def test_mcp_prompt_skill_discovery_registers_virtual_skills(tmp_path):
    from codeassist.skills import SkillRegistry

    client = MCPClient()
    client._prompts["demo"] = [mcp_mod.MCPPrompt(
        name="review", description="Review code", arguments=[{"name": "path", "required": True}],
    )]
    client._status["demo"] = {
        "name": "demo", "connected": True, "transport": "stdio", "error": "",
        "tools": [], "prompts": [], "resources": [], "instructions": "", "oauth_provider": "",
    }
    registry = SkillRegistry(tmp_path)
    registry.discover()
    count = registry.set_mcp_prompt_skills(client)
    assert count == 1
    skill = registry.get_skill("mcp:demo/review")
    assert skill is not None
    assert skill.source == "mcp:demo"
    assert registry.category_for(skill) == SkillRegistry.CATEGORY_MCP
    assert "mcp_get_prompt" in skill.content
    assert not registry.is_user_owned("mcp:demo/review")


@pytest.mark.asyncio
async def test_mcp_notification_fans_out_to_automation_events(monkeypatch):
    from codeassist import automations as automations_mod

    events = []

    class FakeEngine:
        async def trigger_event(self, name):
            events.append(name)
            return 1

    monkeypatch.setattr(automations_mod, "automation_engine", FakeEngine())
    client = MCPClient()
    client.notification_handler = server._handle_mcp_notification
    monkeypatch.setattr(server, "skill_registry", None)
    monkeypatch.setattr(server, "tools", None)
    monkeypatch.setattr(server, "mcp_client", client)

    await client._handle_notification("demo", "notifications/tools/list_changed", {})
    assert "mcp:tools_changed" in events
    assert "mcp:demo:tools_changed" in events
    assert "mcp:notifications/tools/list_changed" in events


@pytest.mark.asyncio
async def test_server_mcp_notification_hook_triggers_automation_event(monkeypatch):
    from codeassist import automations as automations_mod

    events = []

    class FakeEngine:
        async def trigger_event(self, name):
            events.append(name)
            return 1

    monkeypatch.setattr(automations_mod, "automation_engine", FakeEngine())
    monkeypatch.setattr(server, "skill_registry", None)
    await server._handle_mcp_notification("demo", "notifications/prompts/list_changed", {})
    assert "mcp:prompts_changed" in events
    assert "mcp:demo:prompts_changed" in events


@pytest.mark.asyncio
async def test_websocket_server_connects_discovers_and_calls(monkeypatch):
    from codeassist.mcp_client import _WebSocketMCPTransport

    sent = []

    class FakeWS:
        def __init__(self):
            self._messages: asyncio.Queue[str] = asyncio.Queue()
            self.closed = False

        async def send(self, data):
            msg = json.loads(data)
            sent.append(msg)
            method = msg.get("method")
            msg_id = msg.get("id")
            if msg_id is None:
                return
            if method == "initialize":
                result = {"capabilities": {}, "instructions": "ws"}
            elif method == "tools/list":
                result = {"tools": [{"name": "ping", "description": "ping", "inputSchema": {"type": "object"}}]}
            elif method == "prompts/list":
                result = {"prompts": []}
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "tools/call":
                result = {"content": [{"type": "text", "text": "ws-pong"}]}
            else:
                result = {}
            await self._messages.put(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}))

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.closed and self._messages.empty():
                raise StopAsyncIteration
            try:
                return await asyncio.wait_for(self._messages.get(), timeout=30)
            except asyncio.TimeoutError as exc:
                if self.closed:
                    raise StopAsyncIteration from exc
                raise

        async def close(self):
            self.closed = True

    fake_ws = FakeWS()

    async def fake_connect(url, **kwargs):
        assert url == "ws://example.test/mcp"
        return fake_ws

    import websockets

    monkeypatch.setattr(websockets, "connect", fake_connect)

    client = MCPClient()
    try:
        connected = await client.initialize({"demo": {"url": "ws://example.test/mcp", "transport": "websocket"}})
        assert connected == ["demo"]
        status = client.get_status()[0]
        assert status["transport"] == "websocket"
        assert status["connected"] is True
        output = await client.call_tool("mcp_demo_ping", {})
        assert output == "ws-pong"
        assert any(m.get("method") == "tools/call" for m in sent)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_oauth_start_route_returns_authorization_url(monkeypatch, tmp_path):
    import codeassist.session as session_mod
    from codeassist.oauth import OAuthProviderConfig, oauth_manager
    from codeassist.routes.mcp import start_mcp_oauth
    from codeassist.session import MCPServer

    old_path = session_mod.DB_PATH
    session_mod.DB_PATH = tmp_path / "test.db"
    try:
        await session_mod.init_db()

        oauth_manager._providers.pop("github", None)
        oauth_manager._tokens.pop("github", None)
        oauth_manager._pending_states.clear()
        oauth_manager.register_provider(OAuthProviderConfig(
            name="github",
            client_id="cid",
            client_secret="secret",
            auth_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            redirect_uri="http://localhost:7878/api/oauth/callback",
            default_scopes=["repo"],
        ))

        row = await MCPServer.create("gh-mcp", {"url": "https://example.test/mcp", "oauth_provider": "github"})
        monkeypatch.setattr(server, "get_config", lambda: types.SimpleNamespace(mcp=types.SimpleNamespace(enabled=True)))

        body = await start_mcp_oauth(row.id, {})
        assert body["ok"] is True
        assert body["provider"] == "github"
        assert "github.com/login/oauth/authorize" in body["authorization_url"]
        assert body["state"]
    finally:
        session_mod.DB_PATH = old_path
