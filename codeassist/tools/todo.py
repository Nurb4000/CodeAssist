from pathlib import Path

from . import Tool, ToolResult

# Accepted status spellings -> canonical todo status. Models frequently use
# "done", "complete", "finished" or "active"/"in progress" instead of the
# canonical enum; without this a well-intentioned update would set a status the
# plan UI cannot render, leaving an item looking stuck (e.g. never completed).
STATUS_ALIASES = {
    "pending": "pending",
    "todo": "pending",
    "not started": "pending",
    "not_started": "pending",
    "backlog": "pending",
    "in_progress": "in_progress",
    "in progress": "in_progress",
    "active": "in_progress",
    "started": "in_progress",
    "working": "in_progress",
    "working_on": "in_progress",
    "running": "in_progress",
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "finished": "completed",
    "finished!": "completed",
    "closed": "completed",
}
VALID_STATUSES = set(STATUS_ALIASES.values())


def _normalize_status(status: str) -> str | None:
    if status is None:
        return None
    key = str(status).strip().lower().replace("-", " ").replace("_", " ")
    return STATUS_ALIASES.get(key)


def _coerce_task_id(task_id) -> int | None:
    """Accept int ids and numeric string ids (LLMs often serialize numbers as
    strings, and ``1 == "1"`` is False — a mismatch that used to silently drop
    the update and leave the plan stuck)."""
    if isinstance(task_id, bool) or task_id is None:
        return None
    try:
        return int(task_id)
    except (TypeError, ValueError):
        return None


class TodoTool(Tool):
    name = "todo"
    description = "Manage a task list. Use to track progress on multi-step work."
    workspace = Path(".")

    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "update", "list", "clear"], "description": "Action to perform"},
            "task_id": {"type": "integer", "description": "Task ID (for update). May be a numeric string."},
            "content": {"type": "string", "description": "Task description (for add/update)"},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"], "description": "Task status (for update). Aliases like done/complete/active are accepted."},
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
            normalized = _normalize_status(status)
            if status is not None and normalized is None:
                return ToolResult(
                    output=f"Error: unknown status '{status}'. Use one of: {', '.join(sorted(VALID_STATUSES))}",
                    error=True,
                )
            task = {"id": self._next_id, "content": content, "status": normalized or "pending"}
            self._tasks.append(task)
            self._next_id += 1
            return ToolResult(output=f"Added task #{task['id']}: {content}")

        elif action == "update":
            tid = _coerce_task_id(task_id)
            if tid is None:
                return ToolResult(
                    output="Error: task_id is required and must be an integer for update",
                    error=True,
                )
            normalized = _normalize_status(status) if status else None
            if status is not None and normalized is None:
                return ToolResult(
                    output=f"Error: unknown status '{status}'. Use one of: {', '.join(sorted(VALID_STATUSES))}",
                    error=True,
                )
            if content is None and normalized is None:
                return ToolResult(
                    output="Error: provide content and/or status to update",
                    error=True,
                )
            for task in self._tasks:
                if task["id"] == tid:
                    if content is not None:
                        task["content"] = content
                    if normalized is not None:
                        task["status"] = normalized
                    return ToolResult(output=f"Updated task #{task['id']}: {task['content']} [{task['status']}]")
            ids = ", ".join(f"#{t['id']}" for t in self._tasks) or "none"
            return ToolResult(output=f"Error: task #{tid} not found (tasks: {ids})", error=True)

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