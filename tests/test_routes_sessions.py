"""Tests for the undo/rollback session routes' mid-turn guard (§1).

Deleting message history while a turn is running strands tool calls and leaves
the in-flight agent operating on a stale snapshot, so those routes reject with
409 while a turn is active. server.run_agent_task() registers the session id via
turn_state at turn start and clears it in its finally, so an idle session is
never blocked.
"""

import pytest
from fastapi import HTTPException

from codeassist import turn_state
from codeassist.routes.sessions import rollback_session, undo_session
from codeassist.session import Session, init_db


@pytest.mark.asyncio
async def test_undo_rejected_while_turn_running():
    await init_db()
    session = await Session.create(name="guard-undo")
    turn_state.start_turn(session.id)
    try:
        with pytest.raises(HTTPException) as excinfo:
            await undo_session(session.id)
        assert excinfo.value.status_code == 409
    finally:
        turn_state.end_turn(session.id)


@pytest.mark.asyncio
async def test_undo_allowed_when_idle():
    await init_db()
    session = await Session.create(name="undo-idle")
    # Nothing to undo, but an idle session must not be rejected.
    assert await undo_session(session.id) == {"deleted": 0}


@pytest.mark.asyncio
async def test_rollback_rejected_while_turn_running():
    await init_db()
    session = await Session.create(name="guard-rollback")
    turn_state.start_turn(session.id)
    try:
        with pytest.raises(HTTPException) as excinfo:
            await rollback_session(session.id, "0" * 32)
        assert excinfo.value.status_code == 409
    finally:
        turn_state.end_turn(session.id)


@pytest.mark.asyncio
async def test_rollback_allowed_when_idle():
    await init_db()
    session = await Session.create(name="rollback-idle")
    # Unknown message id deletes nothing, but must not be rejected while idle.
    assert await rollback_session(session.id, "0" * 32) == {"deleted": 0}


def test_end_turn_is_idempotent_when_never_started():
    """A finally that ends a turn which was never registered (e.g. a crash before
    start_turn) must not raise -- discard is a no-op for missing ids."""
    sid = "never-registered"
    assert not turn_state.is_session_running(sid)
    turn_state.end_turn(sid)  # must not raise, session never started


def test_is_session_running_reflects_registration():
    sid = "reg-test-session"
    assert not turn_state.is_session_running(sid)
    turn_state.start_turn(sid)
    assert turn_state.is_session_running(sid)
    turn_state.end_turn(sid)
    assert not turn_state.is_session_running(sid)
