"""Tests for the todo (task list / plan) tool."""
import pytest
import pytest_asyncio

from codeassist.tools.todo import TodoTool


class TestTodoTool:
    @pytest.fixture
    def todo(self):
        """Create a fresh TodoTool instance."""
        return TodoTool()

    @pytest.mark.asyncio
    async def test_add_and_list(self, todo):
        await todo.execute("add", content="Write tests")
        await todo.execute("add", content="Run them")
        tasks = todo.get_tasks()
        assert len(tasks) == 2
        assert tasks[0] == {"id": 1, "content": "Write tests", "status": "pending"}
        assert tasks[1]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_add_requires_content(self, todo):
        result = await todo.execute("add")
        assert result.error

    @pytest.mark.asyncio
    async def test_update_moves_status(self, todo):
        await todo.execute("add", content="Task A")
        result = await todo.execute("update", task_id=1, status="in_progress")
        assert result.error is False
        assert todo.get_tasks()[0]["status"] == "in_progress"
        result = await todo.execute("update", task_id=1, status="completed")
        assert result.error is False
        assert todo.get_tasks()[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_status_aliases_normalize(self, todo):
        # "done" / "complete" / "active" are very common model spellings that
        # previously created an unrenderable status, leaving the plan stuck.
        for alias, canonical in [
            ("done", "completed"),
            ("complete", "completed"),
            ("finished", "completed"),
            ("DONE", "completed"),
            ("active", "in_progress"),
            ("in progress", "in_progress"),
            ("started", "in_progress"),
            ("todo", "pending"),
        ]:
            await todo.clear_tasks()
            await todo.execute("add", content=f"Task for {alias}")
            result = await todo.execute("update", task_id=1, status=alias)
            assert result.error is False, f"alias {alias!r} should be accepted"
            assert todo.get_tasks()[0]["status"] == canonical, f"alias {alias!r} -> {canonical}"

    @pytest.mark.asyncio
    async def test_numeric_string_task_id_is_accepted(self, todo):
        # Models frequently serialize ids as strings; "1" == 1 is False and used
        # to silently drop the update.
        await todo.execute("add", content="Task A")
        result = await todo.execute("update", task_id="1", status="done")
        assert result.error is False
        assert todo.get_tasks()[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_unknown_status_rejected(self, todo):
        await todo.execute("add", content="Task A")
        result = await todo.execute("update", task_id=1, status="halfway")
        assert result.error
        assert todo.get_tasks()[0]["status"] == "pending"  # unchanged

    @pytest.mark.asyncio
    async def test_update_missing_task(self, todo):
        await todo.execute("add", content="Task A")
        result = await todo.execute("update", task_id=99, status="done")
        assert result.error
        assert "not found" in result.output

    @pytest.mark.asyncio
    async def test_clear(self, todo):
        await todo.execute("add", content="Task A")
        await todo.execute("clear")
        assert todo.get_tasks() == []

    @pytest.mark.asyncio
    async def test_list_output_markers(self, todo):
        await todo.execute("add", content="Task A")
        await todo.execute("update", task_id=1, status="in_progress")
        await todo.execute("add", content="Task B")
        await todo.execute("update", task_id=2, status="completed")
        result = await todo.execute("list")
        assert "[~] Task A" in result.output
        assert "[x] Task B" in result.output
        assert "[ ]" not in result.output

    def test_todo_tool_schema(self, todo):
        schema = todo.schema()
        assert schema["name"] == "todo"
        assert "action" in schema["parameters"]["properties"]
        assert schema["parameters"]["properties"]["action"]["enum"] == ["add", "update", "list", "clear"]

class TestTodoPersistence:
    """The plan list is the user's record of what the agent is doing.

    It used to be a plain list on one process-wide tool instance, so a restart
    emptied it and every session in the process shared -- and cleared -- the same
    one. It is now keyed by session and written through to `plan_tasks` on every
    mutation.
    """

    @pytest_asyncio.fixture
    async def db(self):
        # Call init_db directly rather than via the shared `initialized_db`
        # fixture: that one is a plain async def, which pytest-asyncio in strict
        # mode refuses to treat as a fixture at all.
        from codeassist.session import init_db

        await init_db()

    @staticmethod
    async def _rows(session_id):
        from codeassist.session import get_db

        async with get_db() as conn:
            cur = await conn.execute(
                "SELECT task_id, content, status FROM plan_tasks "
                "WHERE session_id = ? ORDER BY position, task_id",
                (session_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    @pytest.mark.asyncio
    async def test_added_tasks_are_written_to_the_database(self, db):
        todo = TodoTool()
        todo.bind("sess-a")
        await todo.execute("add", content="Write tests")
        await todo.execute("add", content="Run them")

        rows = await self._rows("sess-a")
        assert [r["content"] for r in rows] == ["Write tests", "Run them"]
        assert [r["task_id"] for r in rows] == [1, 2]

    @pytest.mark.asyncio
    async def test_a_status_change_is_persisted(self, db):
        # Not just adds: a tick-marked-off task is the whole point of the panel,
        # so the update has to reach disk as well.
        todo = TodoTool()
        todo.bind("sess-a")
        await todo.execute("add", content="Task A")
        await todo.execute("update", task_id=1, status="in_progress")
        await todo.execute("update", task_id=1, status="completed")

        rows = await self._rows("sess-a")
        assert rows[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_the_plan_survives_a_restart(self, db):
        # The regression: a fresh process starts with an empty in-memory list
        # and used to overwrite whatever was on disk.
        first = TodoTool()
        first.bind("sess-a")
        await first.execute("add", content="Survive me")
        await first.execute("add", content="And me")
        await first.execute("update", task_id=1, status="in_progress")

        # Simulate the restart: a brand-new instance with no memory of it.
        restarted = TodoTool()
        restarted.bind("sess-a")
        await restarted.load_session("sess-a")

        tasks = restarted.get_tasks("sess-a")
        assert [t["content"] for t in tasks] == ["Survive me", "And me"]
        assert tasks[0]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_ids_continue_after_a_reload_instead_of_colliding(self, db):
        # If next_id restarted at 1 the model would be handed an id that already
        # belongs to a different task, and the next update would hit the wrong one.
        first = TodoTool()
        first.bind("sess-a")
        await first.execute("add", content="One")
        await first.execute("add", content="Two")

        restarted = TodoTool()
        restarted.bind("sess-a")
        await restarted.load_session("sess-a")
        result = await restarted.execute("add", content="Three")

        assert "#3" in result.output
        assert [r["content"] for r in await self._rows("sess-a")] == ["One", "Two", "Three"]

    @pytest.mark.asyncio
    async def test_sessions_do_not_share_a_plan(self, db):
        # Two browser tabs, two conversations: one must never see or clear the
        # other's plan.
        todo = TodoTool()
        todo.bind("sess-a")
        await todo.execute("add", content="A's work")

        todo.bind("sess-b")
        await todo.load_session("sess-b")
        assert todo.get_tasks("sess-b") == [], "B must not inherit A's plan"

        await todo.execute("add", content="B's work")
        todo.bind("sess-a")
        assert [t["content"] for t in todo.get_tasks("sess-a")] == ["A's work"], (
            "B's task must not leak into A"
        )

    @pytest.mark.asyncio
    async def test_clearing_one_session_leaves_the_others_alone(self, db):
        todo = TodoTool()
        todo.bind("sess-a")
        await todo.execute("add", content="A's work")
        todo.bind("sess-b")
        await todo.execute("add", content="B's work")

        await todo.clear_tasks("sess-a")

        assert todo.get_tasks("sess-a") == []
        assert [t["content"] for t in todo.get_tasks("sess-b")] == ["B's work"]
        assert len(await self._rows("sess-b")) == 1, "B's rows survive on disk"

    @pytest.mark.asyncio
    async def test_load_session_is_idempotent(self, db):
        # It runs on every connect and every turn, so a second call must not
        # clobber tasks added in the meantime.
        todo = TodoTool()
        todo.bind("sess-a")
        await todo.load_session("sess-a")
        await todo.execute("add", content="Added after load")
        await todo.load_session("sess-a")

        assert [t["content"] for t in todo.get_tasks("sess-a")] == ["Added after load"]

    @pytest.mark.asyncio
    async def test_clear_empties_the_database_too(self, db):
        todo = TodoTool()
        todo.bind("sess-a")
        await todo.execute("add", content="Temporary")
        await todo.execute("clear")

        assert todo.get_tasks("sess-a") == []
        assert await self._rows("sess-a") == [], "cleared rows must not linger on disk"

    @pytest.mark.asyncio
    async def test_no_session_means_no_persistence_and_no_error(self, db):
        # A bare tool (unit tests, or a call before any socket bound a session)
        # keeps working in memory rather than failing on a NOT NULL session_id.
        todo = TodoTool()
        await todo.execute("add", content="In-memory only")
        assert [t["content"] for t in todo.get_tasks()] == ["In-memory only"]


class TestTodoConcurrentSessions:
    """Two sessions streaming at once must not share a plan.

    There is one todo tool for the whole process, so "which session is this" has
    to come from the running turn's own context. With a plain attribute, the
    second turn to start would redirect the first turn's writes into its own
    plan -- a task landing in the wrong conversation.
    """

    @pytest.mark.asyncio
    async def test_interleaved_turns_do_not_cross_contaminate(self):
        todo = TodoTool()

        # Simulate the interleaving: session A's turn begins, then B's, and A's
        # todo call lands after B has bound. With a shared attribute the `_key()`
        # resolution below would return B.
        with todo.active("sess-a"):
            # B's turn starts and finishes entirely inside A's.
            with todo.active("sess-b"):
                await todo.execute("add", content="B's task")
            # A's turn resumes.
            await todo.execute("add", content="A's task")

        assert [t["content"] for t in todo.get_tasks("sess-a")] == ["A's task"]
        assert [t["content"] for t in todo.get_tasks("sess-b")] == ["B's task"]

    @pytest.mark.asyncio
    async def test_an_interleaved_turn_does_not_see_the_other_sessions_tasks(self):
        todo = TodoTool()
        with todo.active("sess-a"):
            await todo.execute("add", content="A's task")
            with todo.active("sess-b"):
                listing = await todo.execute("list")
        assert listing.output == "No tasks", "B must not read A's plan mid-flight"

    @pytest.mark.asyncio
    async def test_the_binding_is_released_after_the_turn(self):
        # The ContextVar is reset on exit, so a later bare call falls back to the
        # process-wide binding rather than leaking the finished turn's session.
        todo = TodoTool()
        todo.bind("sess-rest")
        with todo.active("sess-a"):
            await todo.execute("add", content="A's task")
        await todo.execute("add", content="REST task")
        assert [t["content"] for t in todo.get_tasks("sess-rest")] == ["REST task"]
        assert [t["content"] for t in todo.get_tasks("sess-a")] == ["A's task"]

    @pytest.mark.asyncio
    async def test_an_explicit_session_id_still_wins(self):
        todo = TodoTool()
        with todo.active("sess-a"):
            await todo.execute("add", content="via binding")
            await todo.save_session("sess-b")
        # save_session("sess-b") ran under A's binding but was told exactly which
        # session to write, and must not have touched A's plan.
        assert [t["content"] for t in todo.get_tasks("sess-b")] == []


@pytest.mark.asyncio
async def test_a_failed_load_is_retried_on_the_next_call(monkeypatch):
    """A transient DB error must not permanently hide a plan.

    Marking a session "loaded" before its read succeeded would pin the empty
    in-memory list in place: the next turn would start from that empty list and
    then overwrite the real plan on disk.
    """
    from codeassist.session import get_db, init_db

    await init_db()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO plan_tasks (session_id, task_id, content, status, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("sess-retry", 7, "Written elsewhere", "pending", 0, "2024-01-01T00:00:00+00:00"),
        )
        await db.commit()

    todo = TodoTool()

    def _boom():
        # get_db() is used as `async with get_db() as db`, so raise on call
        # rather than returning an un-awaited coroutine.
        raise RuntimeError("database is locked")

    monkeypatch.setattr("codeassist.session.get_db", _boom)
    await todo.load_session("sess-retry")
    assert todo.get_tasks("sess-retry") == [], "the failed read yields an empty list"
    assert "sess-retry" not in todo._loaded, "a failed read must not count as loaded"

    monkeypatch.undo()
    await todo.load_session("sess-retry")
    assert [t["content"] for t in todo.get_tasks("sess-retry")] == ["Written elsewhere"], (
        "the retry picked the plan back up instead of accepting the empty list"
    )
