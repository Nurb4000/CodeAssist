"""Knowledge Base GUI dashboard API routes."""
from datetime import datetime
import json
import logging
import csv
import io
import re
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kb", tags=["kb_gui"])


@router.get("/stats")
async def kb_stats():
    """Get knowledge base statistics (entry counts, session counts, quality scores)."""
    from codeassist.knowledge import KnowledgeBase
    from codeassist.session import get_db

    entry_types = ["pattern", "convention", "decision", "bug_fix", "optimization",
                   "skill_created", "tool_created", "effectiveness"]

    stats = {}
    for entry_type in entry_types:
        entries = await KnowledgeBase.search_knowledge(entry_type=entry_type, limit=1000)
        stats[entry_type] = len(entries)

    all_entries = await KnowledgeBase.search_knowledge(limit=10000)
    stats["total"] = len(all_entries)

    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM sessions")
        stats["total_sessions"] = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM tool_executions")
        stats["total_tool_calls"] = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM llm_usage")
        stats["total_llm_calls"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM knowledge_entries WHERE datetime(created_at) >= datetime('now', '-7 days')"
        )
        stats["recent_entries_7d"] = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT AVG(quality_score) FROM session_summaries WHERE quality_score IS NOT NULL"
        )
        avg_quality = (await cursor.fetchone())[0]
        stats["avg_quality_score"] = round(avg_quality, 2) if avg_quality else 0

        cursor = await db.execute(
            """SELECT
                    SUM(CASE WHEN usage_count = 0 THEN 1 ELSE 0 END) as unused,
                    SUM(CASE WHEN usage_count BETWEEN 1 AND 2 THEN 1 ELSE 0 END) as low_use,
                    SUM(CASE WHEN usage_count >= 3 THEN 1 ELSE 0 END) as high_use
                FROM knowledge_entries"""
        )
        row = await cursor.fetchone()
        stats["usage_distribution"] = {
            "unused": row["unused"] or 0,
            "low_use": row["low_use"] or 0,
            "high_use": row["high_use"] or 0,
        }

    # Lifecycle breakdown (F3)
    status_counts = await KnowledgeBase.entry_status_counts()
    stats["status_breakdown"] = status_counts
    stats["entries_in_review"] = status_counts.get("review", 0)
    stats["entries_flagged"] = status_counts.get("flagged", 0)
    stats["entries_archived"] = status_counts.get("archived", 0)
    stats["orphan_entries"] = await KnowledgeBase.orphan_entry_count()

    # Frequently-used active entries (F2): candidates for promotion, computed
    # read-only so hitting /stats never archives anything.
    stats["high_usage_count"] = len(
        await KnowledgeBase.high_usage_entries(min_usage=3)
    )

    return stats


@router.get("/entries")
async def kb_list_entries(
    entry_type: str | None = None,
    scope: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    status: str = "active",
    limit: int = 50,
    offset: int = 0,
):
    """List knowledge entries with optional filtering by type, scope, tag, search, or lifecycle status.

    ``status`` accepts a single lifecycle value (``active``/``review``/``flagged``/``archived``)
    or ``all`` to include every state. The triage UI lists one concrete bucket at a time so
    full-text search stays meaningful within it; ``all`` skips FTS and filters structurally.
    """
    from codeassist.knowledge import KnowledgeBase

    include_all = status == "all"
    if search and not include_all:
        entries = await KnowledgeBase.fulltext_search_knowledge(
            search, entry_type=entry_type, limit=limit + offset, status=status
        )
    else:
        tags = [tag] if tag else None
        entries = await KnowledgeBase.search_knowledge(
            entry_type=entry_type,
            scope=scope,
            tags=tags,
            limit=limit + offset,
            status=None if include_all else status,
        )

    entries = entries[offset:offset + limit]

    return {"entries": entries, "count": len(entries)}


@router.get("/orphans")
async def kb_list_orphans(limit: int = 200, offset: int = 0):
    """List knowledge entries whose source session was deleted (triage candidates)."""
    from codeassist.knowledge import KnowledgeBase

    entries = await KnowledgeBase.list_orphan_entries(limit=limit, offset=offset)
    return {"entries": entries, "count": len(entries)}


@router.get("/high-usage")
async def kb_high_usage(limit: int = 20):
    """List active entries with the highest usage counts (F2 promotion candidates)."""
    from codeassist.knowledge import KnowledgeBase

    entries = await KnowledgeBase.high_usage_entries(min_usage=1, limit=limit)
    return {"entries": entries, "count": len(entries)}


