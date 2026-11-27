"""D3 staged revert / redo / per-message diff regressions."""

import pytest

from codeassist import turn_state
from codeassist.routes.sessions import (
    clear_revert,
    commit_revert,
    get_message_diff,
    get_revert_state,
    redo_session,
    stage_revert,
)
from codeassist.session import Session, init_db


@pytest.mark.asyncio
async def test_stage_clear_commit_redo_round_trip():
    await init_db()
    session = await Session.create(name="d3")
    m1 = await session.add_message("user", "u1")
    await session.add_message("assistant", "a1")
    m3 = await session.add_message("user", "u2")
    await session.add_message("assistant", "a2")

    staged = await session.stage_revert(m3)
    assert staged["staged"] is True
    assert staged["revert"]["message_id"] == m3

    visible = await session.get_messages(include_reverted=False)
    assert [m["id"] for m in visible] == [m1, (await session.get_messages())[1]["id"]]

    full = await session.get_messages(include_reverted=True)
    reverted_ids = [m["id"] for m in full if m.get("reverted")]
    assert m3 in reverted_ids
    assert len(reverted_ids) == 2

    cleared = await session.clear_revert_state()
    assert cleared is True
    visible_after_clear = await session.get_messages(include_reverted=False)
    assert len(visible_after_clear) == 4

    await session.stage_revert(m3)
    committed = await session.commit_revert()
    assert committed["committed"] is True
    assert committed["deleted"] == 2
    assert committed["redo"]["can_redo"] is True

    after_commit = await session.get_messages()
    assert [m["id"] for m in after_commit] == [m1, after_commit[1]["id"]]

    redone = await session.redo()
    assert redone["redone"] is True
    assert redone["restored"] == 2
    after_redo = await session.get_messages()
    assert len(after_redo) == 4
    assert after_redo[2]["id"] == m3


@pytest.mark.asyncio
async def test_new_user_message_commits_staged_revert_and_clears_redo():
    await init_db()
    session = await Session.create(name="d3-branch")
    await session.add_message("user", "u1")
    await session.add_message("assistant", "a1")
    m3 = await session.add_message("user", "u2")
    await session.add_message("assistant", "a2")

    await session.stage_revert(m3)
    # new branch: staged revert committed, redo cleared
    await session.add_message("user", "u3")
    msgs = await session.get_messages()
    assert [m["content"] for m in msgs] == ["u1", "a1", "u3"]
    assert await session.get_revert_state() is None
    assert (await session.get_redo_state())["can_redo"] is False


@pytest.mark.asyncio
async def test_message_diff_endpoint_returns_snapshots():
    await init_db()
    session = await Session.create(name="d3-diff")
    mid = await session.add_message("assistant", "edited")
    from codeassist.knowledge import KnowledgeBase

    await KnowledgeBase.log_file_snapshot(
        session_id=session.id,
        file_path="src/app.py",
        action="write",
        content_preview="print('hi')",
        message_id=mid,
    )
    data = await get_message_diff(session.id, mid)
    assert data["has_diff"] is True
    assert data["files"][0]["file_path"] == "src/app.py"
    assert data["files"][0]["content_preview"] == "print('hi')"


@pytest.mark.asyncio
async def test_revert_routes_guard_mid_turn():
    await init_db()
    session = await Session.create(name="d3-guard")
    mid = await session.add_message("user", "u1")
    turn_state.start_turn(session.id)
    try:
        with pytest.raises(Exception) as excinfo:
            await stage_revert(session.id, mid)
        assert getattr(excinfo.value, "status_code", None) == 409
        with pytest.raises(Exception) as excinfo:
            await clear_revert(session.id)
        assert getattr(excinfo.value, "status_code", None) == 409
        with pytest.raises(Exception) as excinfo:
            await commit_revert(session.id)
        assert getattr(excinfo.value, "status_code", None) == 409
        with pytest.raises(Exception) as excinfo:
            await redo_session(session.id)
        assert getattr(excinfo.value, "status_code", None) == 409
    finally:
        turn_state.end_turn(session.id)

    # idle works
    state = await get_revert_state(session.id)
    assert state["revert"] is None
    assert state["redo"]["can_redo"] is False
