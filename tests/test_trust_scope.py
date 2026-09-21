"""Session-scoped trust flags (review item A2).

"Trust for this session" must survive WS reconnects: each connection builds a
fresh Agent, but the flags are seeded from a session-keyed store, so reconnecting
to the same session keeps the trust while other sessions stay isolated and
nothing survives a server restart.
"""
from pathlib import Path
from types import SimpleNamespace

import asyncio

import pytest

from codeassist.agent import Agent, SESSION_TRUST, SESSION_TOOL_TRUST
from codeassist.config import Config
from codeassist.llm import Finish, LLMClient, TextDelta, ToolCall, Usage
from codeassist.session import Session


@pytest.fixture(autouse=True)
def _clear_session_trust():
    SESSION_TRUST.clear()
    SESSION_TOOL_TRUST.clear()
    yield
    SESSION_TRUST.clear()
    SESSION_TOOL_TRUST.clear()


def _agent(session_id: str, workspace: Path) -> Agent:
    cfg = Config()
    cfg.workspace = workspace
    cfg.llm.api_key = "not-used-in-test"  # AsyncOpenAI() rejects a fully-empty api_key
    return Agent(cfg, Session(session_id), {}, "system prompt")


def test_trust_defaults_off():
    agent = _agent("sess-A", Path("/tmp/ca-a2-a"))
    assert agent._trust_shell is False
    assert agent._trust_workspace_writes is False


@pytest.mark.asyncio
async def test_trust_persists_across_agents_same_session(tmp_path):
    a1 = _agent("sess-B", tmp_path)
    a1.set_trust(trust_shell=True, trust_workspace=True)
    assert SESSION_TRUST["sess-B"] == {"workspace": True, "shell": True}

    # Reconnect: a brand-new Agent for the same session id keeps the trust.
    a2 = _agent("sess-B", tmp_path)
    assert a2._trust_shell is True
    assert a2._trust_workspace_writes is True
    # Shell short-circuits before the permission manager, so this is deterministic.
    assert await a2.needs_confirmation("shell", {"shell_command": "ls"}) is False


def test_trust_isolated_per_session_id(tmp_path):
    _agent("sess-C", tmp_path).set_trust(trust_shell=True)
    assert SESSION_TRUST["sess-C"]["shell"] is True

    other = _agent("sess-D", tmp_path)
    assert other._trust_shell is False
    assert other._trust_workspace_writes is False


def test_reset_trust_clears_store(tmp_path):
    agent = _agent("sess-E", tmp_path)
    agent.set_trust(trust_shell=True)
    assert SESSION_TRUST["sess-E"]["shell"] is True

    agent.reset_trust()
    assert "sess-E" not in SESSION_TRUST


def test_shell_only_trust_keeps_writes_untrusted(tmp_path):
    _agent("sess-F", tmp_path).set_trust(trust_shell=True)
    reconnect = _agent("sess-F", tmp_path)
    assert reconnect._trust_shell is True
    assert reconnect._trust_workspace_writes is False


class _StubTools:
    def schemas(self):
        return []

    def get(self, name):
        return None

    async def execute(self, name, args):
        return SimpleNamespace(output="ok", error=None)


@pytest.mark.asyncio
async def test_per_tool_session_trust_via_resolve_confirm(tmp_path, monkeypatch):
    """resolve_confirm(trust_tool=True) must trust that tool for the session (A1)."""
    agent = _agent("sess-G", tmp_path)
    agent._confirm_tools["c1"] = "shell"
    assert "shell" not in SESSION_TOOL_TRUST.get("sess-G", set())

    agent.resolve_confirm("c1", True, trust_tool=True)

    assert "shell" in SESSION_TOOL_TRUST["sess-G"]
    assert await agent.needs_confirmation("shell", {}) is False
    # Other tools are not session-trusted by this choice.
    assert "bash" not in SESSION_TOOL_TRUST["sess-G"]


def test_per_tool_trust_isolated_between_sessions(tmp_path):
    agent = _agent("sess-H", tmp_path)
    agent._confirm_tools["c1"] = "shell"
    agent.resolve_confirm("c1", True, trust_tool=True)

    assert SESSION_TOOL_TRUST.get("sess-I") is None
    assert SESSION_TOOL_TRUST.get("sess-H") == {"shell"}


