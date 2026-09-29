"""End-to-end smoke tests that boot the real FastAPI app and hit its REST surface.

Guards against the regression (commit 921381d) where the `codeassist/routes/`
package existed but was never registered, so every /api/* endpoint returned 404,
and where route handlers referenced pre-refactor module names (`from session
import ...`) that would 500 at request time.
"""


REST_ENDPOINTS_GET = [
    "/health",
    "/api/config",
    "/api/status",
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


def test_api_status_shape(live_client):
    r = live_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "codeassist" in body["db_path"] and body["db_path"].endswith(".db")
    assert isinstance(body["db_size_bytes"], int) and body["db_size_bytes"] >= 0
    assert body["db_size_human"]
    assert isinstance(body["restart_needed"], bool)


def test_api_status_restart_flag(live_client):
    # No overrides yet -> nothing needs a restart.
    assert live_client.get("/api/status").json()["restart_needed"] is False

    # A restart-required override flips the flag on…
    r = live_client.put("/api/settings", json={"server.port": 9101})
    assert r.status_code == 200
    assert live_client.get("/api/status").json()["restart_needed"] is True

    # …and back off once it's removed.
    r = live_client.delete("/api/settings/server.port")
    assert r.status_code == 200
    assert live_client.get("/api/status").json()["restart_needed"] is False


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


def test_kb_quality_pass_archives_low_confidence(live_client):
    """G1: the on-demand quality pass archives low-confidence entries and reports it."""
    created = live_client.post(
        "/api/kb/entries",
        json={"entry_type": "pattern", "scope": "project", "content": "junk", "confidence": 0.2},
    )
    assert created.status_code == 200, created.text

    report = live_client.post("/api/kb/quality-pass")
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["scanned"] >= 1
    assert body["archived"] == 1
    assert len(body["candidates"]) == 1

    # The archived entry no longer appears in the default (active) listing.
    listed = live_client.get("/api/kb/entries").json()
    ids = {e["id"] for e in listed["entries"]}
    assert created.json()["entry_id"] not in ids


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
    assert next(iter(pinned)) == sid, "pinned session should sort to the top"

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

def test_ws_review_agent_receives_enforced_ruleset(live_client, monkeypatch):
    """The active agent's permission map must reach the running Agent.

    Regression: `websocket_endpoint` constructed `Agent(...)` without
    `agent_ruleset`, so every per-agent deny/allow list was dead config and the
    read-only review agent could write code.
    """
    import uuid

    import codeassist.agent as agent_mod
    import codeassist.llm as llm_mod

    async def _stub_stream(self, *args, **kwargs):
        raise ConnectionError("stubbed LLM for test")

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _stub_stream)

    captured: dict = {}
    real_agent_cls = agent_mod.Agent

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_agent_cls(*args, **kwargs)

    monkeypatch.setattr(agent_mod, "Agent", spy)

    sid = f"review-ruleset-{uuid.uuid4()}"
    # No reconnect: the switch must take effect within this same connection.
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        assert _drain_until(ws, "active_agent")["agent"]["id"] == "default"
        ws.send_json({"type": "switch_agent", "agent_name": "review"})
        _drain_until(ws, "agent_switched")

        ws.send_json({"type": "user_message", "content": "review this change"})
        _drain_until(ws, "error")  # the stubbed LLM raises

    assert "agent_ruleset" in captured, "server did not pass the agent's ruleset"
    ruleset = captured["agent_ruleset"]
    assert ruleset.check("edit") == "deny"
    assert ruleset.check("write") == "deny"
    assert ruleset.check("apply_patch") == "deny"
    assert ruleset.check("read") == "allow"


def test_ws_switch_agent_rebuilds_permissions(live_client, monkeypatch):
    """`switch_agent` must rebuild the agent, not just swap its prompt.

    Regression: the handler mutated `agent.system_prompt` in place, so the new
    agent's permission ruleset and step budget were never applied -- switching
    into the read-only review agent kept the previous agent's write access.
    """
    import uuid

    import codeassist.agent as agent_mod
    import codeassist.llm as llm_mod

    async def _stub_stream(self, *args, **kwargs):
        raise ConnectionError("stubbed LLM for test")

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _stub_stream)

    built: list = []
    real_agent_cls = agent_mod.Agent

    def spy(*args, **kwargs):
        built.append(kwargs.get("agent_ruleset"))
        return real_agent_cls(*args, **kwargs)

    monkeypatch.setattr(agent_mod, "Agent", spy)

    sid = f"switch-perms-{uuid.uuid4()}"
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        _drain_until(ws, "active_agent")
        assert built[-1].check("edit") == "confirm", "default agent confirms writes"

        ws.send_json({"type": "switch_agent", "agent_name": "review"})
        _drain_until(ws, "agent_switched")

    assert len(built) == 2, "switching must construct a new Agent"
    assert built[-1].check("edit") == "deny", "review must deny edits after a switch"
    assert built[-1].check("write") == "deny"
    assert built[-1].check("test_runner") == "deny"
    assert built[-1].check("read") == "allow"


