"""
CodeAssist Knowledge Base - CRUD operations for Phase 1 knowledge base.

This module provides:
- Session summary generation and storage
- Knowledge entry management
- Search functionality via FTS5
- Tool execution logging
- LLM usage tracking
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from session import get_db

log = logging.getLogger(__name__)


class KnowledgeBase:
    """Manages knowledge base operations for CodeAssist."""

    # ── Session Summaries ──────────────────────────────────────────────

    @staticmethod
    async def create_session_summary(
        session_id: str,
        summary: str,
        key_topics: list[str] | None = None,
        goals_achieved: list[str] | None = None,
        tools_used: list[str] | None = None,
        files_modified: list[str] | None = None,
        duration_seconds: int | None = None,
        message_count: int | None = None,
        token_usage: int | None = None,
        model: str | None = None,
        quality_score: float | None = None,
    ) -> str:
        """Create a session summary entry."""
        summary_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with get_db() as db:
            await db.execute(
                """INSERT OR REPLACE INTO session_summaries 
                   (id, session_id, summary, key_topics, goals_achieved, tools_used, 
                    files_modified, duration_seconds, message_count, token_usage, 
                    model, quality_score, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    summary_id,
                    session_id,
                    summary,
                    json.dumps(key_topics) if key_topics else None,
                    json.dumps(goals_achieved) if goals_achieved else None,
                    json.dumps(tools_used) if tools_used else None,
                    json.dumps(files_modified) if files_modified else None,
                    duration_seconds,
                    message_count,
                    token_usage,
                    model,
                    quality_score,
                    now,
                    now,
                ),
            )
            await db.commit()

        return summary_id

    @staticmethod
    async def get_session_summary(session_id: str) -> dict | None:
        """Get summary for a specific session."""
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM session_summaries WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    async def update_session_summary(session_id: str, **kwargs) -> bool:
        """Update session summary fields."""
        allowed_fields = {
            "summary", "key_topics", "goals_achieved", "tools_used",
            "files_modified", "duration_seconds", "message_count",
            "token_usage", "model", "quality_score"
        }
        
        fields = []
        values = []
        for key, value in kwargs.items():
            if key not in allowed_fields:
                continue
            if key in ("key_topics", "goals_achieved", "tools_used", "files_modified"):
                fields.append(f"{key} = ?")
                values.append(json.dumps(value) if value else None)
            else:
                fields.append(f"{key} = ?")
                values.append(value)
        
        if not fields:
            return False
        
        fields.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(session_id)

        async with get_db() as db:
            cursor = await db.execute(
                f"UPDATE session_summaries SET {', '.join(fields)} WHERE session_id = ?",
                values,
            )
            await db.commit()
            return cursor.rowcount > 0

    # ── Knowledge Entries ──────────────────────────────────────────────

    @staticmethod
    async def create_knowledge_entry(
        entry_type: str,
        scope: str,
        content: str,
        scope_identifier: str | None = None,
        source_session_id: str | None = None,
        confidence: float = 1.0,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Create a knowledge entry."""
        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with get_db() as db:
            await db.execute(
                """INSERT INTO knowledge_entries 
                   (id, entry_type, scope, scope_identifier, content, source_session_id,
                    confidence, usage_count, tags, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
                (
                    entry_id,
                    entry_type,
                    scope,
                    scope_identifier,
                    content,
                    source_session_id,
                    confidence,
                    json.dumps(tags) if tags else None,
                    json.dumps(metadata) if metadata else None,
                    now,
                    now,
                ),
            )
            await db.commit()

        return entry_id

    @staticmethod
    async def get_knowledge_entry(entry_id: str) -> dict | None:
        """Get a specific knowledge entry."""
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM knowledge_entries WHERE id = ?",
                (entry_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    async def search_knowledge(
        entry_type: str | None = None,
        scope: str | None = None,
        scope_identifier: str | None = None,
        tags: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        """Search knowledge entries with filters."""
        conditions = ["confidence >= ?"]
        params: list = [min_confidence]

        if entry_type:
            conditions.append("entry_type = ?")
            params.append(entry_type)
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        if scope_identifier:
            conditions.append("scope_identifier = ?")
            params.append(scope_identifier)
        if tags:
            # Simple JSON array contains check
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        where_clause = " AND ".join(conditions)
        params.append(limit)

        async with get_db() as db:
            cursor = await db.execute(
                f"""SELECT * FROM knowledge_entries 
                    WHERE {where_clause}
                    ORDER BY confidence DESC, usage_count DESC
                    LIMIT ?""",
                params,
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    async def update_knowledge_entry(entry_id: str, **kwargs) -> bool:
        """Update knowledge entry fields."""
        allowed_fields = {
            "content", "confidence", "tags", "metadata", "embedding"
        }
        
        fields = []
        values = []
        for key, value in kwargs.items():
            if key not in allowed_fields:
                continue
            if key in ("tags", "metadata"):
                fields.append(f"{key} = ?")
                values.append(json.dumps(value) if value else None)
            else:
                fields.append(f"{key} = ?")
                values.append(value)
        
        if not fields:
            return False
        
        fields.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(entry_id)

        async with get_db() as db:
            cursor = await db.execute(
                f"UPDATE knowledge_entries SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def increment_usage(entry_id: str) -> bool:
        """Increment usage count for a knowledge entry."""
        async with get_db() as db:
            cursor = await db.execute(
                "UPDATE knowledge_entries SET usage_count = usage_count + 1 WHERE id = ?",
                (entry_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def delete_knowledge_entry(entry_id: str) -> bool:
        """Delete a knowledge entry."""
        async with get_db() as db:
            cursor = await db.execute(
                "DELETE FROM knowledge_entries WHERE id = ?",
                (entry_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    # ── FTS5 Search ────────────────────────────────────────────────────

    @staticmethod
    async def fulltext_search_knowledge(
        query: str,
        entry_type: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Full-text search across knowledge entries using FTS5."""
        try:
            async with get_db() as db:
                # Ensure FTS table exists and is populated
                await _ensure_fts_populated(db)

                if entry_type:
                    cursor = await db.execute(
                        """SELECT k.* FROM knowledge_search ks
                           JOIN knowledge_entries k ON ks.entry_id = k.id
                           WHERE knowledge_search MATCH ? AND ks.entry_type = ?
                           ORDER BY rank
                           LIMIT ?""",
                        (query, entry_type, limit),
                    )
                else:
                    cursor = await db.execute(
                        """SELECT k.* FROM knowledge_search ks
                           JOIN knowledge_entries k ON ks.entry_id = k.id
                           WHERE knowledge_search MATCH ?
                           ORDER BY rank
                           LIMIT ?""",
                        (query, limit),
                    )
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            log.warning("FTS5 search failed, falling back to LIKE search: %s", e)
            # Fallback to LIKE search
            return await KnowledgeBase.search_knowledge(
                entry_type=entry_type,
                min_confidence=0.0,
                limit=limit,
            )

    @staticmethod
    async def fulltext_search_sessions(
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """Full-text search across session summaries using FTS5."""
        async with get_db() as db:
            await _ensure_fts_populated(db)

            cursor = await db.execute(
                """SELECT s.name, ss.* FROM session_summary_search sss
                   JOIN session_summaries ss ON sss.summary_id = ss.id
                   JOIN sessions s ON ss.session_id = s.id
                   WHERE session_summary_search MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Tool Executions ────────────────────────────────────────────────

    @staticmethod
    async def log_tool_execution(
        session_id: str,
        tool_name: str,
        arguments: dict | None = None,
        result_summary: str | None = None,
        result_full: str | None = None,
        duration_ms: int | None = None,
        success: bool = True,
        error_message: str | None = None,
        token_usage: int | None = None,
    ) -> str:
        """Log a tool execution."""
        execution_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with get_db() as db:
            await db.execute(
                """INSERT INTO tool_executions 
                   (id, session_id, tool_name, arguments, result_summary, result_full,
                    duration_ms, success, error_message, token_usage, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution_id,
                    session_id,
                    tool_name,
                    json.dumps(arguments) if arguments else None,
                    result_summary[:1000] if result_summary else None,
                    result_full,
                    duration_ms,
                    1 if success else 0,
                    error_message,
                    token_usage,
                    now,
                ),
            )
            await db.commit()

        return execution_id

    @staticmethod
    async def get_tool_stats(
        session_id: str | None = None,
        tool_name: str | None = None,
        period_days: int | None = None,
    ) -> dict:
        """Get tool usage statistics."""
        conditions = []
        params: list = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if period_days:
            conditions.append("created_at >= datetime('now', ?)")
            params.append(f"-{period_days} days")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        async with get_db() as db:
            cursor = await db.execute(
                f"""SELECT 
                        tool_name,
                        COUNT(*) as total_calls,
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful,
                        SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed,
                        AVG(duration_ms) as avg_duration_ms,
                        SUM(token_usage) as total_tokens
                    FROM tool_executions
                    {where_clause}
                    GROUP BY tool_name
                    ORDER BY total_calls DESC""",
                params,
            )
            rows = await cursor.fetchall()
            return {row["tool_name"]: dict(row) for row in rows}

    # ── LLM Usage ──────────────────────────────────────────────────────

    @staticmethod
    async def log_llm_usage(
        session_id: str,
        model: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        finish_reason: str | None = None,
        duration_ms: int | None = None,
        estimated_cost_usd: float | None = None,
    ) -> str:
        """Log LLM usage."""
        usage_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with get_db() as db:
            await db.execute(
                """INSERT INTO llm_usage 
                   (id, session_id, model, prompt_tokens, completion_tokens,
                    total_tokens, finish_reason, duration_ms, estimated_cost_usd, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    usage_id,
                    session_id,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    finish_reason,
                    duration_ms,
                    estimated_cost_usd,
                    now,
                ),
            )
            await db.commit()

        return usage_id

    @staticmethod
    async def get_llm_stats(
        session_id: str | None = None,
        model: str | None = None,
        period_days: int | None = None,
    ) -> dict:
        """Get LLM usage statistics."""
        conditions = []
        params: list = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if model:
            conditions.append("model = ?")
            params.append(model)
        if period_days:
            conditions.append("created_at >= datetime('now', ?)")
            params.append(f"-{period_days} days")

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        async with get_db() as db:
            cursor = await db.execute(
                f"""SELECT 
                        model,
                        COUNT(*) as total_calls,
                        SUM(prompt_tokens) as total_prompt_tokens,
                        SUM(completion_tokens) as total_completion_tokens,
                        SUM(total_tokens) as total_tokens,
                        AVG(duration_ms) as avg_duration_ms,
                        SUM(estimated_cost_usd) as total_cost_usd
                    FROM llm_usage
                    {where_clause}
                    GROUP BY model
                    ORDER BY total_calls DESC""",
                params,
            )
            rows = await cursor.fetchall()
            return {row["model"]: dict(row) for row in rows}

    # ── Session Tags ───────────────────────────────────────────────────

    @staticmethod
    async def add_session_tag(
        session_id: str,
        tag: str,
        source: str = "user",
    ) -> str:
        """Add a tag to a session."""
        tag_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with get_db() as db:
            try:
                await db.execute(
                    """INSERT INTO session_tags (id, session_id, tag, source, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (tag_id, session_id, tag, source, now),
                )
                await db.commit()
                return tag_id
            except Exception:
                # Tag already exists
                return ""

    @staticmethod
    async def get_session_tags(session_id: str) -> list[str]:
        """Get all tags for a session."""
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT tag FROM session_tags WHERE session_id = ? ORDER BY tag",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [row["tag"] for row in rows]

    @staticmethod
    async def search_sessions_by_tags(
        tags: list[str],
        match_all: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        """Search sessions by tags."""
        if not tags:
            return []

        async with get_db() as db:
            if match_all:
                # Session must have ALL tags
                placeholders = ",".join(["?" for _ in tags])
                cursor = await db.execute(
                    f"""SELECT DISTINCT s.* FROM sessions s
                        JOIN session_tags st ON s.id = st.session_id
                        WHERE st.tag IN ({placeholders})
                        GROUP BY s.id
                        HAVING COUNT(DISTINCT st.tag) = ?
                        ORDER BY s.updated_at DESC
                        LIMIT ?""",
                    (*tags, len(tags), limit),
                )
            else:
                # Session must have ANY tag
                placeholders = ",".join(["?" for _ in tags])
                cursor = await db.execute(
                    f"""SELECT DISTINCT s.* FROM sessions s
                        JOIN session_tags st ON s.id = st.session_id
                        WHERE st.tag IN ({placeholders})
                        ORDER BY s.updated_at DESC
                        LIMIT ?""",
                    (*tags, limit),
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── File Snapshots ─────────────────────────────────────────────────

    @staticmethod
    async def log_file_snapshot(
        session_id: str,
        file_path: str,
        action: str,
        content_hash: str | None = None,
        content_preview: str | None = None,
        size_bytes: int | None = None,
    ) -> str:
        """Log a file snapshot."""
        snapshot_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with get_db() as db:
            await db.execute(
                """INSERT INTO file_snapshots 
                   (id, session_id, file_path, action, content_hash, content_preview, 
                    size_bytes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    session_id,
                    file_path,
                    action,
                    content_hash,
                    content_preview[:500] if content_preview else None,
                    size_bytes,
                    now,
                ),
            )
            await db.commit()

        return snapshot_id

    @staticmethod
    async def get_file_history(
        file_path: str,
        limit: int = 50,
    ) -> list[dict]:
        """Get modification history for a file."""
        async with get_db() as db:
            cursor = await db.execute(
                """SELECT fs.*, s.name as session_name 
                   FROM file_snapshots fs
                   JOIN sessions s ON fs.session_id = s.id
                   WHERE fs.file_path = ?
                   ORDER BY fs.created_at DESC
                   LIMIT ?""",
                (file_path, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


# ── Helper Functions ─────────────────────────────────────────────────────

async def _ensure_fts_populated(db):
    """Ensure FTS tables are populated with current data."""
    try:
        # Check if knowledge_search exists and is empty
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_search'"
        )
        if await cursor.fetchone():
            cursor = await db.execute("SELECT COUNT(*) FROM knowledge_search")
            count = (await cursor.fetchone())[0]
            
            if count == 0:
                # Populate from knowledge_entries
                await db.execute(
                    """INSERT INTO knowledge_search(entry_id, entry_type, content, tags, scope, scope_identifier)
                       SELECT id, entry_type, content, tags, scope, scope_identifier
                       FROM knowledge_entries"""
                )
    except Exception as e:
        log.warning("Failed to populate knowledge_search FTS: %s", e)
    
    try:
        # Check if session_summary_search exists and is empty
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='session_summary_search'"
        )
        if await cursor.fetchone():
            cursor = await db.execute("SELECT COUNT(*) FROM session_summary_search")
            count = (await cursor.fetchone())[0]
            
            if count == 0:
                # Populate from session_summaries
                await db.execute(
                    """INSERT INTO session_summary_search(summary_id, session_id, summary, key_topics, tools_used, files_modified)
                       SELECT id, session_id, summary, key_topics, tools_used, files_modified
                       FROM session_summaries"""
                )
    except Exception as e:
        log.warning("Failed to populate session_summary_search FTS: %s", e)
    
    try:
        await db.commit()
    except Exception:
        pass
