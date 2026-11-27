"""Headless single-turn runner used by `codeassist run` and ACP prompt turns.

It reuses Agent.run and maps interactive confirmations/questions to safe
non-interactive defaults unless a handler is provided.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import server
from .agent import AgentEvent
from .headless import bind_session_tools, build_agent, load_session_todos
from .session import Session
from .turn_state import end_turn, start_turn

log = logging.getLogger(__name__)

EventSink = Callable[[AgentEvent], Awaitable[None] | None]
ConfirmHandler = Callable[[dict[str, Any]], Awaitable[bool] | bool]
QuestionHandler = Callable[[dict[str, Any]], Awaitable[str | list | dict | None] | str | list | dict | None]


@dataclass
class RunResult:
    session_id: str
    text: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"
    error: str | None = None


async def _maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value


async def _emit(sink: EventSink | None, event: AgentEvent) -> None:
    if sink is None:
        return
    awaitable = sink(event)
    if hasattr(awaitable, "__await__"):
        await awaitable


async def run_prompt(
    *,
    session_id: str,
    message: str,
    attachments: list[dict] | None = None,
    agent_name: str | None = None,
    sink: EventSink | None = None,
    confirm_handler: ConfirmHandler | None = None,
    question_handler: QuestionHandler | None = None,
    capture_events: bool = False,
    agent_out: list | None = None,
) -> RunResult:
    """Run one prompt through the normal Agent loop and return aggregated text.

    `agent_out`, when provided, receives the Agent instance so callers can cancel it.
    """
    session = Session(session_id)
    bind_session_tools(session_id)
    await load_session_todos(session_id)

    agent, _config_obj, _resolved_name = build_agent(session, agent_name)
    if agent_out is not None:
        agent_out[:] = [agent]
    result = RunResult(session_id=session_id)
    ended = False

    start_turn(session_id)
    try:
        async for event in agent.run(message, attachments or None):
            if capture_events:
                result.events.append({"type": event.type, **event.data})
            await _emit(sink, event)

            if event.type == "text_delta":
                result.text += event.data.get("content", "")
            elif event.type == "confirm_request":
                request = dict(event.data)
                if confirm_handler is None:
                    approved = False
                else:
                    approved = bool(await _maybe_await(confirm_handler(request)))
                await agent.resolve_confirm(request.get("id", ""), approved)
            elif event.type == "question_request":
                request = dict(event.data)
                if question_handler is None:
                    # Non-interactive default: dismiss. The agent already handles
                    # empty answer as a dismissed question.
                    await agent.resolve_confirm(request.get("id", ""), False, answer="")
                else:
                    answer = await _maybe_await(question_handler(request))
                    if isinstance(answer, (list, dict)):
                        answer = json.dumps(answer)
                    await agent.resolve_confirm(
                        request.get("id", ""),
                        True,
                        answer="" if answer is None else str(answer),
                    )
            elif event.type == "cancelled":
                result.stop_reason = "cancelled"
            elif event.type == "error":
                result.error = event.data.get("message") or result.error
                result.stop_reason = "error"
            elif event.type == "incomplete":
                result.stop_reason = "incomplete"
            elif event.type == "done":
                ended = True

        if not ended:
            result.stop_reason = result.stop_reason or "end_turn"
    finally:
        end_turn(session_id)
        session_tool = server.tools.get("session") if server.tools else None
        if session_tool and getattr(session_tool, "current_session_id", None) == session_id:
            session_tool.current_session_id = None
        if agent_out is not None:
            agent_out[:] = []

    return result
