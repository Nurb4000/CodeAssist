"""The `todo` tool: the model-facing task/plan list.

The list is per-session state. It used to be a single list on one process-wide
tool instance, which meant a server restart threw the plan away and every
session in the process shared (and cleared) the same one. It is now keyed by
session id and written through to the `plan_tasks` table, so a plan belongs to
the conversation that produced it and survives a restart.
"""
import contextlib
import logging
import re
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from . import Tool, ToolResult

log = logging.getLogger(__name__)

# Bucket for the no-session case (bare tool in tests, or called before the
# WebSocket has bound one). Kept in memory only -- there is nothing to key on.
_NO_SESSION = "\x00no-session"

# The session a *running turn* belongs to. A ContextVar rather than a plain
# attribute: each asyncio task gets its own copy of the context, so two sessions
# streaming at the same time each resolve their own plan. A shared attribute
# would let whichever run bound last win, and a todo call would then be written
# into the wrong conversation's plan.
_active_session: ContextVar[str | None] = ContextVar("todo_active_session", default=None)

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
    "inprogress": "in_progress",
    "wip": "in_progress",
    "active": "in_progress",
    "started": "in_progress",
    "working": "in_progress",
    "working on": "in_progress",
    "working_on": "in_progress",
    "running": "in_progress",
    "ongoing": "in_progress",
    "doing": "in_progress",
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "finished": "completed",
    "finished!": "completed",
    "closed": "completed",
    "committed": "completed",
    "pushed": "completed",
}
VALID_STATUSES = set(STATUS_ALIASES.values())


def _normalize_status(status: str) -> str | None:
    if status is None:
        return None
    key = str(status).strip().lower().replace("-", " ").replace("_", " ")
    return STATUS_ALIASES.get(key)


# Models often write the status into the task *text* ("Fix X — IN PROGRESS",
# "Y — DONE: notes", "[completed] synced requirements") and leave `status` at its
# default. Those tags used to be ignored, so the plan showed the item as pending
# even though the text said work had started or finished. Only unmistakable tags
# count: a bracketed word, an uppercase label after a dash/colon, or one at the
# very end. Ordinary prose ("done by Friday", "a DONE handler") is left alone.
_BRACKETED_STATUS_RE = re.compile(
    r"[\[(]\s*(done|complete|completed|finished|closed|in[ -]?progress|wip)\s*[\])]",
    re.IGNORECASE,
)
_BARE_STATUS_RE = re.compile(
    r"(?:^|[—–:;|]\s*|\s+-\s+)(DONE|COMPLETE|COMPLETED|FINISHED|CLOSED|COMMITTED|PUSHED|IN[ -]?PROGRESS|WIP)"
    r"(?=$|[\s—–:;,.!|)])"
)
_TAIL_STATUS_RE = re.compile(r"\s+(DONE|COMPLETED|IN[ -]?PROGRESS|WIP)\s*[.!]?$")


def _status_from_content(content: str | None) -> str | None:
    """Read an explicit status tag from task text, or None if there is none."""
    if not content:
        return None
    tags = [m.group(1) for m in _BRACKETED_STATUS_RE.finditer(content)]
    tags += [m.group(1) for m in _BARE_STATUS_RE.finditer(content)]
    tags += [m.group(1) for m in _TAIL_STATUS_RE.finditer(content)]
    for tag in tags:
        status = _normalize_status(tag)
        if status and status != "pending":
            return status
    return None


