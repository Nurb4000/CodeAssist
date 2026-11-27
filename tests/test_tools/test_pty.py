"""Tests for persistent PTY tool."""

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
