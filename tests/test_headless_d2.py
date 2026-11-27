"""Tests for Phase D2 headless run + ACP adapter."""

import argparse
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_headless_bootstrap_uses_server_subsystems(monkeypatch, tmp_path):
    from codeassist import server
    from codeassist.config import Config
    import codeassist.headless as headless

    cfg = Config()
    cfg.server.workspace = str(tmp_path)
    cfg.workspace = tmp_path

    calls = []

    async def rec(name):
        async def _f(*args, **kwargs):
            calls.append(name)
        return _f

    monkeypatch.setattr(server, "init_db", AsyncMock(side_effect=lambda: calls.append("init_db")))
    monkeypatch.setattr(server, "init_permissions", AsyncMock(side_effect=lambda: calls.append("init_permissions")))
    monkeypatch.setattr(server, "_init_subsystems", AsyncMock(side_effect=lambda c: calls.append("_init_subsystems")))
    monkeypatch.setattr(server, "init_agents", AsyncMock(side_effect=lambda: calls.append("init_agents")))
    monkeypatch.setattr(server, "init_mcp", AsyncMock(side_effect=lambda: calls.append("init_mcp")))
    monkeypatch.setattr(server, "init_skills", AsyncMock(side_effect=lambda: calls.append("init_skills")))
    monkeypatch.setattr(server, "init_plugins", AsyncMock(side_effect=lambda: calls.append("init_plugins")))

    import codeassist.settings as settings_mod
    monkeypatch.setattr(settings_mod, "apply_settings_overrides", AsyncMock(side_effect=lambda c: calls.append("apply_settings_overrides")))

    old = {k: getattr(server, k) for k in ("_config", "tools", "skill_registry", "plugin_registry", "lsp_client", "mcp_client")}
    try:
        rt = await headless.bootstrap(cfg)
        assert rt.config is cfg
        assert server.get_config() is cfg
        for name in [
            "init_db",
            "apply_settings_overrides",
            "init_permissions",
            "_init_subsystems",
            "init_agents",
            "init_mcp",
            "init_skills",
            "init_plugins",
        ]:
            assert name in calls
        # close should be safe with None globals
        await rt.close()
    finally:
        for k, v in old.items():
            setattr(server, k, v)


@pytest.mark.asyncio
async def test_headless_build_agent_uses_resolved_agent(monkeypatch, tmp_path):
    from codeassist import server
    from codeassist.config import Config
    import codeassist.headless as headless

    cfg = Config()
    cfg.server.workspace = str(tmp_path)
    cfg.workspace = tmp_path
    cfg.agent.default_agent = "default"

    # fake agent config
    agent_cfg = SimpleNamespace(
        name="CodeAssist",
        description="d",
        instructions="be helpful",
        model=None,
        permissions=SimpleNamespace(to_ruleset=lambda: "RULES"),
        steps=7,
        get_system_prompt=lambda: "AGENT HEADER",
    )

    class FakeAgentManager:
        def get_agent(self, name):
            return agent_cfg if name == "default" else None

        def get_default_agent(self):
            return agent_cfg

        def list_agents(self):
            return [{"id": "default"}]

    fake_tools = MagicMock()
    fake_tools.get.return_value = None

    old = {k: getattr(server, k) for k in ("_config", "tools", "skill_registry", "plugin_registry", "lsp_client", "mcp_client", "agent_manager")}
    try:
        server._config = cfg
        server.tools = fake_tools
        server.skill_registry = None
        server.plugin_registry = None
        server.lsp_client = None
        server.mcp_client = None
        server.agent_manager = FakeAgentManager()

        captured = {}

        class FakeAgent:
            def __init__(self, config, session, tools, system_prompt, agent_ruleset=None, max_steps=None):
                captured["config"] = config
                captured["session"] = session
                captured["tools"] = tools
                captured["system_prompt"] = system_prompt
                captured["agent_ruleset"] = agent_ruleset
                captured["max_steps"] = max_steps

        monkeypatch.setattr("codeassist.agent.Agent", FakeAgent)
        monkeypatch.setattr(server, "get_instruction_discoverer", lambda: SimpleNamespace(discover_all=lambda: []))

        session = SimpleNamespace(id="ses_test")
        agent, resolved_cfg, name = headless.build_agent(session, "default")
        assert isinstance(agent, FakeAgent)
        assert resolved_cfg is agent_cfg
        assert name == "default"
        assert captured["agent_ruleset"] == "RULES"
        assert captured["max_steps"] == 7
        assert "AGENT HEADER" in captured["system_prompt"]
    finally:
        for k, v in old.items():
            setattr(server, k, v)


