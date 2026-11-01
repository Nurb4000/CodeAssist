"""Regression tests for the shell tool's timeout cleanup.

A timed-out command must not leave backgrounded grandchildren running: killing
only the direct ``/bin/sh`` child orphans any process it forked, which leaks a
runaway process for the full lifetime of the original command.
"""

import os
import signal
import time

import pytest

from codeassist.tools.shell import ShellTool


def _pid_alive(pid: int) -> bool:
    """True if ``pid`` exists and is responsive to signal 0."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not owned by us
    return True


def _wait_gone(pid: int, timeout: float = 3.0) -> bool:
    """Poll until ``pid`` disappears (reaped), or give up after ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


def _tool(tmp_path) -> ShellTool:
    tool = ShellTool()
    tool.workspace = tmp_path
    return tool


@pytest.mark.asyncio
async def test_timed_out_shell_does_not_orphan_a_grandchild(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    # Fork a grandchild (backgrounded under /bin/sh), record its PID, then block
    # on `wait` so the command outlives the timeout and hits the kill path.
    command = f"sleep 30 & echo $! > {pidfile}; wait"

    tool = _tool(tmp_path)
    result = await tool.execute(command=command, timeout=0.3)

    assert result.error, "a timed-out command should report an error"
    assert pidfile.exists(), "grandchild PID was never recorded"

    gc_pid = int(pidfile.read_text(encoding="utf-8").strip())
    try:
        reaped = _wait_gone(gc_pid, timeout=3.0)
        assert reaped, (
            f"grandchild {gc_pid} survived the shell timeout — it was orphaned "
            "instead of being killed with its process group"
        )
    finally:
        # Best-effort cleanup so a failed assertion never leaves a lingering
        # process behind for the rest of the suite.
        if _pid_alive(gc_pid):
            try:
                os.kill(gc_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
async def test_completed_shell_still_returns_output(tmp_path):
    """Sanity check: a fast command is unaffected by the process-group change."""
    tool = _tool(tmp_path)
    result = await tool.execute(command="echo hello", timeout=5)

    assert not result.error
    assert "hello" in result.output


class _FakeStream:
    """Async pipe stand-in: yields fixed chunks, then EOF."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._i = 0

    async def read(self, n):
        if self._i >= len(self._chunks):
            return b""
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk


class TestMemoryBudget:
    @pytest.mark.asyncio
    async def test_under_budget_returns_all_data(self, tmp_path):
        tool = _tool(tmp_path)
        tool.MAX_OUTPUT_BUFFER_BYTES = 10_000_000
        data, truncated = await tool._read_budgeted(
            _FakeStream([b"x" * 1000, b"y" * 1000])
        )
        assert data == b"x" * 1000 + b"y" * 1000
        assert not truncated

    @pytest.mark.asyncio
    async def test_over_budget_truncates_and_keeps_near_ceiling(self, tmp_path):
        tool = _tool(tmp_path)
        tool.MAX_OUTPUT_BUFFER_BYTES = 10
        data, truncated = await tool._read_budgeted(_FakeStream([b"A" * 100]))
        assert truncated
        assert len(data) <= 10

    @pytest.mark.asyncio
    async def test_runaway_command_is_truncated_not_buffered_whole(self, tmp_path):
        # `yes` streams forever; with a tiny budget it must be cut off and the
        # process killed, not buffered until OOM.
        tool = _tool(tmp_path)
        tool.MAX_OUTPUT_BUFFER_BYTES = 4096
        result = await tool.execute(command="yes", timeout=10)
        assert result.error
        assert "truncated" in result.output.lower()

    @pytest.mark.asyncio
    async def test_normal_command_still_reports_exit_code(self, tmp_path):
        tool = _tool(tmp_path)
        tool.MAX_OUTPUT_BUFFER_BYTES = 4096
        result = await tool.execute(command="echo ok; exit 3", timeout=10)
        assert "Exit code: 3" in result.output
        assert result.error
