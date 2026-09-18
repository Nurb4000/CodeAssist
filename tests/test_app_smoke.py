"""End-to-end smoke tests that boot the real FastAPI app and hit its REST surface.

Guards against the regression (commit 921381d) where the `codeassist/routes/`
package existed but was never registered, so every /api/* endpoint returned 404,
and where route handlers referenced pre-refactor module names (`from session
import ...`) that would 500 at request time.
"""
import pytest
from fastapi.testclient import TestClient

import codeassist.server as server

REST_ENDPOINTS_GET = [
    "/health",
    "/api/config",
    "/api/todos",
    "/api/sessions",
    "/api/sessions/search/tags?tags=wip",
    "/api/kb/stats",
    "/api/kb/entries",
    "/api/kb/sessions",
    "/api/kb/search?q=smoke",
    "/api/kb/analytics/tools",
    "/api/kb/analytics/llm",
    "/api/tools/manage/list",
    "/api/tools/manage/usage",
    "/api/tools/trust/pending",
    "/api/tools/trust/trusted",
    "/api/tools/analytics/tools",
    "/api/tools/analytics/llm",
    "/api/skills",
    "/api/skills/list",
    "/api/agents",
    "/api/plugins",
    "/api/lsp/servers",
    "/api/mcp/servers",
    "/api/git/repos",
    "/api/custom-tools",
    "/api/knowledge",
]


def test_rest_api_is_registered(live_client):
    """The whole REST surface must be mounted on the app (not 404/500)."""
    for path in REST_ENDPOINTS_GET:
        r = live_client.get(path)
        assert r.status_code < 500, f"GET {path} -> {r.status_code}"


def test_health(live_client):
    r = live_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_static_admin_page_served(live_client):
    """Registry admin page (review item I) must be served with its JS."""
    assert live_client.get("/static/admin.html").status_code == 200
    assert live_client.get("/static/admin.js").status_code == 200
    assert live_client.get("/static/admin.html").text.count("id=\"admin-status\"") == 1


def test_session_lifecycle(live_client):
    r = live_client.post("/api/sessions")
    assert r.status_code == 200
    sid = r.json()["id"]

    assert live_client.get(f"/api/sessions/{sid}/messages").status_code == 200
    assert live_client.post(f"/api/sessions/{sid}/fork", json={}).status_code == 200
    assert live_client.patch(f"/api/sessions/{sid}", json={"name": "smoke"}).status_code == 200
    assert live_client.post(f"/api/sessions/{sid}/tags", json={"tag": "wip"}).status_code == 200
    assert live_client.get(f"/api/sessions/{sid}/tags").status_code == 200
    assert live_client.post(f"/api/sessions/{sid}/undo").status_code == 200

    export = live_client.post("/api/sessions/export", json={"session_id": sid, "redact": True})
    assert export.status_code == 200
    assert export.json()["version"] == 2

    # Import round-trip: importing the exported bundle creates a new session
    # with the same message count as the original.
    original_count = len(live_client.get(f"/api/sessions/{sid}/messages").json())
    imported = live_client.post("/api/sessions/import", json={"data": export.json()})
    assert imported.status_code == 200
    imported_sid = imported.json()["id"]
    assert imported_sid != sid
    msgs = live_client.get(f"/api/sessions/{imported_sid}/messages").json()
    assert len(msgs) == original_count


def test_tools_management_specific_routes_not_shadowed(live_client):
    """/api/tools/manage/usage and /manage/scan must not be swallowed by /manage/{tool_name}."""
    assert live_client.get("/api/tools/manage/usage").status_code == 200
    assert live_client.post("/api/tools/manage/scan").status_code == 200
    assert live_client.post("/api/tools/reload").status_code == 200


def test_analytics_parity_between_tools_and_kb_prefixes(live_client):
    """/api/tools/analytics/* must be aliases of /api/kb/analytics/*
    (single source of truth, per review item H1)."""
    kb_tools = live_client.get("/api/kb/analytics/tools")
    tools_tools = live_client.get("/api/tools/analytics/tools")
    kb_llm = live_client.get("/api/kb/analytics/llm")
    tools_llm = live_client.get("/api/tools/analytics/llm")
    for r in (kb_tools, tools_tools, kb_llm, tools_llm):
        assert r.status_code == 200
    assert tools_tools.json() == kb_tools.json()
    assert tools_llm.json() == kb_llm.json()

    filtered = live_client.get("/api/kb/analytics/tools?tool_name=shell")
    assert filtered.status_code == 200


def _orphan_message_count(db_path):
    """Count messages rows whose session_id has no matching sessions row."""
    import aiosqlite

    async def _count():
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM messages m "
                "LEFT JOIN sessions s ON m.session_id = s.id WHERE s.id IS NULL"
            )
            row = await cur.fetchone()
            return row[0]

    import asyncio
    return asyncio.new_event_loop().run_until_complete(_count())