def test_static_work_block_lifecycle_guards(live_client):
    """The work-block lifecycle must not collapse or drop content mid-turn.

    These are static assertions, not behavioural tests -- the project has no JS
    test runner. They pin the three regressions behind "the sections move
    around before they should", "actions blink and disappear" and "tool use is
    sometimes not logged":
      * a step that closes mid-run must stay expanded and visible;
      * a tool result must be matched across every step, not just the active one;
      * the Work section may only auto-collapse once the run ends.
    """
    js = live_client.get("/static/app.js").text
    css = live_client.get("/static/style.css").text

    # A closed step stays visible while the run is live (only collapses at endRun).
    assert "if (!runActive || done) activeStepEl.classList.remove('open');" in js
    # Results are routed to the step that owns the call, wherever it lives now.
    assert "function findStepForToolCall(id)" in js
    assert "const step = findStepForToolCall(id);" in js
    # Auto-collapse happens in endRun(), gated on the run being over.
    assert "runActive = false;\n    finalizeState();" in js
    assert "if (!workBlockUserToggled && workBlockEl)" in js
    # Working indicator: a pulsing stop button plus a cue by the mode selector.
    assert "stopBtn.classList.add('busy')" in js
    assert "document.body.classList.add('is-working')" in js
    assert "#stop-btn.busy" in css and "stop-pulse" in css
    assert "body.is-working .mode-dot" in css


def test_static_busy_state_has_single_owner(live_client):
    """The in-flight UI state must be reset in one place.

    It was copy-pasted across five exit paths, which is how the cancel button
    and the busy indicator could disagree with the real run state.
    """
    js = live_client.get("/static/app.js").text
    assert "function setBusy(busy)" in js
    # No exit path should hand-roll the send/stop/input dance any more.
    assert js.count("sendBtn.style.display = 'flex';") == 1, "setBusy() should own this"
    assert js.count("stopBtn.style.display = 'none';") == 1
    assert js.count("inputEl.disabled = false;") == 0, "setBusy() should own this"
    assert "setAttachmentUiBusy(busy);" in js


def test_ws_cancel_interrupts_a_running_tool(live_client, monkeypatch):
    """Stop must end a turn even while a tool is still running.

    The cancel handler used to set only the cooperative `cancel_event`, which the
    agent inspects between streamed chunks and between tool calls -- never inside
    `asyncio.gather` while a tool is executing. A long tool therefore kept the
    run alive long after Stop, which is the reported "it waits for the whole
    turn" behaviour. The handler now also cancels the agent task.
    """
    import asyncio
    import threading
    import time
    import uuid

    import codeassist.llm as llm_mod
    import codeassist.server as server_mod
    from codeassist.llm import Finish, ToolCall, Usage
    from codeassist.tools import ToolResult

    async def _stream(self, *args, **kwargs):
        yield ToolCall(id="c1", name="read", arguments={"file_path": "slow.py"})
        yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _stream)

    tool_running = threading.Event()

    async def _slow_execute(name, args):
        tool_running.set()
        await asyncio.sleep(30)  # far longer than the test will wait
        return ToolResult(output="never", error=False)

    monkeypatch.setattr(server_mod.tools, "execute", _slow_execute)

    sid = f"cancel-latency-{uuid.uuid4()}"
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "run something slow"})
        assert tool_running.wait(timeout=10), "the tool call never started"

        started = time.monotonic()
        ws.send_json({"type": "cancel"})

        seen = []
        for _ in range(50):
            data = ws.receive_json()
            seen.append(data.get("type"))
            if data.get("type") == "cancelled":
                break
        elapsed = time.monotonic() - started

    assert "cancelled" in seen, f"no 'cancelled' event, got {seen}"
    assert "tool_call" in seen, f"the run never reached the tool, got {seen}"
    assert elapsed < 10, f"Stop took {elapsed:.1f}s -- the tool was waited out"

    # The turn is still a valid transcript: the interrupted tool_call is answered.
    msgs = live_client.get(f"/api/sessions/{sid}/messages").json()
    assert any(m["role"] == "tool" and m.get("tool_call_id") == "c1" for m in msgs), (
        "an interrupted tool_call must still get a tool result, or the next "
        "request to the provider is rejected"
    )


def test_ws_cancel_while_waiting_for_first_token(live_client, monkeypatch):
    """Stop must not wait for the model to produce its first token.

    The per-chunk flag check cannot fire before the first chunk arrives, so a
    slow/thinking model left the run hanging with no output to show for it.
    """
    import asyncio
    import time
    import uuid

    import codeassist.llm as llm_mod
    from codeassist.llm import Finish, TextDelta, Usage

    async def _slow_stream(self, *args, **kwargs):
        await asyncio.sleep(30)  # model never gets going
        yield TextDelta("far too late")
        yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _slow_stream)

    sid = f"cancel-first-token-{uuid.uuid4()}"
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "think slowly"})
        # Give the run a moment to reach the stream before cancelling.
        time.sleep(0.3)

        started = time.monotonic()
        ws.send_json({"type": "cancel"})

        seen = []
        for _ in range(50):
            data = ws.receive_json()
            seen.append(data.get("type"))
            if data.get("type") == "cancelled":
                break
        elapsed = time.monotonic() - started

    assert "cancelled" in seen, f"no 'cancelled' event, got {seen}"
    assert elapsed < 10, f"Stop took {elapsed:.1f}s -- it waited on the stream"


