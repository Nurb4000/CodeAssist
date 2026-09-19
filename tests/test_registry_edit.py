"""Tests for registry edit endpoints (admin page UX: edit/remove for every item).

Covers the PUT /api/mcp/servers/{id}, PUT /api/lsp/servers/{id} and PATCH
/api/agents/{id} endpoints plus the underlying model update()/set_enabled()
methods added in session.py and AgentManager.update_agent.
"""
import pytest
from fastapi.testclient import TestClient

import codeassist.server as server
from codeassist.session import MCPServer, LSPServer, init_db


# --- Model-level: MCPServer -------------------------------------------------

class TestMCPServerModel:
    @pytest.mark.asyncio
    async def test_update_name_and_config(self):
        await init_db()
        s = await MCPServer.create("srv", {"command": "npx", "args": ["-y", "@mcps/env"]})
        await s.update(name="renamed", config={"command": "node"})
        updated = await MCPServer.get(s.id)
        cfg = await updated.get_config()  # config is merged into the returned dict
        assert cfg["name"] == "renamed"
        assert cfg["command"] == "node"

    @pytest.mark.asyncio
    async def test_set_enabled_toggles_flag(self):
        await init_db()
        s = await MCPServer.create("srv", {})
        assert any(x["id"] == s.id for x in await MCPServer.list_all())
        await s.set_enabled(False)
        assert not any(x["id"] == s.id for x in await MCPServer.list_all())
        await s.set_enabled(True)
        assert any(x["id"] == s.id for x in await MCPServer.list_all())


# --- Model-level: LSPServer -------------------------------------------------

class TestLSPServerModel:
    @pytest.mark.asyncio
    async def test_update_fields(self):
        await init_db()
        s = await LSPServer.create("py", "python -m pyright-langserver", ["--stdio"], ["python"])
        await s.update(command="python3 -m lspy", languages=["python", "typescript"])
        row = await LSPServer.get(s.id)
        cfg = await row.get_config()
        assert cfg["command"] == "python3 -m lspy"
        assert cfg["languages"] == ["python", "typescript"]

    @pytest.mark.asyncio
    async def test_set_enabled(self):
        await init_db()
        s = await LSPServer.create("py", "cmd")
        assert any(x["id"] == s.id for x in await LSPServer.list_all())
        await s.set_enabled(False)
        assert not any(x["id"] == s.id for x in await LSPServer.list_all())
        await s.set_enabled(True)
        assert any(x["id"] == s.id for x in await LSPServer.list_all())


# --- Route-level: MCP -------------------------------------------------------

class TestMCPRoutes:
    def _enable(self):
        cfg = server.get_config()
        cfg.mcp.enabled = True
        return cfg

    def test_put_update_mcp(self, live_client):
        self._enable()
        created = live_client.post("/api/mcp/servers", json={"name": "env", "config": {"command": "npx"}})
        assert created.status_code == 200
        sid = created.json()["id"]

        r = live_client.put(f"/api/mcp/servers/{sid}", json={
            "name": "env-renamed", "config": {"command": "node"}, "enabled": False,
        })
        assert r.status_code == 200, r.text

        servers = live_client.get("/api/mcp/servers").json()["servers"]
        assert servers == [], "disabled server should not appear in the enabled list"

    def test_put_update_unknown_returns_404(self, live_client):
        self._enable()
        r = live_client.put("/api/mcp/servers/does-not-exist", json={"name": "x"})
        assert r.status_code == 404

    def test_put_when_mcp_disabled_returns_400(self, live_client):
        cfg = server.get_config()
        cfg.mcp.enabled = False
        r = live_client.put("/api/mcp/servers/whatever", json={"name": "x"})
        assert r.status_code == 400


# --- Route-level: LSP -------------------------------------------------------

class TestLSPRoutes:
    def test_put_update_lsp(self, live_client):
        created = live_client.post("/api/lsp/servers", json={
            "name": "py", "command": "python -m pyright-langserver", "args": ["--stdio"], "languages": ["python"],
        })
        assert created.status_code == 200
        sid = created.json()["id"]

        r = live_client.put(f"/api/lsp/servers/{sid}", json={
            "name": "py", "command": "python3 -m pyright-langserver",
            "args": ["--stdio"], "languages": ["python", "typescript"], "enabled": False,
        })
        assert r.status_code == 200, r.text
        assert live_client.get("/api/lsp/servers").json()["servers"] == []

    def test_put_update_unknown_lsp_404(self, live_client):
        r = live_client.put("/api/lsp/servers/nope", json={"name": "x"})
        assert r.status_code == 404


# --- Route-level: agents ----------------------------------------------------

class TestAgentRoutes:
    def test_patch_update_custom_agent(self, live_client):
        r = live_client.post("/api/agents", json={
            "name": "edit-me", "description": "orig", "model": "gpt-4o",
        })
        assert r.status_code == 200

        r = live_client.patch("/api/agents/edit-me", json={
            "description": "updated desc", "model": "gpt-4o-mini", "max_iterations": 25,
        })
        assert r.status_code == 200, r.text

        agents = {a["id"]: a for a in live_client.get("/api/agents").json()}
        assert agents["edit-me"]["description"] == "updated desc"
        assert agents["edit-me"]["model"] == "gpt-4o-mini"

    def test_patch_builtin_agent_400(self, live_client):
        r = live_client.patch("/api/agents/default", json={"description": "nope"})
        assert r.status_code == 400
        assert "built-in" in r.json()["detail"].lower()

    def test_patch_unknown_agent_404(self, live_client):
        r = live_client.patch("/api/agents/no-such-agent", json={"description": "x"})
        assert r.status_code == 404
