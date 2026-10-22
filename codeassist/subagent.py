import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

log = logging.getLogger(__name__)


@dataclass
class SubagentTask:
    """Represents a subagent task."""
    id: str
    description: str
    prompt: str
    subagent_type: str  # "explore", "general", "build"
    parent_session_id: str
    child_session_id: str = ""
    background: bool = False
    status: str = "pending"  # pending, running, completed, error, cancelled
    result: str = ""
    error: str = ""
    created_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "subagent_type": self.subagent_type,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "background": self.background,
        }


class SubagentManager:
    """Manages subagent lifecycle: spawning, tracking, and result collection."""

    def __init__(self):
        self._tasks: dict[str, SubagentTask] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._notifications: dict[str, asyncio.Queue] = {}
        # Optional server-registered hook delivering a background task's
        # completion to its parent session. Set by server.py; without it the
        # notification queue is still drained (see _notify_parent).
        self.on_background_complete = None

    def get_task(self, task_id: str) -> SubagentTask | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict]:
        """List all tasks."""
        return [t.to_dict() for t in self._tasks.values()]

    def get_active_tasks(self) -> list[SubagentTask]:
        """Get tasks that are pending or running."""
        return [t for t in self._tasks.values() if t.status in ("pending", "running")]

    async def create_task(
        self,
        description: str,
        prompt: str,
        subagent_type: str,
        parent_session_id: str,
        background: bool = False,
        task_id: str | None = None,
    ) -> SubagentTask:
        """Create a new subagent task."""
        tid = task_id or str(uuid.uuid4())[:8]

        # Check if resuming existing task
        existing = self._tasks.get(tid)
        if existing:
            log.info("Resuming existing subagent task: %s", tid)
            return existing

        task = SubagentTask(
            id=tid,
            description=description,
            prompt=prompt,
            subagent_type=subagent_type,
            parent_session_id=parent_session_id,
            background=background,
            status="pending",
            created_at=datetime.now(UTC).isoformat(),
        )

        self._tasks[tid] = task
        self._notifications[tid] = asyncio.Queue()

        # Persist to database
        await self._persist_task(task)

        log.info("Created subagent task: %s (type=%s, background=%s)", tid, subagent_type, background)
        return task

    async def start_task(
        self,
        task_id: str,
        agent_runner,  # Callable that runs the agent loop
        config,
        tools_registry,
        depth: int = 0,
    ) -> str:
        """Start a subagent task and return its result string.

        The run is tracked as an ``asyncio.Task`` in ``_running`` so
        ``cancel_task`` can actually stop it. Previously ``_running`` was never
        populated, so cancel only flipped the status flag while the loop kept
        running -- and the completion path then overwrote "cancelled" with
        "completed".
        """
        task = self._tasks.get(task_id)
        if not task:
            return f"Error: task '{task_id}' not found"

        # Check depth limit
        max_depth = config.agent.subagent_depth
        if depth >= max_depth:
            task.status = "error"
            task.error = f"Max subagent depth ({max_depth}) reached"
            await self._persist_task(task)
            return task.error

        task.status = "running"
        run_task = asyncio.create_task(
            self._run_subagent(task, agent_runner, config, tools_registry, depth),
        )
        self._running[task_id] = run_task
        try:
            return await run_task
        except asyncio.CancelledError:
            # Distinguish an external cancel_task (run_task was cancelled) from
            # this coroutine being cancelled itself. Only the former should
            # surface a normal result; the latter must propagate to honour
            # cancellation of the caller.
            if run_task.cancelled():
                return f"Subagent '{task_id}' was cancelled."
            raise
        finally:
            self._running.pop(task_id, None)

    async def _run_subagent(
        self,
        task: SubagentTask,
        agent_runner,
        config,
        tools_registry,
        depth: int,
    ) -> str:
        """Run the subagent loop and persist the outcome.

        Split out from ``start_task`` so it can be tracked as its own
        ``asyncio.Task`` (cancellable via ``_running``) and so every exit path --
        completion, error, and cancellation -- persists the task to the database.
        """
        try:
            # Create child session
            from codeassist.session import Session
            child_session = await Session.create(
                name=f"subagent-{task.subagent_type}-{task.id}",
                parent_id=task.parent_session_id,
            )
            task.child_session_id = child_session.id

            # Get agent config for subagent type
            from codeassist.agents import agent_manager
            agent_config = agent_manager.get_agent(task.subagent_type)
            if not agent_config:
                task.status = "error"
                task.error = f"Unknown subagent type: {task.subagent_type}"
                await self._persist_task(task)
                return task.error

            # Build system prompt for subagent
            system_prompt = agent_config.get_system_prompt()
            system_prompt += f"\n\n## Task\n{task.prompt}\n\nYou are a subagent. " \
                            f"Report your findings concisely. Do NOT use the 'task' tool to spawn sub-subagents."

            # Run the agent
            from codeassist.agent import Agent
            sub_agent = Agent(
                config,
                child_session,
                tools_registry,
                system_prompt,
                # Enforce the subagent's own permission map; without it a
                # read-only subagent (explore/research) inherited the parent's
                # unrestricted access.
                agent_ruleset=agent_config.permissions.to_ruleset(),
                max_steps=agent_config.steps,
            )

            # Inject the prompt as a user message and run
            result_parts = []
            async for event in agent_runner(sub_agent, task.prompt):
                if event.type == "text_delta":
                    result_parts.append(event.data.get("content", ""))
                elif event.type == "tool_result":
                    pass  # Tool results are internal to subagent
                elif event.type == "error":
                    log.warning("Subagent %s error: %s", task.id, event.data)

            full_result = "".join(result_parts)

            # If no text output, generate summary from session messages
            if not full_result.strip():
                messages = await child_session.get_messages()
                assistant_msgs = [m for m in messages if m["role"] == "assistant" and m.get("content")]
                if assistant_msgs:
                    full_result = assistant_msgs[-1]["content"]
                else:
                    full_result = f"Subagent completed task '{task.description}' without producing output."

            # Don't clobber a status set by cancel_task while the loop was
            # winding down.
            if task.status != "cancelled":
                task.status = "completed"
                task.result = full_result[:5000]  # Cap result size
                task.completed_at = datetime.now(UTC).isoformat()
                await self._persist_task(task)

            # Notify parent if background
            if task.background:
                await self._notify_parent(task)

            return full_result

        except asyncio.CancelledError:
            # An external cancel_task cancelled this run. Record it (unless a
            # real error already set the status) and persist so the cancellation
            # survives a restart, then re-raise so start_task can report it.
            if task.status not in ("completed", "error"):
                task.status = "cancelled"
                task.completed_at = datetime.now(UTC).isoformat()
            await self._persist_task(task)
            raise
        except Exception as e:
            log.exception("Subagent task %s failed", task.id)
            task.status = "error"
            task.error = str(e)
            task.completed_at = datetime.now(UTC).isoformat()
            await self._persist_task(task)
            raise

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task.

        Cancels the tracked ``asyncio.Task`` (so the agent loop actually stops)
        and records the cancellation. Returns False if there was nothing to
        cancel (unknown, already-finished, or non-running task).
        """
        task = self._tasks.get(task_id)
        if not task or task.status not in ("pending", "running"):
            return False

        run_task = self._running.pop(task_id, None)
        if run_task is not None:
            run_task.cancel()

        task.status = "cancelled"
        task.completed_at = datetime.now(UTC).isoformat()
        await self._persist_task(task)
        return True

    async def _persist_task(self, task: SubagentTask):
        """Persist task to database."""
        try:
            from codeassist.session import get_db
            async with get_db() as db:
                await db.execute(
                    "INSERT OR REPLACE INTO subagent_tasks "
                    "(id, parent_session_id, child_session_id, description, subagent_type, "
                    "background, status, result, created_at, completed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        task.id,
                        task.parent_session_id,
                        task.child_session_id,
                        task.description,
                        task.subagent_type,
                        1 if task.background else 0,
                        task.status,
                        task.result,
                        task.created_at,
                        task.completed_at,
                    ),
                )
                await db.commit()
        except Exception as e:  # noqa: BLE001
            log.debug("Failed to persist subagent task: %s", e)

    async def _notify_parent(self, task: SubagentTask):
        """Deliver a background task's completion to its parent session.

        Drains the per-task queue (so it cannot grow unbounded) and hands the
        completed task to the server-registered ``on_background_complete`` hook
        for actual delivery. Without the hook this is still a no-op, but the
        queue entry is always drained.
        """
        try:
            queue = self._notifications.pop(task.id, None)
            if queue is not None:
                while not queue.empty():
                    queue.get_nowait()
            if self.on_background_complete is not None:
                await self.on_background_complete(task)
        except Exception as e:  # noqa: BLE001
            log.debug("Failed to notify parent of subagent completion: %s", e)


# Singleton instance
subagent_manager = SubagentManager()
