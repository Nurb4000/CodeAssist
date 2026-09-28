"""Tests for the todo (task list / plan) tool."""
import pytest

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
            todo.clear_tasks()
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