def test_plan_tasks_are_persisted_and_scoped_per_session(live_client, monkeypatch):
    """A session's plan belongs to that session and outlives the process.

    The plan used to be a list on one process-wide tool instance: a restart
    emptied it, and every session shared -- and cleared -- the same one, so
    switching conversations destroyed the work in progress.
    """
    import asyncio

    from codeassist import server as server_mod
    from codeassist.tools.todo import TodoTool

    a = live_client.post("/api/sessions", json={}).json()["id"]
    b = live_client.post("/api/sessions", json={}).json()["id"]

    async def _seed(session_id, items):
        tool = server_mod.tools.get("todo")
        tool.bind(session_id)
        await tool.load_session(session_id)
        for content in items:
            await tool.execute("add", content=content)

    asyncio.run(_seed(a, ["A one", "A two"]))
    asyncio.run(_seed(b, ["B one"]))

    assert [t["content"] for t in live_client.get(f"/api/todos?session_id={a}").json()["tasks"]] == [
        "A one", "A two",
    ]
    assert [t["content"] for t in live_client.get(f"/api/todos?session_id={b}").json()["tasks"]] == [
        "B one",
    ]

    # Simulate a restart: the tool instance is rebuilt with no memory, and the
    # only copy of each plan is on disk.
    server_mod.tools.register(TodoTool())
    assert [t["content"] for t in live_client.get(f"/api/todos?session_id={a}").json()["tasks"]] == [
        "A one", "A two",
    ], "the plan must come back after a restart"

    # Clearing one session must not touch the other.
    live_client.post(f"/api/todos/clear?session_id={a}")
    assert live_client.get(f"/api/todos?session_id={a}").json()["tasks"] == []
    assert [t["content"] for t in live_client.get(f"/api/todos?session_id={b}").json()["tasks"]] == [
        "B one",
    ], "clearing one session must not empty another"


def test_deleting_a_session_removes_its_plan(live_client):
    """No orphaned plan rows: the pool does not enforce FK cascades, so the
    delete has to remove them explicitly."""
    import asyncio

    from codeassist import server as server_mod
    from codeassist.session import get_db

    sid = live_client.post("/api/sessions", json={}).json()["id"]

    async def _seed_and_count():
        tool = server_mod.tools.get("todo")
        tool.bind(sid)
        await tool.load_session(sid)
        await tool.execute("add", content="Doomed task")
        async with get_db() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM plan_tasks WHERE session_id = ?", (sid,)
            )
            return (await cur.fetchone())[0]

    assert asyncio.run(_seed_and_count()) == 1

    live_client.delete(f"/api/sessions/{sid}")

    async def _remaining():
        async with get_db() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM plan_tasks WHERE session_id = ?", (sid,)
            )
            return (await cur.fetchone())[0]

    assert asyncio.run(_remaining()) == 0, "plan rows must not outlive their session"


def test_ws_turn_writes_the_plan_to_its_own_session(live_client, monkeypatch):
    """A turn's plan_update must describe the session that ran it.

    The todo tool is one instance for the whole process, so reading a bare
    get_tasks() would report whichever session happened to be bound last.
    """
    import asyncio
    import uuid

    import codeassist.llm as llm_mod
    from codeassist.llm import Finish, TextDelta, ToolCall, Usage

    sid = f"plan-ws-{uuid.uuid4()}"

    calls = {"n": 0}

    async def _stream(self, *args, **kwargs):
        # Plan on the first turn, then answer in prose so the run terminates
        # instead of looping on the same tool call.
        calls["n"] += 1
        if calls["n"] == 1:
            yield ToolCall(id="t1", name="todo", arguments={"action": "add", "content": "Plan item"})
        else:
            yield TextDelta("Plan recorded.")
        yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _stream)

    def _plan_update(events):
        for e in events:
            if e.get("type") == "plan_update":
                return e
        return None

    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "make me a plan"})
        events = []
        for _ in range(60):
            data = ws.receive_json()
            events.append(data)
            if data.get("type") == "done":
                break

    update = _plan_update(events)
    assert update is not None, f"no plan_update event, got {[e.get('type') for e in events]}"
    assert [t["content"] for t in update["tasks"]] == ["Plan item"]

    # And it is on disk against this session, not just in memory.
    async def _rows():
        from codeassist.session import get_db

        async with get_db() as db:
            cur = await db.execute(
                "SELECT content FROM plan_tasks WHERE session_id = ?", (sid,)
            )
            return [r[0] for r in await cur.fetchall()]

    assert asyncio.run(_rows()) == ["Plan item"]
