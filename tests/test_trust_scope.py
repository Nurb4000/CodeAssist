"""Session-scoped trust flags (review item A2).

"Trust for this session" must survive WS reconnects: each connection builds a
fresh Agent, but the flags are seeded from a session-keyed store, so reconnecting
to the same session keeps the trust while other sessions stay isolated and
nothing survives a server restart.
"""
from pathlib import Path

import pytest

from codeassist.agent import Agent, SESSION_TRUST
from codeassist.config import Config
from codeassist.session import Session


@pytest.fixture(autouse=True)
def _clear_session_trust():
    SESSION_TRUST.clear()
    yield
    SESSION_TRUST.clear()


def _agent(session_id: str, workspace: Path) -> Agent:
    cfg = Config()
    cfg.workspace = workspace
    cfg.llm.api_key = "not-used-in-test"  # AsyncOpenAI() rejects a fully-empty api_key
    return Agent(cfg, Session(session_id), {}, "system prompt")


def test_trust_defaults_off():
    agent = _agent("sess-A", Path("/tmp/ca-a2-a"))
    assert agent._trust_shell is False
    assert agent._trust_workspace_writes is False


def test_trust_persists_across_agents_same_session(tmp_path):
    a1 = _agent("sess-B", tmp_path)
    a1.set_trust(trust_shell=True, trust_workspace=True)
    assert SESSION_TRUST["sess-B"] == {"workspace": True, "shell": True}

    # Reconnect: a brand-new Agent for the same session id keeps the trust.
    a2 = _agent("sess-B", tmp_path)
    assert a2._trust_shell is True
    assert a2._trust_workspace_writes is True
    # Shell short-circuits before the permission manager, so this is deterministic.
    assert a2.needs_confirmation("shell", {"shell_command": "ls"}) is False


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