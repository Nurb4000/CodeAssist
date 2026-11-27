"""Unattended automations: cron / interval / event triggers running a prompt or shell command.

Design constraints that shape this module:

* Schedules are persisted as ``next_run_at``, so a server restart resumes rather
  than re-arming every job (which would duplicate runs on every boot).
* ``tick()`` only decides what is due and spawns tasks. It never awaits a job, so
  one slow or failing run cannot delay the next one.
* Every run gets an append-only row with its status and (truncated) output. A
  failure is recorded and reported, never re-raised into the scheduler loop.
* Shell actions are admin-configured and gated by ``[automations].allow_shell``;
  automations are disabled entirely by default because they run unattended.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from .session import Session, get_db

log = logging.getLogger(__name__)

KINDS = ("cron", "interval", "event")
ACTIONS = ("prompt", "shell")
MAX_OUTPUT_CHARS = 8000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class CronSchedule:
    """A small, dependency-free 5-field cron expression (minute hour dom month dow)."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]

    def next_after(self, now: datetime) -> datetime | None:
        """First matching minute strictly after ``now``, or None if unreachable.

        The 367-day bound keeps an impossible expression such as ``0 0 30 2 *``
        from spinning forever. Weekday 0 is Sunday, as in cron; when both day of
        month and day of week are restricted the standard OR rule applies.
        """
        candidate = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        limit = candidate + timedelta(days=367)
        all_days = self.days == frozenset(range(1, 32))
        all_weekdays = self.weekdays == frozenset(range(7))
        while candidate < limit:
            if (
                candidate.minute in self.minutes
                and candidate.hour in self.hours
                and candidate.month in self.months
            ):
                dom_match = candidate.day in self.days
                dow_match = ((candidate.weekday() + 1) % 7) in self.weekdays
                if all_days and all_weekdays:
                    day_match = True
                elif all_days:
                    day_match = dow_match
                elif all_weekdays:
                    day_match = dom_match
                else:
                    day_match = dom_match or dow_match
                if day_match:
                    return candidate
            candidate += timedelta(minutes=1)
        return None