@pytest.mark.asyncio
async def test_run_prompt_collects_text_and_defaults_noninteractive(monkeypatch):
    import codeassist.headless_run as hr
    from codeassist.agent import AgentEvent

    class FakeAgent:
        def __init__(self):
            self.resolved = []

        async def run(self, message, attachments=None):
            yield AgentEvent("text_delta", {"content": "hello"})
            yield AgentEvent("confirm_request", {"id": "c1", "tool": "shell"})
            yield AgentEvent("question_request", {"id": "q1", "question": "?"})
            yield AgentEvent("done")

        async def resolve_confirm(self, confirm_id, approved, **kwargs):
            self.resolved.append((confirm_id, approved, kwargs))

    fake_agent = FakeAgent()

    monkeypatch.setattr(hr, "bind_session_tools", lambda sid: None)
    monkeypatch.setattr(hr, "load_session_todos", AsyncMock())
    monkeypatch.setattr(hr, "build_agent", lambda session, agent_name=None: (fake_agent, None, "default"))
    monkeypatch.setattr(hr, "start_turn", lambda sid: None)
    monkeypatch.setattr(hr, "end_turn", lambda sid: None)
    monkeypatch.setattr(hr.server, "tools", None)

    agent_out = []
    result = await hr.run_prompt(session_id="ses1", message="hi", agent_out=agent_out)
    assert result.text == "hello"
    assert result.stop_reason == "end_turn"
    assert ("c1", False, {}) in fake_agent.resolved
    assert any(r[0] == "q1" and r[1] is False for r in fake_agent.resolved)
    assert agent_out == []


def test_cli_parses_run_and_acp():
    from codeassist.cli import main
    import sys

    old = sys.argv
    try:
        sys.argv = ["codeassist", "run", "--format", "final", "hello"]
        # parse only by invoking parser through SystemExit from run? easier: build parser via calling main pieces not exposed.
    finally:
        sys.argv = old

    # Build parser like main does by importing module internals
    import codeassist.cli as cli
    parser = argparse.ArgumentParser(prog="codeassist")
    cli._add_common_args(parser, include_browser=True)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run")
    cli._add_common_args(run)
    run.add_argument("message", nargs="*")
    run.add_argument("--session", default=None)
    run.add_argument("--continue", dest="continue_session", action="store_true")
    run.add_argument("--agent", default=None)
    run.add_argument("--format", choices=["text", "final", "json"], default="text")
    run.add_argument("--title", default=None)
    args = parser.parse_args(["run", "--format", "final", "--agent", "research", "hello", "world"])
    assert args.command == "run"
    assert args.format == "final"
    assert args.agent == "research"
    assert args.message == ["hello", "world"]


@pytest.mark.asyncio
async def test_acp_initialize_and_session_new(monkeypatch, tmp_path):
    import codeassist.acp_adapter as acp
    from codeassist.config import Config

    cfg = Config()
    cfg.server.workspace = str(tmp_path)
    cfg.workspace = tmp_path
    state = acp.ACPState(config=cfg)

    writes = []

    class FakeWriter:
        def write(self, data):
            writes.append(data)

        async def drain(self):
            return None

    state.writer = FakeWriter()

    async def fake_create(name=None, parent_id=None, fork_point=None):
        return SimpleNamespace(id="sess123")

    monkeypatch.setattr(acp.Session, "create", fake_create)
    monkeypatch.setattr(acp.Session, "list_all", AsyncMock(return_value=[{"id": "sess123", "name": "ACP"}]))

    await acp.handle_message(state, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}})
    await acp.handle_message(state, {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(tmp_path)}})
    await acp.handle_message(state, {"jsonrpc": "2.0", "id": 3, "method": "session/list", "params": {}})

    msgs = [json.loads(w.decode("utf-8")) for w in writes]
    assert msgs[0]["id"] == 1
    assert msgs[0]["result"]["protocolVersion"] == 1
    assert msgs[1]["id"] == 2
    assert "sessionId" in msgs[1]["result"]
    assert msgs[2]["id"] == 3
    assert msgs[2]["result"]["sessions"][0]["sessionId"].startswith("acp_")


@pytest.mark.asyncio
async def test_acp_prompt_streams_text_and_returns_stop_reason(monkeypatch, tmp_path):
    import codeassist.acp_adapter as acp
    from codeassist.config import Config
    from codeassist.agent import AgentEvent

    cfg = Config()
    state = acp.ACPState(config=cfg)
    state.sessions["acp_sess1"] = {"id": "acp_sess1", "sessionId": "sess1", "cwd": None}

    writes = []

    class FakeWriter:
        def write(self, data):
            writes.append(data)

        async def drain(self):
            return None

    state.writer = FakeWriter()

    async def fake_run_prompt(**kwargs):
        sink = kwargs["sink"]
        await sink(AgentEvent("text_delta", {"content": "hi"}))
        await sink(AgentEvent("done"))
        return SimpleNamespace(stop_reason="end_turn")

    monkeypatch.setattr(acp, "run_prompt", fake_run_prompt)

    await acp.handle_message(state, {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "session/prompt",
        "params": {"sessionId": "acp_sess1", "prompt": [{"type": "text", "text": "hello"}]},
    })
    # prompt spawns task; wait for it
    for _ in range(50):
        if any(json.loads(w.decode()).get("id") == 7 for w in writes):
            break
        await asyncio.sleep(0.01)

    msgs = [json.loads(w.decode("utf-8")) for w in writes]
    notif = [m for m in msgs if m.get("method") == "session/update"]
    resp = [m for m in msgs if m.get("id") == 7]
    assert notif
    assert notif[0]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert resp[0]["result"]["stopReason"] == "end_turn"