def _effective_status(task: dict) -> str:
    """Status to display for a task, honouring text tags on legacy rows."""
    status = _normalize_status(task.get("status"))
    if status and status != "pending":
        return status
    return _status_from_content(task.get("content")) or "pending"


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

    def coerce_arguments(self, arguments: dict) -> dict:
        """Canonicalize ids/statuses before the registry's strict validation.

        ``_coerce_task_id`` and ``_normalize_status`` used to run only inside
        ``execute()``, but the registry checks ``task_id`` as an integer and
        ``status`` against a three-value enum first. Models frequently send
        ``"1"``, ``"done"`` or ``"Completed"``, so those updates were rejected
        before they ever reached the tool and the plan stayed stale. Unknown
        values are left alone so validation (or ``execute``) still rejects them.
        """
        args = dict(arguments)
        if "task_id" in args:
            tid = _coerce_task_id(args["task_id"])
            if tid is not None:
                args["task_id"] = tid
        if "status" in args:
            normalized = _normalize_status(args["status"])
            if normalized is not None:
                args["status"] = normalized
        return args

    def __init__(self):
        # session id -> ordered task list, and the next id to hand out in each.
        self._tasks: dict[str, list[dict]] = {}
        self._next_id: dict[str, int] = {}
        # Set by the WebSocket handler on connect (same hook the `session` tool
        # uses) so a bare `get_tasks()`/`execute()` targets the live session.
        self.current_session_id: str | None = None
        self._loaded: set[str] = set()

    # ── session scoping ─────────────────────────────────────────────────────

    def bind(self, session_id: str | None):
        """Set the process-wide fallback session (on WebSocket connect).

        This only covers calls that pass no session at all -- the REST route
        and bare `get_tasks()`. A running turn uses `active()` instead, so
        concurrent runs cannot clobber each other.
        """
        self.current_session_id = session_id

    @contextlib.contextmanager
    def active(self, session_id: str | None) -> Iterator[None]:
        """Bind this task's session for the duration of a turn."""
        token = _active_session.set(session_id)
        try:
            yield
        finally:
            _active_session.reset(token)

    def _key(self, session_id: str | None = None) -> str:
        if session_id:
            return session_id
        return _active_session.get() or self.current_session_id or _NO_SESSION

    def _list(self, session_id: str | None = None) -> list[dict]:
        return self._tasks.setdefault(self._key(session_id), [])

    def get_tasks(self, session_id: str | None = None) -> list[dict]:
        return list(self._list(session_id))

    async def load_session(self, session_id: str | None = None) -> list[dict]:
        """Hydrate a session's list from the database.

        Idempotent per session, so it is cheap to call on every connect and on
        every turn. Must run before the list is read for a session this process
        has not served yet -- that is the restart case, where the only copy of
        the plan is on disk.
        """
        key = self._key(session_id)
        if key in self._loaded:
            return self.get_tasks(session_id)
        if key == _NO_SESSION:
            self._loaded.add(key)
            return self.get_tasks(session_id)
        try:
            from codeassist.session import get_db

            async with get_db() as db:
                cursor = await db.execute(
                    "SELECT task_id, content, status FROM plan_tasks "
                    "WHERE session_id = ? ORDER BY position, task_id",
                    (key,),
                )
                rows = await cursor.fetchall()
        except Exception as e:  # noqa: BLE001
            # Bookkeeping must never break a turn. The list stays empty and the
            # model rebuilds it; the failure is visible in the log. Deliberately
            # *not* marked loaded, so a later call (next turn, next connect)
            # retries instead of treating the empty list as the truth.
            log.warning("Could not load plan tasks for session %s: %s", key, e)
            return self.get_tasks(session_id)

        self._tasks[key] = [
            {"id": r["task_id"], "content": r["content"], "status": r["status"]} for r in rows
        ]
        self._next_id[key] = (max((r["task_id"] for r in rows), default=0)) + 1
        self._loaded.add(key)
        return self.get_tasks(session_id)

    async def save_session(self, session_id: str | None = None):
        """Write a session's list out, replacing whatever is stored.

        Write-through on every mutation: the plan is the user's record of what
        the agent is doing, so a crash or restart must not cost it.
        """
        key = self._key(session_id)
        if key == _NO_SESSION:
            return
        tasks = self._list(key)
        try:
            from codeassist.session import get_db

            async with get_db() as db:
                await db.execute("DELETE FROM plan_tasks WHERE session_id = ?", (key,))
                if tasks:
                    now = datetime.now(UTC).isoformat()
                    await db.executemany(
                        "INSERT INTO plan_tasks "
                        "(session_id, task_id, content, status, position, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            (key, t["id"], t["content"], t["status"], i, now)
                            for i, t in enumerate(tasks)
                        ],
                    )
                await db.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("Could not persist plan tasks for session %s: %s", key, e)

    async def clear_tasks(self, session_id: str | None = None):
        """Empty a session's plan, in memory and on disk."""
        key = self._key(session_id)
        self._tasks[key] = []
        self._next_id[key] = 1
        await self.save_session(key)

    # ── tool surface ────────────────────────────────────────────────────────

    async def execute(self, action: str, task_id: int | None = None, content: str | None = None, status: str | None = None) -> ToolResult:
        key = self._key()
        tasks = self._list(key)

        if action == "add":
            if not content:
                return ToolResult(output="Error: content is required for add", error=True)
            normalized = _normalize_status(status)
            if status is not None and normalized is None:
                return ToolResult(
                    output=f"Error: unknown status '{status}'. Use one of: {', '.join(sorted(VALID_STATUSES))}",
                    error=True,
                )
            if normalized in (None, "pending"):
                normalized = _status_from_content(content) or normalized or "pending"
            next_id = self._next_id.setdefault(key, 1)
            task = {"id": next_id, "content": content, "status": normalized}
            tasks.append(task)
            self._next_id[key] = next_id + 1
            await self.save_session(key)
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
            # A content rewrite is often how the model records progress ("— DONE").
            # Treat that as a status change too, so the plan does not stay gray.
            if content is not None and normalized in (None, "pending"):
                normalized = _status_from_content(content) or normalized
            if content is None and normalized is None:
                return ToolResult(
                    output="Error: provide content and/or status to update",
                    error=True,
                )
            for task in tasks:
                if task["id"] == tid:
                    if content is not None:
                        task["content"] = content
                    if normalized is not None:
                        task["status"] = normalized
                    await self.save_session(key)
                    return ToolResult(output=f"Updated task #{task['id']}: {task['content']} [{task['status']}]")
            ids = ", ".join(f"#{t['id']}" for t in tasks) or "none"
            return ToolResult(output=f"Error: task #{tid} not found (tasks: {ids})", error=True)

        elif action == "list":
            if not tasks:
                return ToolResult(output="No tasks")
            lines = []
            for t in tasks:
                status = _effective_status(t)
                marker = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}.get(status, "[ ]")
                lines.append(f"#{t['id']} {marker} {t['content']}")
            return ToolResult(output="\n".join(lines))

        elif action == "clear":
            await self.clear_tasks(key)
            return ToolResult(output="Task list cleared")

        return ToolResult(output=f"Error: unknown action '{action}'", error=True)
