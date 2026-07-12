from pathlib import Path
from tools import Tool, ToolResult


class TodoTool(Tool):
    name = "todo"
    description = "Manage a task list. Use to track progress on multi-step work."
    workspace = Path(".")

    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "update", "list", "clear"], "description": "Action to perform"},
            "task_id": {"type": "integer", "description": "Task ID (for update)"},
            "content": {"type": "string", "description": "Task description (for add/update)"},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"], "description": "Task status (for update)"},
        },
        "required": ["action"],
    }

    def __init__(self):
        self._tasks: list[dict] = []
        self._next_id = 1

    def get_tasks(self) -> list[dict]:
        return list(self._tasks)

    def clear_tasks(self):
        self._tasks.clear()
        self._next_id = 1

    async def execute(self, action: str, task_id: int | None = None, content: str | None = None, status: str | None = None) -> ToolResult:
        if action == "add":
            if not content:
                return ToolResult(output="Error: content is required for add", error=True)
            task = {"id": self._next_id, "content": content, "status": status or "pending"}
            self._tasks.append(task)
            self._next_id += 1
            return ToolResult(output=f"Added task #{task['id']}: {content}")

        elif action == "update":
            if task_id is None:
                return ToolResult(output="Error: task_id is required for update", error=True)
            for task in self._tasks:
                if task["id"] == task_id:
                    if content:
                        task["content"] = content
                    if status:
                        task["status"] = status
                    return ToolResult(output=f"Updated task #{task_id}: {task['content']} [{task['status']}]")
            return ToolResult(output=f"Error: task #{task_id} not found", error=True)

        elif action == "list":
            if not self._tasks:
                return ToolResult(output="No tasks")
            lines = []
            for t in self._tasks:
                marker = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}.get(t["status"], "[ ]")
                lines.append(f"#{t['id']} {marker} {t['content']}")
            return ToolResult(output="\n".join(lines))

        elif action == "clear":
            self._tasks.clear()
            self._next_id = 1
            return ToolResult(output="Task list cleared")

        return ToolResult(output=f"Error: unknown action '{action}'", error=True)