@router.get("/entries/{entry_id}")
async def kb_get_entry(entry_id: str):
    """Get a single knowledge entry by ID."""
    from codeassist.knowledge import KnowledgeBase
    entry = await KnowledgeBase.get_knowledge_entry(entry_id)
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Entry not found"})

    # Record the view as usage so usage_count is a live signal for the
    # quality/retention pass and future promotion logic (F2). Best-effort: a
    # failed increment must never break viewing an entry.
    try:
        await KnowledgeBase.increment_usage(entry_id)
    except Exception as e:
        log.debug("Failed to record entry usage for %s: %s", entry_id, e)

    return entry


@router.put("/entries/{entry_id}")
async def kb_update_entry(entry_id: str, body: dict):
    """Update a knowledge entry. Supported fields: content, confidence, tags, metadata."""
    from codeassist.knowledge import KnowledgeBase

    updates = {}
    for field in ["content", "confidence", "tags", "metadata"]:
        if field in body:
            updates[field] = body[field]

    if not updates:
        return JSONResponse(status_code=400, content={"error": "No valid fields to update"})

    success = await KnowledgeBase.update_knowledge_entry(entry_id, **updates)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Entry not found or no changes"})

    return {"message": "Entry updated", "entry_id": entry_id}


@router.delete("/entries/{entry_id}")
async def kb_delete_entry(entry_id: str):
    """Delete a single knowledge entry by ID."""
    from codeassist.knowledge import KnowledgeBase
    success = await KnowledgeBase.delete_knowledge_entry(entry_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Entry not found"})
    return {"message": "Entry deleted", "entry_id": entry_id}


@router.post("/entries/{entry_id}/status")
async def kb_set_entry_status(entry_id: str, body: dict):
    """Transition an entry's lifecycle status (review / flagged / archived)."""
    from codeassist.knowledge import KnowledgeBase

    status = body.get("status")
    if not status:
        return JSONResponse(status_code=400, content={"error": "status required"})

    success = await KnowledgeBase.set_entry_status(entry_id, status)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Entry not found"})
    return {"message": f"Entry set to {status}", "entry_id": entry_id, "status": status}


@router.post("/entries/bulk-delete")
async def kb_bulk_delete(body: dict):
    """Delete multiple knowledge entries by ID list."""
    from codeassist.knowledge import KnowledgeBase

    entry_ids = body.get("entry_ids", [])
    if not entry_ids:
        return JSONResponse(status_code=400, content={"error": "No entry IDs provided"})

    deleted = 0
    for entry_id in entry_ids:
        if await KnowledgeBase.delete_knowledge_entry(entry_id):
            deleted += 1

    return {"message": f"Deleted {deleted} entries", "deleted": deleted}


@router.post("/entries")
async def kb_create_entry(body: dict):
    """Create a new knowledge entry. Required fields: entry_type, scope, content."""
    from codeassist.knowledge import KnowledgeBase

    required = ["entry_type", "scope", "content"]
    for field in required:
        if field not in body:
            return JSONResponse(status_code=400, content={"error": f"Missing required field: {field}"})

    entry_id = await KnowledgeBase.create_knowledge_entry(
        entry_type=body["entry_type"],
        scope=body["scope"],
        content=body["content"],
        scope_identifier=body.get("scope_identifier"),
        confidence=body.get("confidence", 1.0),
        tags=body.get("tags"),
        metadata=body.get("metadata"),
    )

    return {"message": "Entry created", "entry_id": entry_id}


@router.get("/search")
async def kb_search(
    q: str,
    entry_type: str | None = None,
    semantic: bool = False,
    limit: int = 20,
):
    """Search knowledge entries via full-text search or semantic (embedding) search."""
    from codeassist.knowledge import KnowledgeBase

    if semantic:
        try:
            from codeassist.embeddings import get_embedding_manager
            manager = get_embedding_manager()
            # search_by_embedding silently falls back to text search when no
            # embedding model is configured or the query cannot be embedded.
            # Report the path that actually ran so clients aren't misled (F4).
            if manager._get_client() is not None:
                results = await manager.search_by_embedding(q, limit=limit)
                used_vector = any("similarity" in r for r in results) or not results
                if used_vector:
                    return {"results": results, "type": "semantic"}
            results = await KnowledgeBase.fulltext_search_knowledge(
                q, entry_type=entry_type, limit=limit
            )
            return {"results": results, "type": "text"}
        except Exception:
            pass

    results = await KnowledgeBase.fulltext_search_knowledge(q, entry_type=entry_type, limit=limit)
    return {"results": results, "type": "text"}


@router.get("/sessions")
async def kb_list_sessions(limit: int = 50, offset: int = 0):
    """List sessions with their summaries, ordered by most recently updated."""
    from codeassist.session import get_db

    async with get_db() as db:
        cursor = await db.execute(
            """SELECT s.*, ss.summary, ss.quality_score, ss.key_topics, ss.tools_used
               FROM sessions s
               LEFT JOIN session_summaries ss ON s.id = ss.session_id
               ORDER BY s.updated_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        sessions = [dict(row) for row in await cursor.fetchall()]

    return {"sessions": sessions, "count": len(sessions)}


@router.get("/sessions/{session_id}")
async def kb_get_session(session_id: str):
    """Get full session details including messages, summary, tags, and tool executions."""
    from codeassist.session import get_db, Session
    from codeassist.knowledge import KnowledgeBase

    session = Session(session_id)
    messages = await session.get_messages()

    summary = await KnowledgeBase.get_session_summary(session_id)
    tags = await KnowledgeBase.get_session_tags(session_id)

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM tool_executions WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        tool_calls = [dict(row) for row in await cursor.fetchall()]

    return {
        "session_id": session_id,
        "messages": messages[:100],
        "summary": summary,
        "tags": tags,
        "tool_calls": tool_calls,
    }


@router.get("/analytics/tools")
async def kb_analytics_tools(
    session_id: str = None,
    tool_name: str = None,
    period_days: int = 30,
):
    """Get tool usage analytics for the specified period."""
    from codeassist.knowledge import KnowledgeBase
    return await KnowledgeBase.get_tool_stats(
        session_id=session_id,
        tool_name=tool_name,
        period_days=period_days,
    )


@router.get("/analytics/llm")
async def kb_analytics_llm(
    session_id: str = None,
    model: str = None,
    period_days: int = 30,
):
    """Get LLM usage analytics (token counts, costs) for the specified period."""
    from codeassist.knowledge import KnowledgeBase
    return await KnowledgeBase.get_llm_stats(
        session_id=session_id,
        model=model,
        period_days=period_days,
    )


# ── PII Manager ────────────────────────────────────────────────

@router.get("/pii/scan")
async def kb_pii_scan():
    """Scan all knowledge entries for personally identifiable information (PII)."""
    from codeassist.knowledge import KnowledgeBase

    pii_patterns = {
        "email": r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        "ip_address": r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
        "phone": r'\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b',
        "ssn": r'\b\d{3}[-]?\d{2}[-]?\d{4}\b',
        "credit_card": r'\b(?:\d{4}[-]?){3}\d{4}\b',
        "api_key": r'(?i)(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?key)[=:]\s*["\']?[A-Za-z0-9]{20,}',
    }

    entries = await KnowledgeBase.search_knowledge(limit=10000)
    flagged = []
    # entry_id -> set of PII types found, so we can flag the entry once and
    # record every category detected (rather than overwriting per-type).
    findings: dict[str, set[str]] = {}

    for entry in entries:
        content = entry.get("content", "") or ""
        entry_id = entry.get("id", "")

        for pii_type, pattern in pii_patterns.items():
            matches = re.findall(pattern, content)
            if matches:
                flagged.append({
                    "entry_id": entry_id,
                    "entry_type": entry.get("entry_type"),
                    "pii_type": pii_type,
                    "matches": matches[:5],
                    "content_preview": content[:200],
                })
                findings.setdefault(entry_id, set()).add(pii_type)

    # Close the loop between PII detection and the lifecycle state machine (F3):
    # active entries containing PII/secrets become 'flagged', so they show up in
    # the stats bar and are protected from automatic quality-pass archival.
    if findings:
        await KnowledgeBase.flag_entries_for_pii(findings)

    return {"flagged": flagged, "total_scanned": len(entries)}


@router.post("/pii/redact")
async def kb_pii_redact(body: dict):
    """Redact PII from a knowledge entry. Optionally specify a pii_type to redact only that type."""
    from codeassist.knowledge import KnowledgeBase

    entry_id = body.get("entry_id")
    pii_type = body.get("pii_type")

    if not entry_id:
        return JSONResponse(status_code=400, content={"error": "entry_id required"})

    entry = await KnowledgeBase.get_knowledge_entry(entry_id)
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Entry not found"})

    content = entry.get("content", "")

    redaction_map = {
        "email": r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        "ip_address": r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
        "phone": r'\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b',
        "ssn": r'\b\d{3}[-]?\d{2}[-]?\d{4}\b',
        "credit_card": r'\b(?:\d{4}[-]?){3}\d{4}\b',
        "api_key": r'(?i)(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?key)[=:]\s*["\']?[A-Za-z0-9]{20,}',
    }

    if pii_type and pii_type in redaction_map:
        redacted_content = re.sub(redaction_map[pii_type], f'[REDACTED {pii_type.upper()}]', content)
        await KnowledgeBase.update_knowledge_entry(entry_id, content=redacted_content)
        return {"message": f"Redacted {pii_type}", "entry_id": entry_id}
    else:
        redacted_content = content
        for ptype, pattern in redaction_map.items():
            redacted_content = re.sub(pattern, f'[REDACTED {ptype.upper()}]', redacted_content)

        await KnowledgeBase.update_knowledge_entry(entry_id, content=redacted_content)
        return {"message": "All PII redacted", "entry_id": entry_id}


# ── Quality / Retention ─────────────────────────────────────────

@router.post("/quality-pass")
async def kb_quality_pass(body: dict | None = None):
    """Run the quality/retention pass on demand and return its report (G1).

    Soft-deletes (archives) active entries that are below ``min_confidence`` and
    used ``max_usage`` times or fewer, and returns the archived candidates plus
    promotion candidates for the review UI. Params are optional and default to
    the values ``run_quality_pass`` uses; pass an empty body for defaults.
    """
    from codeassist.knowledge import KnowledgeBase

    body = body or {}
    report = await KnowledgeBase.run_quality_pass(
        min_confidence=body.get("min_confidence", 0.5),
        max_usage=body.get("max_usage", 0),
        promote_after=body.get("promote_after", 3),
    )
    return report


# ── Settings ────────────────────────────────────────────────────

@router.get("/settings")
async def kb_get_settings():
    """Get auto-creation settings for the knowledge base."""
    from ..server import get_config
    config = get_config()

    return {
        "auto_create_skills": config.agent.auto_create_skills,
        "auto_create_tools": config.agent.auto_create_tools,
        "max_auto_creations": config.agent.max_auto_creations,
        "min_confidence": config.agent.min_confidence,
    }


@router.put("/settings")
async def kb_update_settings(body: dict):
    """Update auto-creation settings at runtime. Body fields: auto_create_skills, auto_create_tools, max_auto_creations, min_confidence."""
    from ..server import get_config

    config = get_config()

    if "auto_create_skills" in body:
        config.agent.auto_create_skills = body["auto_create_skills"]
    if "auto_create_tools" in body:
        config.agent.auto_create_tools = body["auto_create_tools"]
    if "max_auto_creations" in body:
        config.agent.max_auto_creations = body["max_auto_creations"]
    if "min_confidence" in body:
        config.agent.min_confidence = body["min_confidence"]

    return {"message": "Settings updated (runtime only)", "settings": await kb_get_settings()}


# ── Export / Import / Clear ─────────────────────────────────────

@router.post("/export")
async def kb_export(body: dict | None = None):
    """Export knowledge base entries as JSON or CSV."""
    from codeassist.knowledge import KnowledgeBase

    format_type = body.get("format", "json") if body else "json"

    entries = await KnowledgeBase.search_knowledge(limit=100000)

    if format_type == "csv":
        output = io.StringIO()
        if entries:
            writer = csv.DictWriter(output, fieldnames=entries[0].keys())
            writer.writeheader()
            writer.writerows(entries)

        return {
            "format": "csv",
            "data": output.getvalue(),
            "count": len(entries),
            "filename": f"codeassist_kb_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        }
    else:
        return {
            "format": "json",
            "data": json.dumps(entries, indent=2, default=str),
            "count": len(entries),
            "filename": f"codeassist_kb_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        }


@router.post("/import")
async def kb_import(body: dict):
    """Import knowledge base entries from a list of entry objects."""
    from codeassist.knowledge import KnowledgeBase

    data = body.get("data", [])
    if not data:
        return JSONResponse(status_code=400, content={"error": "No data provided"})

    imported = 0
    for entry in data:
        try:
            await KnowledgeBase.create_knowledge_entry(
                entry_type=entry.get("entry_type", "pattern"),
                scope=entry.get("scope", "project"),
                content=entry.get("content", ""),
                scope_identifier=entry.get("scope_identifier"),
                confidence=entry.get("confidence", 1.0),
                tags=entry.get("tags"),
                metadata=entry.get("metadata"),
            )
            imported += 1
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to import entry: %s", e)

    return {"message": f"Imported {imported} entries", "imported": imported}


@router.delete("/clear")
async def kb_clear(body: dict):
    """Clear all knowledge base entries. Body must contain confirm=true."""
    from codeassist.session import get_db

    confirm = body.get("confirm", False)
    if not confirm:
        return JSONResponse(status_code=400, content={"error": "Set confirm=true to clear all KB entries"})

    async with get_db() as db:
        await db.execute("DELETE FROM session_tags")
        await db.execute("DELETE FROM file_snapshots")
        await db.execute("DELETE FROM tool_executions")
        await db.execute("DELETE FROM llm_usage")
        await db.execute("DELETE FROM knowledge_entries")
        await db.execute("DELETE FROM session_summaries")
        await db.commit()

    return {"message": "All KB entries cleared"}
