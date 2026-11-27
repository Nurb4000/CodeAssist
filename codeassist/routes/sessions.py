"""Session-related API routes (REST endpoints only)."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

MAX_SESSION_NAME_LEN = 200
MAX_MESSAGE_LEN = 100_000
MAX_SEARCH_QUERY_LEN = 500
MAX_SEARCH_LIMIT = 200


@router.get("")
async def list_sessions():
    """List all sessions ordered by most recently updated."""
    from codeassist.session import Session
    return await Session.list_all()


@router.post("")
async def create_session():
    """Create a new empty session."""
    from codeassist.session import Session
    session = await Session.create()
    return {"id": session.id}


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and all its messages."""
    from codeassist.session import Session
    session = Session(session_id)
    await session.delete()
    return {"ok": True}


@router.patch("/{session_id}")
async def update_session(session_id: str, body: dict):
    """Update a session. Body may contain 'name' (rename) and/or 'pinned' (bool)."""
    from codeassist.session import Session

    session = Session(session_id)
    if "name" in body:
        name = body.get("name", "Untitled")
        if not isinstance(name, str) or len(name) > MAX_SESSION_NAME_LEN:
            raise HTTPException(status_code=400, detail=f"Session name must be <= {MAX_SESSION_NAME_LEN} characters")
        await session.rename(name)
    if "pinned" in body:
        await Session.set_pinned(session_id, bool(body.get("pinned")))
    return {"ok": True}


@router.get("/{session_id}/messages")
async def get_messages(session_id: str, include_reverted: bool = True):
    """Get all messages for a session in chronological order."""
    from codeassist.session import Session
    session = Session(session_id)
    return await session.get_messages(include_reverted=include_reverted)


@router.get("/{session_id}/messages/search")
async def search_messages(
    session_id: str,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """Search messages in one session. Read-only, so no mid-turn guard."""
    query = q.strip()
    if not query or len(query) > MAX_SEARCH_QUERY_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"q must be 1-{MAX_SEARCH_QUERY_LEN} characters",
        )
    if limit < 1 or limit > MAX_SEARCH_LIMIT or offset < 0:
        raise HTTPException(
            status_code=400,
            detail=f"limit must be 1-{MAX_SEARCH_LIMIT} and offset >= 0",
        )
    from codeassist.knowledge import KnowledgeBase
    return await KnowledgeBase.fulltext_search_messages(
        session_id, query, limit=limit, offset=offset
    )