def _cron_field(spec: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in spec.split(","):
        if not part:
            raise ValueError("empty cron field")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ValueError(f"invalid cron step: {step_text}") from exc
            if step < 1:
                raise ValueError("cron step must be positive")
        if part == "*":
            start, end = low, high
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ValueError(f"invalid cron range: {part}") from exc
        else:
            try:
                start = end = int(part)
            except ValueError as exc:
                raise ValueError(f"invalid cron value: {part}") from exc
        if start < low or end > high or start > end:
            raise ValueError(f"cron value out of range: {part}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


def parse_cron(expression: str) -> CronSchedule:
    """Parse a 5-field cron expression, raising ValueError on anything invalid."""
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron expression needs 5 fields: minute hour dom month dow")
    minute, hour, dom, month, dow = fields
    weekdays = _cron_field(dow, 0, 7)
    if 7 in weekdays:  # cron accepts 7 as Sunday
        weekdays = frozenset((0 if d == 7 else d) for d in weekdays)
    return CronSchedule(
        minutes=_cron_field(minute, 0, 59),
        hours=_cron_field(hour, 0, 23),
        days=_cron_field(dom, 1, 31),
        months=_cron_field(month, 1, 12),
        weekdays=weekdays,
    )


def compute_next(kind: str, schedule: str, now: datetime | None = None) -> str | None:
    """Return the next fire time as ISO text, or None for event-driven jobs."""
    now = now or datetime.now(UTC)
    if kind == "cron":
        due = parse_cron(schedule).next_after(now)
        return due.isoformat() if due else None
    if kind == "interval":
        try:
            seconds = int(schedule)
        except ValueError as exc:
            raise ValueError("interval must be a whole number of seconds") from exc
        if seconds < 1:
            raise ValueError("interval must be at least 1 second")
        return (now + timedelta(seconds=seconds)).isoformat()
    if kind == "event":
        if not schedule.strip():
            raise ValueError("event automations need an event name")
        return None
    raise ValueError(f"unknown automation kind: {kind}")


def validate_automation(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a create/update payload (partial updates allowed)."""
    allowed = {
        "name", "kind", "schedule", "action", "payload", "session_id",
        "enabled", "max_runs",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown automation field(s): {', '.join(sorted(unknown))}")
    result: dict[str, Any] = {}
    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            raise ValueError("name is required")
        result["name"] = name
    if "kind" in data:
        if data["kind"] not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        result["kind"] = data["kind"]
    if "action" in data:
        if data["action"] not in ACTIONS:
            raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
        result["action"] = data["action"]
    if "schedule" in data:
        schedule = str(data["schedule"]).strip()
        if not schedule:
            raise ValueError("schedule is required")
        result["schedule"] = schedule
    if "payload" in data:
        payload = str(data["payload"])
        if not payload.strip():
            raise ValueError("payload is required")
        result["payload"] = payload
    if "session_id" in data:
        session_id = data["session_id"]
        result["session_id"] = str(session_id).strip() if session_id else None
    if "enabled" in data:
        result["enabled"] = bool(data["enabled"])
    if "max_runs" in data:
        try:
            max_runs = int(data["max_runs"])
        except (TypeError, ValueError) as exc:
            raise ValueError("max_runs must be a whole number") from exc
        if max_runs < 0:
            raise ValueError("max_runs cannot be negative")
        result["max_runs"] = max_runs
    # Validate the resulting schedule against the resulting kind. On updates the
    # two may arrive separately, so use the stored values in the store layer.
    if "kind" in result and "schedule" in result:
        compute_next(result["kind"], result["schedule"])
    return result


class AutomationStore:
    """SQLite persistence for automations and their run history."""

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        return data

    async def list_all(self) -> list[dict[str, Any]]:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM automations ORDER BY created_at, name")
            return [self._dict(row) for row in await cursor.fetchall()]

    async def get(self, automation_id: str) -> dict[str, Any] | None:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM automations WHERE id = ?", (automation_id,)
            )
            row = await cursor.fetchone()
            return self._dict(row) if row else None

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        automation_id = str(uuid.uuid4())
        next_run = compute_next(data["kind"], data["schedule"])
        async with get_db() as db:
            await db.execute(
                """INSERT INTO automations
                   (id, name, kind, schedule, action, payload, session_id, enabled,
                    max_runs, run_count, next_run_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (
                    automation_id, data["name"], data["kind"], data["schedule"],
                    data["action"], data["payload"], data.get("session_id"),
                    int(data.get("enabled", True)), int(data.get("max_runs", 0)),
                    next_run, now, now,
                ),
            )
            await db.commit()
        result = await self.get(automation_id)
        assert result is not None
        return result

    async def update(self, automation_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        current = await self.get(automation_id)
        if current is None:
            return None
        merged = {**current, **patch}
        # Re-arm from now when the schedule or kind changes; otherwise keep the
        # persisted next_run_at so edits do not duplicate an imminent run.
        if "schedule" in patch or "kind" in patch:
            merged["next_run_at"] = compute_next(merged["kind"], merged["schedule"])
        async with get_db() as db:
            await db.execute(
                """UPDATE automations SET name=?, kind=?, schedule=?, action=?, payload=?,
                   session_id=?, enabled=?, max_runs=?, next_run_at=?, updated_at=?
                   WHERE id=?""",
                (
                    merged["name"], merged["kind"], merged["schedule"],
                    merged["action"], merged["payload"], merged["session_id"],
                    int(merged["enabled"]), int(merged["max_runs"]),
                    merged["next_run_at"], _now_iso(), automation_id,
                ),
            )
            await db.commit()
        return await self.get(automation_id)

    async def set_next_run(self, automation_id: str, next_run_at: str | None) -> None:
        async with get_db() as db:
            await db.execute(
                "UPDATE automations SET next_run_at=? WHERE id=?",
                (next_run_at, automation_id),
            )
            await db.commit()

    async def delete(self, automation_id: str) -> bool:
        async with get_db() as db:
            cursor = await db.execute("DELETE FROM automations WHERE id=?", (automation_id,))
            await db.execute("DELETE FROM automation_runs WHERE automation_id=?", (automation_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def start_run(self, automation_id: str) -> str:
        run_id = str(uuid.uuid4())
        async with get_db() as db:
            await db.execute(
                """INSERT INTO automation_runs (id, automation_id, started_at, status)
                   VALUES (?, ?, ?, 'running')""",
                (run_id, automation_id, _now_iso()),
            )
            await db.execute(
                "UPDATE automations SET run_count = run_count + 1 WHERE id=?",
                (automation_id,),
            )
            await db.commit()
        return run_id

    async def finish_run(
        self, run_id: str, status: str, output: str = "", error: str = ""
    ) -> None:
        async with get_db() as db:
            await db.execute(
                """UPDATE automation_runs SET finished_at=?, status=?, output=?, error=?
                   WHERE id=?""",
                (_now_iso(), status, output[:MAX_OUTPUT_CHARS], error[:MAX_OUTPUT_CHARS], run_id),
            )
            await db.execute(
                """UPDATE automations SET last_run_at=?, last_status=?, last_error=?
                   WHERE id=(SELECT automation_id FROM automation_runs WHERE id=?)""",
                (_now_iso(), status, error[:MAX_OUTPUT_CHARS] or None, run_id),
            )
            await db.commit()

    async def list_runs(self, automation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with get_db() as db:
            cursor = await db.execute(
                """SELECT * FROM automation_runs WHERE automation_id=?
                   ORDER BY started_at DESC LIMIT ?""",
                (automation_id, max(1, min(int(limit), 200))),
            )
            return [dict(row) for row in await cursor.fetchall()]


PromptRunner = Callable[[dict[str, Any], Session], Awaitable[str]]
HookRunner = Callable[..., Awaitable[list[Any]]]
Deliverer = Callable[[dict[str, Any]], Awaitable[None]]


async def default_prompt_runner(automation: dict[str, Any], session: Session, tools: Any, cfg: Any) -> str:
    """Run a prompt as a normal agent turn, returning the final assistant text."""
    from .agent import Agent
    from .prompts import build_system_prompt

    prompt = build_system_prompt(
        workspace=cfg.workspace,
        model_id=cfg.llm.model,
        features={
            "mcp_enabled": cfg.mcp.enabled,
            "skills_enabled": cfg.skills.enabled,
            "plugins_enabled": cfg.plugins.enabled,
            "lsp_enabled": cfg.lsp.enabled,
            "git_enabled": cfg.git.enabled,
            "remote_test_vm": "off",
        },
        instructions=None,
    )
    agent = Agent(cfg, session, tools, prompt)
    chunks: list[str] = []
    async for event in agent.run(str(automation["payload"])):
        if event.type == "text_delta":
            chunks.append(str(event.data.get("content", "")))
        elif event.type == "error":
            raise RuntimeError(event.data.get("message") or "agent turn failed")
    return "".join(chunks).strip()


class AutomationEngine:
    """Tick loop and task supervisor for automations."""

    def __init__(
        self,
        cfg: Any,
        store: AutomationStore | None = None,
        tools: Any = None,
        prompt_runner: PromptRunner | None = None,
        hook_runner: HookRunner | None = None,
        deliver: Deliverer | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store or AutomationStore()
        self.tools = tools
        self._prompt_runner = prompt_runner
        # Injected by the app lifespan: plugin hooks may contribute follow-up
        # event names, and prompt results are delivered as WebSocket frames.
        self._hook_runner = hook_runner
        self._deliver = deliver
        self._tasks: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task:
        async def guarded():
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive: one job must never kill the loop
                log.exception("Automation task failed")

        task = asyncio.create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def tick(self, now: datetime | None = None) -> list[str]:
        """Arm due jobs and return their ids. Due jobs are never awaited here."""
        now = now or datetime.now(UTC)
        due: list[str] = []
        for automation in await self.store.list_all():
            if not automation["enabled"]:
                continue
            max_runs = int(automation.get("max_runs") or 0)
            if max_runs and int(automation.get("run_count") or 0) >= max_runs:
                continue
            if automation["kind"] == "event":
                continue
            scheduled = _parse_iso(automation.get("next_run_at"))
            if scheduled is None:
                # Backfill for jobs created before scheduling state existed.
                scheduled = _parse_iso(compute_next(automation["kind"], automation["schedule"], now))
            if scheduled is None or scheduled > now:
                continue
            next_run = compute_next(automation["kind"], automation["schedule"], now)
            await self.store.set_next_run(automation["id"], next_run)
            self._spawn(self.run(automation))
            due.append(automation["id"])
        return due

    async def trigger_event(self, event_name: str) -> int:
        """Run every enabled event automation registered for ``event_name``.

        Plugins registered on ``automation.trigger`` may return event names (or
        dicts with an ``event`` key) to fan out follow-up events. The depth and
        seen-set bounds keep a buggy plugin from creating an infinite cascade.
        """
        seen: set[str] = set()
        pending = [event_name]
        triggered = 0
        automations = await self.store.list_all()
        for _depth in range(5):
            if not pending:
                break
            batch, pending = pending, []
            for name in batch:
                if name in seen:
                    continue
                seen.add(name)
                for automation in automations:
                    if (
                        automation["enabled"]
                        and automation["kind"] == "event"
                        and automation["schedule"] == name
                    ):
                        max_runs = int(automation.get("max_runs") or 0)
                        if max_runs and int(automation.get("run_count") or 0) >= max_runs:
                            continue
                        self._spawn(self.run(automation))
                        triggered += 1
                if self._hook_runner is None:
                    continue
                try:
                    results = await self._hook_runner("automation.trigger", name)
                except Exception:  # noqa: BLE001 - a plugin must not break automation
                    log.exception("automation.trigger hook failed for %s", name)
                    continue
                for result in results or []:
                    derived = (
                        result
                        if isinstance(result, str)
                        else result.get("event")
                        if isinstance(result, dict)
                        else None
                    )
                    if derived and derived not in seen:
                        pending.append(str(derived))
        return triggered

    async def run_now(self, automation_id: str) -> bool:
        automation = await self.store.get(automation_id)
        if automation is None:
            return False
        self._spawn(self.run(automation))
        return True

    async def run(self, automation: dict[str, Any]) -> str:
        """Execute one job. Always records a terminal status; never raises."""
        run_id = await self.store.start_run(automation["id"])
        output = ""
        error = ""
        status = "ok"
        try:
            if automation["action"] == "shell":
                if not getattr(self.cfg.automations, "allow_shell", False):
                    raise RuntimeError("shell automations are disabled by [automations].allow_shell")
                timeout = int(getattr(self.cfg.automations, "timeout_seconds", 300))
                process = await asyncio.create_subprocess_shell(
                    str(automation["payload"]),
                    cwd=str(self.cfg.workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
                output = (stdout or b"").decode("utf-8", errors="replace").strip()
                if process.returncode:
                    error = f"exit status {process.returncode}"
                    status = "error"
            else:
                session_id = automation.get("session_id") or f"automation-{automation['id']}"
                session = await Session.get_or_create(session_id)
                if self._prompt_runner is not None:
                    output = str(await self._prompt_runner(automation, session))
                else:
                    output = await default_prompt_runner(automation, session, self.tools, self.cfg)
        except asyncio.CancelledError:
            await self.store.finish_run(run_id, "cancelled", output, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - the run row is the failure report
            status, error = "error", str(exc)
            log.warning("Automation %s failed: %s", automation["id"], exc, exc_info=True)
        await self.store.finish_run(run_id, status, output, error)
        # Shell output can contain secrets and stays in the admin run history;
        # only prompt results are pushed to connected clients.
        if self._deliver is not None and automation["action"] == "prompt":
            try:
                await self._deliver({
                    "type": "automation_result",
                    "automation_id": automation["id"],
                    "name": automation["name"],
                    "status": status,
                    "output": output[:MAX_OUTPUT_CHARS],
                    "error": error[:MAX_OUTPUT_CHARS],
                    "session_id": automation.get("session_id"),
                })
            except Exception:  # noqa: BLE001 - delivery is best-effort
                log.exception("Could not deliver automation result")
        return status

    async def _loop(self) -> None:
        interval = max(0.1, float(getattr(self.cfg.automations, "tick_seconds", 1.0)))
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - keep the loop alive
                log.exception("Automation tick failed")
            await asyncio.sleep(interval)

    def start(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        tasks = [task for task in (self._loop_task, *self._tasks) if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._loop_task = None


# Set by the app lifespan. Routes read this so tests can swap in an isolated engine.
automation_engine: AutomationEngine | None = None
