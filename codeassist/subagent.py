import asyncio
import json
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
        """Start a subagent task. Returns result string."""
        task = self._tasks.get(task_id)
        if not task:
            return f"Error: task '{task_id}' not found"

        # Check depth limit
        max_depth = config.agent.subagent_depth
        if depth >= max_depth:
            task.status = "error"
            task.error = f"Max subagent depth ({max_depth}) reached"
            return task.error

        task.status = "running"

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
                return task.error

            # Build system prompt for subagent
            system_prompt = agent_config.get_system_prompt()
            system_prompt += f"\n\n## Task\n{task.prompt}\n\nYou are a subagent. " \
                            f"Report your findings concisely. Do NOT use the 'task' tool to spawn sub-subagents."

            # Run the agent
            from codeassist.agent import Agent
            sub_agent = Agent(config, child_session, tools_registry, system_prompt)

            # Inject the prompt as a user message and run
            result_parts = []
            async for event in agent_runner(sub_agent, task.prompt):
                if event.type == "text_delta":
                    result_parts.append(event.data.get("content", ""))
                elif event.type == "tool_result":
                    pass  # Tool results are internal to subagent
                elif event.type == "error":
                    log.warning("Subagent %s error: %s", task_id, event.data)

            full_result = "".join(result_parts)

            # If no text output, generate summary from session messages
            if not full_result.strip():
                messages = await child_session.get_messages()
                assistant_msgs = [m for m in messages if m["role"] == "assistant" and m.get("content")]
                if assistant_msgs:
                    full_result = assistant_msgs[-1]["content"]
                else:
                    full_result = f"Subagent completed task '{task.description}' without producing output."

            task.status = "completed"
            task.result = full_result[:5000]  # Cap result size
            task.completed_at = datetime.now(UTC).isoformat()

            # Notify parent if background
            if task.background:
                await self._notify_parent(task)

            return full_result

        except Exception as e:
            log.exception("Subagent task %s failed", task_id)
            task.status = "error"
            task.error = str(e)
            task.completed_at = datetime.now(UTC).isoformat()
            raise

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        task = self._tasks.get(task_id)
        if not task or task.status not in ("pending", "running"):
            return False

        task.status = "cancelled"
        task.completed_at = datetime.now(UTC).isoformat()

        # Cancel asyncio task if running
        if task_id in self._running:
            self._running[task_id].cancel()
            del self._running[task_id]

        return True

    async def _persist_task(self, task: SubagentTask):
        """Persist task to database."""
        try:
            from codeassist.session import get_db
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO todos (id, session_id, content, status, priority, created_at) "
                    "VALUES (?, ?, ?, ?, 'medium', ?)",
                    (
                        task.id,
                        task.parent_session_id,
                        json.dumps({
                            "description": task.description,
                            "subagent_type": task.subagent_type,
                            "background": task.background,
                        }),
                        task.status,
                        task.created_at,
                    ),
                )
                await db.commit()
        except Exception as e:  # noqa: BLE001
            log.debug("Failed to persist subagent task: %s", e)

    async def _notify_parent(self, task: SubagentTask):
        """Send notification to parent session for background tasks."""
        try:
            queue = self._notifications.get(task.id)
            if queue:
                await queue.put(task.to_dict())
        except Exception as e:  # noqa: BLE001
            log.debug("Failed to notify parent of subagent completion: %s", e)


# Singleton instance
subagent_manager = SubagentManager()
