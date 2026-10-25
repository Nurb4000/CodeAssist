"""Tests for the subagent task lifecycle (review item §1.15):

* cancellation actually stops the running loop and leaves the task "cancelled"
  (it used to flip a flag while the loop kept running, then overwrite the status
  with "completed");
* completion/error/cancellation are persisted to the database (results used to
  reach the DB only on creation, so background tasks vanished on restart);
* background notifications are delivered and their queue is drained instead of
  growing unbounded.
"""
import asyncio
from types import SimpleNamespace

import pytest

from codeassist.session import get_db
from codeassist.subagent import SubagentManager


def _agent_config():
    return SimpleNamespace(
        get_system_prompt=lambda: "system",
        permissions=SimpleNamespace(to_ruleset=lambda: None),
        steps=5,
    )


@pytest.fixture
def stub_deps(monkeypatch):
    """Patch the heavy imports ``_run_subagent`` makes so a task can run without a
    real Agent, session, or agent manager."""

    class FakeSession:
        @staticmethod
        async def create(*, name, parent_id, **kw):
            return SimpleNamespace(id=f"child-{parent_id[-4:]}")

        async def get_messages(self):
            return []

    class FakeAgentManager:
        @staticmethod
        def get_agent(name):
            return _agent_config()

    def fake_agent(*args, **kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("codeassist.session.Session", FakeSession)
    monkeypatch.setattr("codeassist.agents.agent_manager", FakeAgentManager())
    monkeypatch.setattr("codeassist.agent.Agent", fake_agent)


def _runner_with(text):
    async def runner(agent, prompt):
        yield SimpleNamespace(type="text_delta", data={"content": text})
    return runner


CFG = SimpleNamespace(agent=SimpleNamespace(subagent_depth=5))


@pytest.mark.asyncio
async def test_completion_persists_result_to_db(stub_deps):
    """A completed task's result must reach the database (was memory-only)."""
    from codeassist.session import init_db

    await init_db()
    mgr = SubagentManager()
    task = await mgr.create_task(
        description="d", prompt="p", subagent_type="general",
        parent_session_id="parent-1",
    )
    result = await mgr.start_task(task.id, _runner_with("result-text"), CFG, None)

    assert result == "result-text"

    async with get_db() as db:
        cur = await db.execute(
            "SELECT status, result FROM subagent_tasks WHERE id=?", (task.id,),
        )
        row = await cur.fetchone()

    assert row is not None, "completed task must be persisted"
    assert row[0] == "completed"
    assert row[1] == "result-text"


@pytest.mark.asyncio
async def test_error_persists_to_db(stub_deps):
    """A failing task records its error in the database."""
    from codeassist.session import init_db

    await init_db()
    mgr = SubagentManager()

    async def failing_runner(agent, prompt):
        raise RuntimeError("boom")
        yield  # pragma: no cover - makes this an async generator

    task = await mgr.create_task(
        description="bg", prompt="p", subagent_type="general",
        parent_session_id="parent-1",
    )
    with pytest.raises(RuntimeError):
        await mgr.start_task(task.id, failing_runner, CFG, None)

    async with get_db() as db:
        cur = await db.execute(
            "SELECT status FROM subagent_tasks WHERE id=?", (task.id,),
        )
        row = await cur.fetchone()

    assert row is not None, "errored task must be persisted"
    assert row[0] == "error"


@pytest.mark.asyncio
async def test_cancel_stops_the_running_loop(stub_deps):
    """cancel_task must actually cancel the tracked run; the loop has to observe
    the cancellation and the task must stay 'cancelled' (not be overwritten with
    'completed')."""
    from codeassist.session import init_db

    await init_db()
    mgr = SubagentManager()
    task = await mgr.create_task(
        description="d", prompt="p", subagent_type="general",
        parent_session_id="parent-1",
    )

    running = asyncio.Event()
    cancel_seen = asyncio.Event()

    async def blocking_runner(agent, prompt):
        running.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancel_seen.set()
            raise
        yield  # pragma: no cover - makes this an async generator

    run_task = asyncio.create_task(
        mgr.start_task(task.id, blocking_runner, CFG, None),
    )
    await asyncio.wait_for(running.wait(), timeout=5)

    assert await mgr.cancel_task(task.id) is True
    # The loop really was cancelled, not just flagged.
    await asyncio.wait_for(cancel_seen.wait(), timeout=5)
    # start_task resolves to a normal result rather than propagating the
    # cancellation into the foreground caller.
    result = await asyncio.wait_for(run_task, timeout=5)
    assert result == f"Subagent '{task.id}' was cancelled."
    assert task.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_unknown_task_is_noop(stub_deps):
    mgr = SubagentManager()
    assert await mgr.cancel_task("does-not-exist") is False


@pytest.mark.asyncio
async def test_background_completion_notifies_and_drains_queue(stub_deps):
    """A background task delivers its completion to the registered hook and its
    notification entry is drained (the old code enqueued but never consumed, so
    the queue grew unbounded)."""
    from codeassist.session import init_db

    await init_db()
    mgr = SubagentManager()
    delivered = []

    async def hook(task):
        delivered.append(task)

    mgr.on_background_complete = hook

    task = await mgr.create_task(
        description="bg", prompt="p", subagent_type="general",
        parent_session_id="parent-1", background=True,
    )
    await mgr.start_task(task.id, _runner_with("bg-result"), CFG, None)

    assert len(delivered) == 1
    assert delivered[0].id == task.id
    assert task.id not in mgr._notifications, "notification queue must be drained"


@pytest.mark.asyncio
async def test_subagent_nesting_is_bounded_by_depth(stub_deps):
    """A subagent that spawns another subagent must be bounded by
    subagent_depth. TaskTool used to pass a constant depth=0 to start_task, so
    the nesting guard (`depth >= subagent_depth`) never fired and unbounded
    nesting was prevented only by the prompt-text instruction. Now each level
    increments a per-execution depth, so with subagent_depth=1 the first-level
    subagent runs but its nested spawn is rejected by the guard."""
    from codeassist.session import init_db
    from codeassist.subagent import _subagent_depth

    await init_db()
    mgr = SubagentManager()
    cfg = SimpleNamespace(agent=SimpleNamespace(subagent_depth=1))

    async def nesting_runner(agent, prompt):
        # The first-level subagent runs one level below the main agent.
        assert _subagent_depth.get() == 1
        nested = await mgr.create_task(
            description="n", prompt="np", subagent_type="general",
            parent_session_id="parent-nest",
        )
        # Spawn the nested task at THIS level's depth, exactly as TaskTool does.
        result = await mgr.start_task(
            nested.id, _runner_with("nested-ok"), cfg, None,
            depth=_subagent_depth.get(),
        )
        yield SimpleNamespace(type="text_delta", data={"content": result})

    parent = await mgr.create_task(
        description="p", prompt="pp", subagent_type="general",
        parent_session_id="parent-1",
    )
    out = await mgr.start_task(parent.id, nesting_runner, cfg, None)

    assert "Max subagent depth" in out
    assert "nested-ok" not in out
