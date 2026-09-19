"""Tests for registry edit endpoints (admin page UX: edit/remove for every item).

Covers the PUT /api/mcp/servers/{id}, PUT /api/lsp/servers/{id} and PATCH
/api/agents/{id} endpoints plus the underlying model update()/set_enabled()
methods added in session.py and AgentManager.update_agent.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

import codeassist.server as server
from codeassist.session import MCPServer, LSPServer, init_db


async def _flush_reloads():
    """Await any background reconcile tasks spawned by edit routes.

    Admin edits fire reloads off the request path (server.spawn_reload), so
    tests must drain _reload_tasks before asserting a reload ran.
    """
    pending = list(server._reload_tasks)
    if pending:
        await asyncio.gather(*pending)


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


# --- Boot-time merge: config.toml servers + admin-managed DB servers ---------

class TestMergedMCPServers:
    @pytest.mark.asyncio
    async def test_combines_toml_and_db_servers(self):
        await init_db()
        cfg = server.get_config()
        cfg.mcp.servers = {"toml_srv": {"url": "http://toml/mcp"}}
        await MCPServer.create("db_srv", {"url": "http://db/mcp"})

        merged = await server._merged_mcp_servers(cfg)
        assert set(merged) == {"toml_srv", "db_srv"}
        assert merged["db_srv"] == {"url": "http://db/mcp"}

    @pytest.mark.asyncio
    async def test_toml_wins_on_name_collision(self):
        await init_db()
        cfg = server.get_config()
        cfg.mcp.servers = {"shared": {"url": "http://toml"}}
        await MCPServer.create("shared", {"url": "http://db"})

        merged = await server._merged_mcp_servers(cfg)
        assert merged["shared"] == {"url": "http://toml"}

    @pytest.mark.asyncio
    async def test_skips_malformed_db_config(self):
        await init_db()
        cfg = server.get_config()
        cfg.mcp.servers = {}
        # Store an invalid JSON blob in the config column.
        await MCPServer.create("broken", {"url": "http://ok"})
        import codeassist.session as session_mod

        async with session_mod.get_db() as db:
            await db.execute(
                "UPDATE mcp_servers SET config = ? WHERE name = 'broken'",
                ("not-json",),
            )

        merged = await server._merged_mcp_servers(cfg)
        assert "broken" not in merged  # skipped, startup did not raise
        assert merged == {}


# --- Boot-time merge: LSP specs (config.toml + DB) --------------------------

class TestMergedLSPSpecs:
    @pytest.mark.asyncio
    async def test_combines_toml_and_db_servers(self):
        await init_db()
        cfg = server.get_config()
        cfg.lsp.servers = {"toml_srv": {"command": "toml-lsp", "args": ["--toml"], "languages": ["yaml"]}}
        await LSPServer.create("db_srv", "db-lsp", ["--db"], ["python"])

        specs = await server._merged_lsp_specs(cfg)
        assert set(specs) == {"toml_srv", "db_srv"}
        assert specs["db_srv"] == {"command": "db-lsp", "args": ["--db"], "languages": ["python"]}

    @pytest.mark.asyncio
    async def test_toml_wins_on_name_collision(self):
        await init_db()
        cfg = server.get_config()
        cfg.lsp.servers = {"shared": {"command": "toml-lsp", "args": [], "languages": ["json"]}}
        await LSPServer.create("shared", "db-lsp", [], ["py"])

        specs = await server._merged_lsp_specs(cfg)
        assert specs["shared"]["command"] == "toml-lsp"

    @pytest.mark.asyncio
    async def test_skips_malformed_db_args(self):
        await init_db()
        cfg = server.get_config()
        cfg.lsp.servers = {}
        await LSPServer.create("broken", "cmd", ["--ok"], ["py"])
        import codeassist.session as session_mod

        async with session_mod.get_db() as db:
            await db.execute(
                "UPDATE lsp_servers SET args = 'not-json' WHERE name = 'broken'",
            )

        specs = await server._merged_lsp_specs(cfg)
        assert "broken" not in specs
        assert specs == {}

    @pytest.mark.asyncio
    async def test_empty_when_nothing_configured(self):
        await init_db()
        cfg = server.get_config()
        cfg.lsp.servers = {}
        specs = await server._merged_lsp_specs(cfg)
        assert specs == {}


class TestStartLSPServers:
    @pytest.mark.asyncio
    async def test_starts_toml_and_db_servers(self):
        await init_db()
        from codeassist.session import LSPServer

        cfg = server.get_config()
        cfg.lsp.enabled = True
        cfg.lsp.servers = {"toml_srv": {"command": "toml-lsp", "args": [], "languages": ["yaml"]}}
        await LSPServer.create("db_srv", "db-lsp", ["--db"], ["python"])

        started = []

        class FakeClient:
            async def start_server(self, **kwargs):
                started.append(kwargs)

        await server._start_lsp_servers(cfg, FakeClient())
        names = {s["name"] for s in started}
        assert names == {"toml_srv", "db_srv"}
        db_spec = next(s for s in started if s["name"] == "db_srv")
        assert db_spec["command"] == "db-lsp" and db_spec["args"] == ["--db"]

    @pytest.mark.asyncio
    async def test_noop_when_disabled(self):
        await init_db()
        cfg = server.get_config()
        cfg.lsp.enabled = False
        started = []

        class FakeClient:
            async def start_server(self, **kwargs):
                started.append(kwargs)

        await server._start_lsp_servers(cfg, FakeClient())
        assert started == []

    @pytest.mark.asyncio
    async def test_bad_server_does_not_abort_loop(self):
        await init_db()
        cfg = server.get_config()
        cfg.lsp.enabled = True
        cfg.lsp.servers = {"good": {"command": "ok", "args": [], "languages": []}}

        class FailingClient:
            async def start_server(self, **kwargs):
                if kwargs["name"] == "bad":
                    raise RuntimeError("boom")
                # 'good' starts first (toml precedence order), then 'bad' fails

        # Register a bad DB server so the loop hits the failing path.
        from codeassist.session import LSPServer

        await LSPServer.create("bad", "nope", ["x"], ["py"])
        started = []

        class RecordingClient:
            async def start_server(self, **kwargs):
                started.append(kwargs["name"])
                if kwargs["name"] == "bad":
                    raise RuntimeError("boom")

        await server._start_lsp_servers(cfg, RecordingClient())
        # 'good' was attempted before 'bad' raised; loop continued past the error.
        assert "good" in started


class _FakeHTTPX:
    async def aclose(self):
        pass


class TestMCPClientReload:
    @pytest.mark.asyncio
    async def test_add_remove_reconnect_on_url_change(self):
        from codeassist.mcp_client import MCPClient

        client = MCPClient()
        connected = []

        async def fake_connect(name, config):
            connected.append(name)
            client._clients[name] = _FakeHTTPX()
            client._urls[name] = config.get("url")
            client._tools[f"mcp_{name}_t"] = object()

        client._connect_server = fake_connect
        await client.initialize({"a": {"url": "http://a"}})
        assert connected == ["a"]

        # Adding b reconnects only b; a is left running.
        connected.clear()
        await client.reload({"a": {"url": "http://a"}, "b": {"url": "http://b"}})
        assert connected == ["b"]

        # Removing a disconnects it and prunes its tools.
        connected.clear()
        await client.reload({"b": {"url": "http://b"}})
        assert connected == []
        assert "a" not in client._urls
        assert "mcp_a_t" not in client._tools

        # Changing b's URL reconnects it with the new url.
        connected.clear()
        await client.reload({"b": {"url": "http://b2"}})
        assert connected == ["b"]
        assert client._urls["b"] == "http://b2"


class TestLSPClientReload:
    @pytest.mark.asyncio
    async def test_starts_stops_and_restarts_on_spec_change(self):
        from pathlib import Path

        from codeassist.lsp_client import LSPClient

        client = LSPClient()
        started, stopped = [], []

        async def fake_start(name, command, args, languages, workspace):
            started.append(name)
            client._servers[name] = object()
            client._specs[name] = {
                "command": command,
                "args": list(args),
                "languages": list(languages),
            }

        async def fake_stop(name):
            stopped.append(name)
            client._servers.pop(name, None)
            client._specs.pop(name, None)

        client.start_server = fake_start
        client._stop_server = fake_stop
        ws = Path("/tmp")

        await client.reload({"a": {"command": "ca", "args": [], "languages": ["py"]}}, ws)
        assert started == ["a"] and stopped == []

        # b is new (start), a's spec changed (restart).
        started.clear()
        await client.reload(
            {
                "a": {"command": "ca2", "args": [], "languages": ["py"]},
                "b": {"command": "cb", "args": [], "languages": ["js"]},
            },
            ws,
        )
        assert sorted(started) == ["a", "b"]

        # b unchanged (no restart); a removed (stopped).
        started.clear()
        await client.reload({"b": {"command": "cb", "args": [], "languages": ["js"]}}, ws)
        assert started == [] and stopped == ["a"]


class TestReloadWiring:
    @pytest.mark.asyncio
    async def test_mcp_create_triggers_reload(self, live_client, monkeypatch):
        calls = []

        async def spy():
            calls.append(1)
            return []

        monkeypatch.setattr(server, "reload_mcp_servers", spy)
        server.get_config().mcp.enabled = True

        r = live_client.post(
            "/api/mcp/servers", json={"name": "x", "config": {"url": "http://x"}}
        )
        assert r.status_code == 200, r.text
        await _flush_reloads()
        assert calls

    @pytest.mark.asyncio
    async def test_mcp_update_triggers_reload(self, live_client, monkeypatch):
        calls = []

        async def spy():
            calls.append(1)
            return []

        monkeypatch.setattr(server, "reload_mcp_servers", spy)
        cfg = server.get_config()
        cfg.mcp.enabled = True

        created = live_client.post(
            "/api/mcp/servers", json={"name": "x", "config": {"url": "http://x"}}
        )
        sid = created.json()["id"]
        r = live_client.put(f"/api/mcp/servers/{sid}", json={"enabled": False})
        assert r.status_code == 200, r.text
        await _flush_reloads()
        assert calls

    @pytest.mark.asyncio
    async def test_mcp_delete_triggers_reload(self, live_client, monkeypatch):
        calls = []

        async def spy():
            calls.append(1)
            return []

        monkeypatch.setattr(server, "reload_mcp_servers", spy)
        cfg = server.get_config()
        cfg.mcp.enabled = True

        created = live_client.post(
            "/api/mcp/servers", json={"name": "x", "config": {"url": "http://x"}}
        )
        sid = created.json()["id"]
        r = live_client.delete(f"/api/mcp/servers/{sid}")
        assert r.status_code == 200, r.text
        await _flush_reloads()
        assert calls

    @pytest.mark.asyncio
    async def test_lsp_create_triggers_reload(self, live_client, monkeypatch):
        calls = []

        async def spy():
            calls.append(1)

        monkeypatch.setattr(server, "reload_lsp_servers", spy)

        r = live_client.post(
            "/api/lsp/servers",
            json={"name": "py", "command": "pyright-langserver", "args": ["--stdio"]},
        )
        assert r.status_code == 200, r.text
        await _flush_reloads()
        assert calls

    @pytest.mark.asyncio
    async def test_lsp_delete_triggers_reload(self, live_client, monkeypatch):
        calls = []

        async def spy():
            calls.append(1)

        monkeypatch.setattr(server, "reload_lsp_servers", spy)

        created = live_client.post(
            "/api/lsp/servers",
            json={"name": "py", "command": "pyright-langserver", "args": ["--stdio"]},
        )
        sid = created.json()["id"]
        r = live_client.delete(f"/api/lsp/servers/{sid}")
        assert r.status_code == 200, r.text
        await _flush_reloads()
        assert calls


class TestReloadEndpoints:
    """Explicit manual reload endpoints triggered by the Admin 'Reload connections'
    button. These are awaited (not fire-and-forget) so the UI can report results."""

    @pytest.mark.asyncio
    async def test_mcp_reload_endpoint(self, live_client, monkeypatch):
        connected = ["svc-a", "svc-b"]

        async def spy():
            return connected

        monkeypatch.setattr(server, "reload_mcp_servers", spy)
        server.get_config().mcp.enabled = True

        r = live_client.post("/api/mcp/reload")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["reconnected"] == connected

    @pytest.mark.asyncio
    async def test_lsp_reload_endpoint(self, live_client, monkeypatch):
        called = []

        async def spy():
            called.append(1)

        monkeypatch.setattr(server, "reload_lsp_servers", spy)

        r = live_client.post("/api/lsp/reload")
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert called
