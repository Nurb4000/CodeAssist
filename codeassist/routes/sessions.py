"""Session-related API routes (REST endpoints only)."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

MAX_SESSION_NAME_LEN = 200
MAX_MESSAGE_LEN = 100_000


@router.get("")
async def list_sessions():
    from session import Session
    return await Session.list_all()


@router.post("")
async def create_session():
    from session import Session
    session = await Session.create()
    return {"id": session.id}


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    from session import Session
    session = Session(session_id)
    await session.delete()
    return {"ok": True}


@router.patch("/{session_id}")
async def rename_session(session_id: str, body: dict):
    name = body.get("name", "Untitled")
    if not isinstance(name, str) or len(name) > MAX_SESSION_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"Session name must be <= {MAX_SESSION_NAME_LEN} characters")
    from session import Session
    session = Session(session_id)
    await session.rename(name)
    return {"ok": True}


@router.get("/{session_id}/messages")
async def get_messages(session_id: str):
    from session import Session
    session = Session(session_id)
    return await session.get_messages()


@router.post("/{session_id}/fork")
async def fork_session(session_id: str, body: dict):
    from session import Session
    name = body.get("name")
    new_session = await Session.fork_session(session_id, name)
    return {"id": new_session.id}


@router.post("/{session_id}/rollback/{message_id}")
async def rollback_session(session_id: str, message_id: str):
    from session import Session
    session = Session(session_id)
    deleted = await session.delete_messages_after(message_id)
    return {"deleted": deleted}


@router.post("/{session_id}/undo")
async def undo_session(session_id: str):
    from session import Session
    session = Session(session_id)
    deleted = await session.undo_last_turn()
    return {"deleted": deleted}


@router.delete("/{session_id}/messages/{message_id}")
async def delete_message(session_id: str, message_id: str):
    from session import Session
    session = Session(session_id)
    deleted = await session.delete_messages_after(message_id)
    return {"deleted": deleted}


@router.get("/{session_id}/summary")
async def session_summary(session_id: str):
    from session_manager import SessionManager
    summary = await SessionManager.get_session_summary(session_id)
    return summary


@router.post("/export")
async def export_session(body: dict):
    from session_manager import SessionManager
    session_id = body.get("session_id")
    redact = body.get("redact", False)
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    data = await SessionManager.export_session(session_id, redact)
    return data


@router.post("/import")
async def import_session(body: dict):
    from session_manager import SessionManager
    data = body.get("data")
    name = body.get("name")
    if not data:
        raise HTTPException(status_code=400, detail="data is required")
    import json
    try:
        export_data = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON data")
    new_session = await SessionManager.import_session(export_data, name)
    return {"id": new_session.id}


# ── Session Tags ────────────────────────────────────────────────

@router.get("/search/tags")
async def search_sessions_by_tags(tags: str, match_all: bool = False, limit: int = 50):
    from knowledge import KnowledgeBase
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        raise HTTPException(status_code=400, detail="tags parameter is required")
    return await KnowledgeBase.search_sessions_by_tags(tag_list, match_all=match_all, limit=limit)


@router.get("/{session_id}/tags")
async def get_session_tags(session_id: str):
    from knowledge import KnowledgeBase
    return await KnowledgeBase.get_session_tags(session_id)


@router.post("/{session_id}/tags")
async def add_session_tag(session_id: str, body: dict):
    from knowledge import KnowledgeBase
    tag = body.get("tag")
    if not tag:
        raise HTTPException(status_code=400, detail="tag is required")

    tag_id = await KnowledgeBase.add_session_tag(
        session_id=session_id,
        tag=tag,
        source=body.get("source", "user"),
    )
    return {"id": tag_id}