def test_ws_unknown_session_id_creates_session_not_orphan(live_client, monkeypatch):
    """Connecting a WS to an unknown session id must create the sessions row
    (get_or_create), so its messages are never orphaned (review item B2)."""
    import uuid
    import codeassist.llm as llm_mod

    async def _stub_stream(self, *args, **kwargs):
        raise ConnectionError("stubbed LLM for test")

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _stub_stream)

    sid = f"stray-{uuid.uuid4()}"
    known = {s["id"] for s in live_client.get("/api/sessions").json()}
    assert sid not in known

    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "hello from stray client"})
        for _ in range(50):
            if ws.receive_json().get("type") == "error":
                break

    # Session row must now exist -> the stored message references a real session.
    sessions = {s["id"] for s in live_client.get("/api/sessions").json()}
    assert sid in sessions, "WS should have created the session row via get_or_create"

    msgs = live_client.get(f"/api/sessions/{sid}/messages").json()
    assert any(m["role"] == "user" and m["content"] == "hello from stray client" for m in msgs)

    # Direct proof across the whole table: zero orphan message rows.
    import codeassist.session as sess_mod
    assert _orphan_message_count(sess_mod.DB_PATH) == 0


def _drain_until(ws, msg_type, max_reads=50):
    """Read WS messages until one of the wanted type appears. Returns that dict."""
    for _ in range(max_reads):
        data = ws.receive_json()
        if data.get("type") == msg_type:
            return data
    raise AssertionError(f"never received a '{msg_type}' WS message")


def test_ws_agent_switcher(live_client, monkeypatch):
    """Agent switcher (review item I): the server announces the active agent on
    connect, applies a switch_agent request, and remembers the choice per session
    across reconnects."""
    import uuid
    import codeassist.llm as llm_mod

    async def _stub_stream(self, *args, **kwargs):
        raise ConnectionError("stubbed LLM for test")

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _stub_stream)

    agents = {a["id"] for a in live_client.get("/api/agents").json()}
    assert "research" in agents, "default AgentManager seeds a 'research' agent"

    sid = f"agent-switcher-{uuid.uuid4()}"
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        initial = _drain_until(ws, "active_agent")
        assert initial["agent"]["id"] == "default", initial

        ws.send_json({"type": "switch_agent", "agent_name": "research"})
        switched = _drain_until(ws, "agent_switched")
        assert switched["agent"]["id"] == "research", switched

    # Reconnect to the same session: the per-session choice must persist.
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        again = _drain_until(ws, "active_agent")
        assert again["agent"]["id"] == "research", again

    # Unknown agent -> error reply.
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        _drain_until(ws, "active_agent")
        ws.send_json({"type": "switch_agent", "agent_name": "does-not-exist"})
        err = _drain_until(ws, "error")
        assert "not found" in err["message"]


def test_session_pin_api(live_client):
    """PATCH /api/sessions/{id} with pinned toggles the flag; pinned sorts first."""
    sid = live_client.post("/api/sessions").json()["id"]
    assert live_client.get("/api/sessions").json()[0]["is_pinned"] == 0

    r = live_client.patch(f"/api/sessions/{sid}", json={"pinned": True})
    assert r.status_code == 200

    pinned = {s["id"]: s["is_pinned"] for s in live_client.get("/api/sessions").json()}
    assert pinned[sid] == 1
    assert list(pinned)[0] == sid, "pinned session should sort to the top"

    live_client.patch(f"/api/sessions/{sid}", json={"pinned": False})
    pinned = {s["id"]: s["is_pinned"] for s in live_client.get("/api/sessions").json()}
    assert pinned[sid] == 0


def test_session_patch_still_renames(live_client):
    """PATCH with a name field keeps working alongside the new pinned support."""
    sid = live_client.post("/api/sessions").json()["id"]
    live_client.patch(f"/api/sessions/{sid}", json={"name": "Renamed"})
    row = next(s for s in live_client.get("/api/sessions").json() if s["id"] == sid)
    assert row["name"] == "Renamed"


def test_agent_management_api(live_client):
    """POST/DELETE /api/agents work; built-in agents can't be deleted."""
    agents = {a["id"]: a for a in live_client.get("/api/agents").json()}
    assert "default" in agents and agents["default"]["builtin"] is True

    # Creating a custom agent surfaces it as non-builtin.
    r = live_client.post("/api/agents", json={
        "name": "qa-custom",
        "description": "QA reviewer",
        "model": "gpt-4o",
    })
    assert r.status_code == 200
    agents = {a["id"]: a for a in live_client.get("/api/agents").json()}
    assert agents["qa-custom"]["builtin"] is False

    # Built-ins are protected.
    r = live_client.delete("/api/agents/default")
    assert r.status_code == 400
    assert "built-in" in r.json()["detail"]

    # Custom agents delete cleanly.
    r = live_client.delete("/api/agents/qa-custom")
    assert r.status_code == 200
    assert "qa-custom" not in {a["id"] for a in live_client.get("/api/agents").json()}