@router.get("/{session_id}/messages/{message_id}/diff")
async def get_message_diff(session_id: str, message_id: str):
    """Return per-message diff/snapshot metadata for timeline UI."""
    from codeassist.knowledge import KnowledgeBase
    from codeassist.session import Session

    session = Session(session_id)
    messages = await session.get_messages(include_reverted=True)
    target = next((m for m in messages if m["id"] == message_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Message not found")

    snapshots = await KnowledgeBase.get_session_file_snapshots(session_id, message_id=message_id)
    files = []
    for snap in snapshots:
        files.append({
            "id": snap.get("id"),
            "file_path": snap.get("file_path"),
            "action": snap.get("action"),
            "content_hash": snap.get("content_hash"),
            "content_preview": snap.get("content_preview"),
            "size_bytes": snap.get("size_bytes"),
            "created_at": snap.get("created_at"),
        })
    return {
        "message_id": message_id,
        "session_id": session_id,
        "reverted": bool(target.get("reverted")),
        "files": files,
        "has_diff": len(files) > 0,
    }


@router.post("/{session_id}/fork")
async def fork_session(session_id: str, body: dict):
    """Create a fork of an existing session. Body may contain an optional 'name'."""
    from codeassist.session import Session
    name = body.get("name")
    new_session = await Session(session_id).fork(name)
    return {"id": new_session.id}


@router.post("/{session_id}/rollback/{message_id}")
async def rollback_session(session_id: str, message_id: str):
    """Delete all messages after the given message ID (rollback to that point)."""
    from codeassist import turn_state

    if turn_state.is_session_running(session_id):
        # Deleting rows mid-turn strands tool calls and leaves the in-flight
        # agent on a stale snapshot; reject until the turn finishes.
        raise HTTPException(
            status_code=409,
            detail="A turn is already running for this session; wait for it to finish.",
        )
    from codeassist.session import Session

    session = Session(session_id)
    deleted = await session.delete_messages_after(message_id)
    return {"deleted": deleted}


@router.post("/{session_id}/undo")
async def undo_session(session_id: str):
    """Undo the last agent turn (removes assistant message and its tool results)."""
    from codeassist import turn_state

    if turn_state.is_session_running(session_id):
        raise HTTPException(
            status_code=409,
            detail="A turn is already running for this session; wait for it to finish.",
        )
    from codeassist.session import Session

    session = Session(session_id)
    deleted = await session.undo_last_turn()
    return {"deleted": deleted}


@router.get("/{session_id}/revert")
async def get_revert_state(session_id: str):
    """Get staged revert boundary and redo availability."""
    from codeassist.session import Session

    session = Session(session_id)
    return {
        "revert": await session.get_revert_state(),
        "redo": await session.get_redo_state(),
    }


@router.post("/{session_id}/revert/stage/{message_id}")
async def stage_revert(session_id: str, message_id: str):
    """Stage a non-destructive revert boundary at the given message."""
    from codeassist import turn_state

    if turn_state.is_session_running(session_id):
        raise HTTPException(status_code=409, detail="A turn is already running for this session; wait for it to finish.")
    from codeassist.session import Session

    session = Session(session_id)
    try:
        return await session.stage_revert(message_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{session_id}/revert/clear")
async def clear_revert(session_id: str):
    """Clear staged revert boundary without deleting messages."""
    from codeassist import turn_state

    if turn_state.is_session_running(session_id):
        raise HTTPException(status_code=409, detail="A turn is already running for this session; wait for it to finish.")
    from codeassist.session import Session

    session = Session(session_id)
    cleared = await session.clear_revert_state()
    return {"cleared": cleared, "revert": await session.get_revert_state(), "redo": await session.get_redo_state()}


@router.post("/{session_id}/revert/commit")
async def commit_revert(session_id: str):
    """Commit staged revert: delete reverted rows and push to redo stack."""
    from codeassist import turn_state

    if turn_state.is_session_running(session_id):
        raise HTTPException(status_code=409, detail="A turn is already running for this session; wait for it to finish.")
    from codeassist.session import Session

    session = Session(session_id)
    return await session.commit_revert()


@router.post("/{session_id}/redo")
async def redo_session(session_id: str):
    """Redo the last committed revert."""
    from codeassist import turn_state

    if turn_state.is_session_running(session_id):
        raise HTTPException(status_code=409, detail="A turn is already running for this session; wait for it to finish.")
    from codeassist.session import Session

    session = Session(session_id)
    return await session.redo()


@router.delete("/{session_id}/messages/{message_id}")
async def delete_message(session_id: str, message_id: str):
    """Delete a specific message and all messages after it."""
    from codeassist.session import Session
    session = Session(session_id)
    deleted = await session.delete_messages_after(message_id)
    return {"deleted": deleted}


@router.get("/{session_id}/summary")
async def session_summary(session_id: str):
    """Get the AI-generated summary for a session."""
    from codeassist.session_manager import SessionManager
    summary = await SessionManager.get_session_summary(session_id)
    return summary


@router.post("/export")
async def export_session(body: dict):
    """Export a session's data. Body must contain 'session_id' and optional 'redact' (boolean)."""
    from codeassist.session_manager import SessionManager
    session_id = body.get("session_id")
    redact = bool(body.get("redact", False))
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    data = await SessionManager.export_session(session_id, redact)
    return data


@router.post("/import")
async def import_session(body: dict):
    """Import a session from exported data. Body must contain 'data' (JSON string or object)."""
    from codeassist.session_manager import SessionManager
    data = body.get("data")
    name = body.get("name")
    if not data:
        raise HTTPException(status_code=400, detail="data is required")
    import json
    try:
        export_data = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON data")
    try:
        new_session = await SessionManager.import_session(export_data, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": new_session.id}


# ── Session Tags ────────────────────────────────────────────────

@router.get("/search/tags")
async def search_sessions_by_tags(tags: str, match_all: bool = False, limit: int = 50):
    """Search sessions by tags. 'tags' is a comma-separated string."""
    from codeassist.knowledge import KnowledgeBase
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        raise HTTPException(status_code=400, detail="tags parameter is required")
    return await KnowledgeBase.search_sessions_by_tags(tag_list, match_all=match_all, limit=limit)


@router.get("/{session_id}/tags")
async def get_session_tags(session_id: str):
    """Get all tags for a session."""
    from codeassist.knowledge import KnowledgeBase
    return await KnowledgeBase.get_session_tags(session_id)


@router.post("/{session_id}/tags")
async def add_session_tag(session_id: str, body: dict):
    """Add a tag to a session. Body must contain 'tag' and optional 'source'."""
    from codeassist.knowledge import KnowledgeBase
    tag = body.get("tag")
    if not tag:
        raise HTTPException(status_code=400, detail="tag is required")

    tag_id = await KnowledgeBase.add_session_tag(
        session_id=session_id,
        tag=tag,
        source=body.get("source", "user"),
    )
    return {"id": tag_id}