@pytest.mark.asyncio
async def test_ws_confirm_flow_grants_per_tool_session_trust(tmp_path, monkeypatch):
    """Drive the exact agent path the WS confirm_response uses: stream yields a
    tool call, we approve with trust_tool=True, and the tool runs + stays trusted.

    Mirrors the real server: one task pumps the agent's event stream while a
    separate task resolves the confirm_request (which run() registers only after
    yielding it), then once approved the tool executes and the loop continues.
    """
    from unittest.mock import AsyncMock, MagicMock

    import codeassist.llm as llm_mod
    from codeassist.agent import SESSION_TOOL_TRUST

    state = {"stream_calls": 0}

    async def _stub_stream(self, messages, tools=None):
        state["stream_calls"] += 1
        if state["stream_calls"] == 1:
            yield ToolCall(id="call_1", name="shell", arguments={"shell_command": "echo hi"})
            yield Finish(finish_reason="tool_calls", usage=Usage(prompt_tokens=1, completion_tokens=1))
        else:
            yield TextDelta("done")
            yield Finish(finish_reason="stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _stub_stream)

    cfg = Config()
    cfg.workspace = tmp_path
    cfg.llm.api_key = "not-used-in-test"
    session = MagicMock(spec=Session)
    session.id = "sess-J"
    session.add_message = AsyncMock(return_value="mid-1")
    session.get_messages = AsyncMock(return_value=[])
    session.update_message = AsyncMock()
    agent = Agent(cfg, session, _StubTools(), "system prompt")

    seen = asyncio.Event()
    confirm_ids: dict[str, dict] = {}
    confirms: list[str] = []

    async def _pump():
        async for event in agent.run("run a command"):
            if event.type == "confirm_request":
                confirm_ids[event.data["id"]] = event.data
                confirms.append(event.data["tool"])
                seen.set()
            elif event.type in ("done", "error", "cancelled"):
                return

    async def _approver():
        await seen.wait()
        cid = next(iter(confirm_ids))
        agent.resolve_confirm(cid, True, trust_tool=True)

    await asyncio.wait_for(asyncio.gather(_pump(), _approver()), timeout=15)

    assert confirms == ["shell"]
    assert state["stream_calls"] == 2
    assert "shell" in SESSION_TOOL_TRUST["sess-J"]
    assert await agent.needs_confirmation("shell", {"shell_command": "echo hi"}) is False


@pytest.mark.asyncio
async def test_ws_confirm_flow_remember_saves_permanent_allow(tmp_path, monkeypatch):
    """Approving with remember=True must persist a permanent allow derived from
    server-bound confirm context (tool + file_path), so the next identical call
    runs without prompting again."""
    from unittest.mock import AsyncMock, MagicMock

    import codeassist.llm as llm_mod

    state = {"stream_calls": 0}

    async def _stub_stream(self, messages, tools=None):
        state["stream_calls"] += 1
        if state["stream_calls"] == 1:
            yield ToolCall(id="call_1", name="write", arguments={"file_path": str(tmp_path / "remembered_target.py"), "content": "x = 1"})
            yield Finish(finish_reason="tool_calls", usage=Usage(prompt_tokens=1, completion_tokens=1))
        else:
            yield TextDelta("done")
            yield Finish(finish_reason="stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _stub_stream)

    cfg = Config()
    cfg.workspace = tmp_path
    cfg.llm.api_key = "not-used-in-test"
    session = MagicMock(spec=Session)
    session.id = "sess-K"
    session.add_message = AsyncMock(return_value="mid-1")
    session.get_messages = AsyncMock(return_value=[])
    session.update_message = AsyncMock()
    agent = Agent(cfg, session, _StubTools(), "system prompt")

    seen = asyncio.Event()
    confirm_ids: dict[str, dict] = {}
    confirms: list[str] = []

    async def _pump():
        async for event in agent.run("edit the file"):
            if event.type == "confirm_request":
                confirm_ids[event.data["id"]] = event.data
                confirms.append(event.data["tool"])
                seen.set()
            elif event.type in ("done", "error", "cancelled"):
                return

    async def _approver():
        # Mirror the real WS confirm_response handler: bind to the stored
        # server-side context, persist the remembered allow, then resolve.
        await seen.wait()
        cid = next(iter(confirm_ids))
        ctx = agent.get_confirm_context(cid)
        assert ctx["tool"] == "write"
        assert ctx["file_path"] == str(tmp_path / "remembered_target.py")
        await agent.save_permission(ctx["tool"], ctx["file_path"], "allow")
        agent.resolve_confirm(cid, True, remember=True)

    await asyncio.wait_for(asyncio.gather(_pump(), _approver()), timeout=15)

    assert confirms == ["write"]
    assert state["stream_calls"] == 2
    assert await agent.needs_confirmation("write", {"file_path": str(tmp_path / "remembered_target.py"), "content": "x = 1"}) is False
    # The saved permission is permanent (persisted), not just session-scoped.
    assert agent.get_confirm_context("any") is None