"""Process-wide registry of sessions with an in-flight turn.

``undo`` / ``rollback`` mutate a session's message history. Doing so while a turn
is running would leave the in-flight agent operating on a stale snapshot and can
strand tool calls whose rows were just deleted, so those routes reject the call
with 409 while a turn is active (see :func:`is_session_running`).

Kept in its own module so both ``server.py`` (which registers turns) and the
routes can import it without a circular dependency. asyncio is single-threaded,
so membership checks are atomic with respect to each other; every entry is
removed in ``run_agent_task``'s ``finally``, so the set never grows without bound.
"""

_active_turn_sessions: set[str] = set()


def start_turn(session_id: str) -> None:
    """Mark *session_id* as having a turn in flight."""
    _active_turn_sessions.add(session_id)


def end_turn(session_id: str) -> None:
    """Clear the in-flight marker for *session_id* (safe to call even if it was
    never started -- a no-op discard)."""
    _active_turn_sessions.discard(session_id)


def is_session_running(session_id: str) -> bool:
    """Return True while a turn is running for *session_id*."""
    return session_id in _active_turn_sessions
