"""Tests for MCPClient.call_tool error handling.

An MCP server reports a failed tool call with a JSON-RPC ``error`` object and no
``result``. The old code read ``result`` unconditionally, so an error response
collapsed to an empty string and the adapter marked ``error=False`` -- a failed
tool looked like an empty success. Now a JSON-RPC ``error`` is surfaced as an
``Error: ...`` string so the tool system flags it as a failure.
"""

import types

import pytest

from codeassist.mcp_client import MCPClient, MCPTool, MCPToolAdapter


def _client(tool_name="mcp_foo_bar", server="foo"):
    client = MCPClient()
    client._tools[tool_name] = MCPTool(
        name=tool_name, description="d", parameters={}, server_name=server
    )
    return client, tool_name, server


def _install_post(client, server, body):
    async def fake_post(url, json):
        return types.SimpleNamespace(
            status_code=200, text=str(body), json=lambda: body
        )

    client._clients[server] = types.SimpleNamespace(post=fake_post)


@pytest.mark.asyncio
async def test_success_response_returns_text_content():
    client, tool_name, server = _client()
    _install_post(
        client,
        server,
        {"result": {"content": [{"type": "text", "text": "ok"}]}},
    )

    assert await client.call_tool(tool_name, {}) == "ok"


@pytest.mark.asyncio
async def test_adapter_marks_jsonrpc_error_as_failure():
    client, tool_name, server = _client()
    _install_post(
        client,
        server,
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "boom"}},
    )

    result = await MCPToolAdapter(client).execute(tool_name, {})

    assert result.error, "a JSON-RPC error must surface as a tool failure"
    assert "boom" in result.output


@pytest.mark.asyncio
async def test_call_tool_wraps_error_message():
    client, tool_name, server = _client()
    _install_post(
        client,
        server,
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "bad args"}},
    )

    output = await client.call_tool(tool_name, {})

    assert output.startswith("Error:")
    assert "bad args" in output


@pytest.mark.asyncio
async def test_connect_server_preserves_config_env_markers(monkeypatch):
    """Connecting to an MCP server must not rewrite the caller's config -- in
    particular the ``env`` sub-dict's ``env:<NAME>`` markers must survive so a
    reload re-resolves them. The old code resolved env in place, mutating the
    persisted config on every connect (and dropping the markers on reload)."""
    from codeassist import mcp_client

    class _Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"result": {"tools": []}}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def post(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr(mcp_client.httpx, "AsyncClient", _FakeClient)

    config = {"url": "http://example/mcp", "env": {"API_KEY": "env:MY_API_KEY"}}
    await mcp_client.MCPClient()._connect_server("foo", config)

    # Markers intact -- the config dict was not mutated in place.
    assert config["env"] == {"API_KEY": "env:MY_API_KEY"}


@pytest.mark.asyncio
async def test_mcp_prefix_stripped_only_once():
    """A tool whose name contains the server prefix more than once must have
    only the leading prefix stripped before the call is sent to the MCP server.
    str.replace strips every occurrence; removeprefix strips a single leading
    one, which is what the server expects."""
    client, tool_name, server = _client(tool_name="mcp_foo_mcp_foo_bar", server="foo")

    captured = {}

    async def fake_post(url, json):
        captured["name"] = json["params"]["name"]
        return types.SimpleNamespace(
            status_code=200,
            text="x",
            json=lambda: {"result": {"content": [{"type": "text", "text": "ok"}]}},
        )

    client._clients[server] = types.SimpleNamespace(post=fake_post)

    assert await client.call_tool(tool_name, {}) == "ok"
    # Only the leading ``mcp_foo_`` is removed; the inner occurrence stays.
    assert captured["name"] == "mcp_foo_bar"
