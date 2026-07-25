"""Knowledge base API routes."""
from pathlib import Path
from datetime import datetime
import json
import csv
import io
import re
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("")
async def list_knowledge(
    entry_type: str = None,
    scope: str = None,
    scope_identifier: str = None,
    min_confidence: float = 0.0,
    limit: int = 50,
):
    from knowledge import KnowledgeBase
    return await KnowledgeBase.search_knowledge(
        entry_type=entry_type,
        scope=scope,
        scope_identifier=scope_identifier,
        min_confidence=min_confidence,
        limit=limit,
    )


@router.get("/search")
async def search_knowledge(q: str, entry_type: str = None, limit: int = 20):
    from knowledge import KnowledgeBase
    try:
        results = await KnowledgeBase.fulltext_search_knowledge(q, entry_type=entry_type, limit=limit)
        if results:
            return results
    except Exception:
        pass
    return await KnowledgeBase.search_knowledge(
        entry_type=entry_type,
        min_confidence=0.0,
        limit=limit,
    )


@router.post("")
async def create_knowledge(body: dict):
    from knowledge import KnowledgeBase
    entry_type = body.get("entry_type")
    scope = body.get("scope")
    content = body.get("content")

    if not all([entry_type, scope, content]):
        raise HTTPException(status_code=400, detail="entry_type, scope, and content are required")

    entry_id = await KnowledgeBase.create_knowledge_entry(
        entry_type=entry_type,
        scope=scope,
        content=content,
        scope_identifier=body.get("scope_identifier"),
        source_session_id=body.get("source_session_id"),
        confidence=body.get("confidence", 1.0),
        tags=body.get("tags"),
        metadata=body.get("metadata"),
    )
    return {"id": entry_id}


@router.get("/{entry_id}")
async def get_knowledge(entry_id: str):
    from knowledge import KnowledgeBase
    entry = await KnowledgeBase.get_knowledge_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return entry


@router.put("/{entry_id}")
async def update_knowledge(entry_id: str, body: dict):
    from knowledge import KnowledgeBase
    updated = await KnowledgeBase.update_knowledge_entry(entry_id, **body)
    if not updated:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return {"ok": True}


@router.delete("/{entry_id}")
async def delete_knowledge(entry_id: str):
    from knowledge import KnowledgeBase
    deleted = await KnowledgeBase.delete_knowledge_entry(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return {"ok": True}


@router.get("/semantic")
async def semantic_search(q: str, entry_type: str = None, limit: int = 10):
    from embeddings import get_embedding_manager
    manager = get_embedding_manager()
    return await manager.search_by_embedding(q, limit=limit, entry_type=entry_type)


@router.get("/{entry_id}/similar")
async def find_similar_knowledge(entry_id: str, limit: int = 5):
    from embeddings import get_embedding_manager
    manager = get_embedding_manager()
    return await manager.search_by_entry_embedding(entry_id, limit=limit)


@router.post("/embeddings/generate")
async def generate_embeddings(batch_size: int = 10):
    from embeddings import get_embedding_manager
    manager = get_embedding_manager()
    count = await manager.generate_embeddings_for_all(batch_size=batch_size)
    return {"generated": count}


# ── File History ────────────────────────────────────────────────

@router.get("/files/history")
async def file_history(file_path: str, limit: int = 50):
    from knowledge import KnowledgeBase
    return await KnowledgeBase.get_file_history(file_path, limit=limit)


# ── Auto-Creation Status ────────────────────────────────────────

@router.get("/auto-creation/status")
async def auto_creation_status():
    from config import load_config
    from knowledge import KnowledgeBase

    config = load_config()

    recent_skills = await KnowledgeBase.search_knowledge(
        entry_type="skill_created",
        scope="project",
        min_confidence=0.5,
        limit=10,
    )

    recent_tools = await KnowledgeBase.search_knowledge(
        entry_type="tool_created",
        scope="project",
        min_confidence=0.5,
        limit=10,
    )

    patterns = await KnowledgeBase.search_knowledge(
        entry_type="pattern",
        tags=["repetitive_workflow"],
        min_confidence=0.5,
        limit=10,
    )

    return {
        "enabled": {
            "skills": config.agent.auto_create_skills,
            "tools": config.agent.auto_create_tools,
        },
        "limits": {
            "max_per_session": config.agent.max_auto_creations,
            "min_confidence": config.agent.min_confidence,
        },
        "recent_skills": recent_skills,
        "recent_tools": recent_tools,
        "detected_patterns": patterns,
    }
