"""Tests for persistent PTY tool."""

import json

import pytest

from codeassist.tools.pty import PTYTool


@pytest.mark.asyncio
async def test_pty_create_read_write_kill(tmp_path):
    tool = PTYTool()
    tool.workspace = tmp_path

    created = await tool.execute(action="create", command="bash -lc 'read x; echo got:$x'", cwd=str(tmp_path))
    assert not created.error, created.output
    pty_id = [line.split()[2] for line in created.output.splitlines() if line.startswith("PTY created:")][0]

    wrote = await tool.execute(action="write", pty_id=pty_id, input="hello")
    assert not wrote.error, wrote.output

    read = await tool.execute(action="read", pty_id=pty_id, timeout_ms=500)
    assert not read.error, read.output
    assert "got:hello" in read.output

    killed = await tool.execute(action="kill", pty_id=pty_id)
    assert not killed.error, killed.output


@pytest.mark.asyncio
async def test_pty_list_and_errors(tmp_path):
    tool = PTYTool()
    tool.workspace = tmp_path
    listed = await tool.execute(action="list")
    assert "No PTY sessions" in listed.output

    missing = await tool.execute(action="read", pty_id="pty_missing")
    assert missing.error


@pytest.mark.asyncio
async def test_pty_event_sink_receives_output(tmp_path):
    tool = PTYTool()
    tool.workspace = tmp_path
    events = []

    async def sink(payload):
        events.append(payload)

    tool.set_event_sink(sink, session_id="sess1")
    tool.bind_session("sess1")
    created = await tool.execute(action="create", command="bash -lc 'echo hi; sleep 0.1'", cwd=str(tmp_path))
    assert not created.error, created.output
    # allow reader loop to emit
    for _ in range(20):
        if any(e.get("event") in {"output", "exit"} for e in events):
            break
        await __import__("asyncio").sleep(0.05)
    assert any(e["type"] == "pty_output" for e in events)
    assert any(e.get("session_id") == "sess1" for e in events)


@pytest.mark.asyncio
async def test_pty_persisted_state_marks_lost_on_restart(tmp_path):
    state = tmp_path / "pty_sessions.json"
    state.write_text(json.dumps({"sessions": [{"id": "pty_old", "command": "bash", "cwd": str(tmp_path), "alive": True, "returncode": None}]}))
    tool = PTYTool()
    tool.workspace = tmp_path
    tool.set_state_path(state)
    tool.load_persisted_state()
    listed = await tool.execute(action="list")
    assert "pty_old" in listed.output
    assert "lost on restart" in listed.output
    data = json.loads(state.read_text())
    assert data["sessions"][0]["alive"] is False
    assert data["sessions"][0]["lost_on_restart"] is True
