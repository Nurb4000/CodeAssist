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
import sqlite3
import uuid
from datetime import UTC, datetime

from .session import get_db

log = logging.getLogger(__name__)

# result_summary is capped at 1000 chars; keep result_full bounded too so a
# single large tool output (big file read, long shell dump) cannot bloat the
# SQLite DB or blow up /api/kb/sessions/{id} which returns tool_calls.
MAX_RESULT_FULL_CHARS = 100_000

# Sentinel returned by _coerce_number when a value can't be converted, so the
# caller can skip the field instead of storing a corrupt type in a numeric column.
_SKIP = object()


def _coerce_number(value, kind: str):
    """Coerce ``value`` to int/float for storage.

    Returns ``None`` to clear the column, the coerced number on success, or
    ``_SKIP`` when ``value`` is a non-null value that can't be parsed. Callers
    drop ``_SKIP`` fields so a bad client payload (e.g. confidence="high") never
    corrupts a REAL/INTEGER column or 500s the request (B3).
    """
    if value is None:
        return None
    try:
        return int(value) if kind == "int" else float(value)
    except (TypeError, ValueError):
        log.warning("Ignoring non-%s value %r for numeric field", kind, value)
        return _SKIP


def _strip_embeddings(rows: list[dict]) -> list[dict]:
    """Remove the raw binary embedding column so rows stay JSON-serializable.

    The `embedding` column stores a struct.pack blob (bytes), which is not
    JSON-serializable and would cause 500s on search/list endpoints once any
    entry has an embedding. Mirrors the stripping done in export_all/import_all.
    """
    if not rows:
        return rows
    cleaned = []
    for r in rows:
        r.pop("embedding", None)
        cleaned.append(r)
    return cleaned


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
        now = datetime.now(UTC).isoformat()

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
        int_fields = {"duration_seconds", "message_count", "token_usage"}
        float_fields = {"quality_score"}

        fields = []
        values = []
        for key, value in kwargs.items():
            if key not in allowed_fields:
                continue
            if key in ("key_topics", "goals_achieved", "tools_used", "files_modified"):
                fields.append(f"{key} = ?")
                values.append(json.dumps(value) if value else None)
            elif key in int_fields or key in float_fields:
                coerced = _coerce_number(
                    value, "int" if key in int_fields else "float"
                )
                if coerced is _SKIP:
                    continue
                fields.append(f"{key} = ?")
                values.append(coerced)
            else:
                fields.append(f"{key} = ?")
                values.append(value)
        
        if not fields:
            return False
        
        fields.append("updated_at = ?")
        values.append(datetime.now(UTC).isoformat())
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
        now = datetime.now(UTC).isoformat()

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
            if not row:
                return None
            row_dict = dict(row)
            row_dict.pop("embedding", None)
            return row_dict

    @staticmethod
    async def search_knowledge(
        entry_type: str | None = None,
        scope: str | None = None,
        scope_identifier: str | None = None,
        tags: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
        status: str | None = "active",
    ) -> list[dict]:
        """Search knowledge entries with filters.

        Defaults to ``status='active'`` so archived/flagged/review entries are
        excluded from default listing and dedup (G2). Pass ``status=None`` to
        query every lifecycle state (e.g. the PII scan or a review UI listing
        flagged/archived rows).
        """
        conditions = ["confidence >= ?"]
        params: list = [min_confidence]
        if status is not None:
            conditions.append("status = ?")
            params.append(status)

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
            # JSON-array "contains" check. Escape LIKE wildcards so a tag name
            # containing %, _, or \\ is matched literally instead of acting as a
            # pattern (G4): searching "a_b" must not match stored "axb".
            for tag in tags:
                escaped = (
                    tag.replace("\\", "\\\\").replace("%", "\\%")
                    .replace("_", "\\_")
                )
                conditions.append("tags LIKE ? ESCAPE '\\'")
                params.append(f'%"{escaped}"%')

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
            return _strip_embeddings([dict(r) for r in rows])

    @staticmethod
    async def update_knowledge_entry(entry_id: str, **kwargs) -> bool:
        """Update knowledge entry fields."""
        allowed_fields = {
            "content", "confidence", "tags", "metadata", "embedding"
        }
        float_fields = {"confidence"}

        fields = []
        values = []
        for key, value in kwargs.items():
            if key not in allowed_fields:
                continue
            if key in ("tags", "metadata"):
                fields.append(f"{key} = ?")
                values.append(json.dumps(value) if value else None)
            elif key in float_fields:
                coerced = _coerce_number(value, "float")
                if coerced is _SKIP:
                    continue
                fields.append(f"{key} = ?")
                values.append(coerced)
            else:
                fields.append(f"{key} = ?")
                values.append(value)
        
        if not fields:
            return False
        
        fields.append("updated_at = ?")
        values.append(datetime.now(UTC).isoformat())
        values.append(entry_id)

        async with get_db() as db:
            cursor = await db.execute(
                f"UPDATE knowledge_entries SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def merge_knowledge_entry(
        entry_id: str,
        confidence: float | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """Merge a near-duplicate into an existing entry instead of inserting a new one.

        Bumps usage_count (a signal for later promotion/retention), takes the max
        confidence, and unions tags. Returns True if the entry existed and was updated.
        """
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT confidence, tags FROM knowledge_entries WHERE id = ?",
                (entry_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return False

            existing_conf = row["confidence"] or 0.0
            new_conf = confidence if confidence is not None else existing_conf
            final_conf = max(existing_conf, new_conf)

            existing_tags = json.loads(row["tags"]) if row["tags"] else []
            incoming_tags = tags or []
            all_tags = list(dict.fromkeys([*existing_tags, *incoming_tags]))

            await db.execute(
                """UPDATE knowledge_entries
                   SET confidence = ?, tags = ?, usage_count = usage_count + 1,
                       updated_at = ?
                   WHERE id = ?""",
                (
                    final_conf,
                    json.dumps(all_tags) if all_tags else None,
                    datetime.now(UTC).isoformat(),
                    entry_id,
                ),
            )
            await db.commit()
            return True

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
    async def run_quality_pass(
        min_confidence: float = 0.5,
        max_usage: int = 0,
        promote_after: int = 3,
    ) -> dict:
        """Run the periodic quality/retention pass (G3/F2).

        Soft-deletes (archives) active entries whose confidence is below
        ``min_confidence`` and which have been used ``max_usage`` times or fewer.
        Rows are archived, not deleted, so their content stays recoverable. Also
        returns ``promotable``: active entries whose ``usage_count`` meets
        ``promote_after`` (a live signal that frequently-used entries are worth
        promoting, e.g. to a skill). Returns a report.
        """
        async with get_db() as db:
            # Total active pool examined by this pass (distinct from the subset
            # that matched the archive criteria below).
            cursor = await db.execute(
                "SELECT COUNT(*) as n FROM knowledge_entries WHERE status = 'active'"
            )
            scanned = (await cursor.fetchone())["n"]

            cursor = await db.execute(
                """SELECT id, entry_type, scope, scope_identifier, confidence, usage_count,
                           metadata
                    FROM knowledge_entries
                    WHERE status = 'active'
                      AND confidence < ?
                      AND usage_count <= ?""",
                (min_confidence, max_usage),
            )
            candidates = [dict(r) for r in await cursor.fetchall()]

            now = datetime.now(UTC).isoformat()
            for c in candidates:
                metadata = json.loads(c["metadata"]) if c["metadata"] else {}
                metadata["archived_reason"] = "low_confidence_unused"
                metadata["archived_at"] = now
                await db.execute(
                    """UPDATE knowledge_entries
                       SET status = 'archived', metadata = ?, updated_at = ?
                       WHERE id = ?""",
                    (json.dumps(metadata), now, c["id"]),
                )
            await db.commit()

            cursor = await db.execute(
                """SELECT id, entry_type, scope, confidence, usage_count
                   FROM knowledge_entries
                   WHERE status = 'active' AND usage_count >= ?
                   ORDER BY usage_count DESC""",
                (promote_after,),
            )
            promotable = [dict(r) for r in await cursor.fetchall()]

        return {
            "scanned": scanned,
            "archived": len(candidates),
            "candidates": candidates,
            "promotable": promotable,
            "promotable_count": len(promotable),
        }

    @staticmethod
    async def high_usage_entries(min_usage: int = 3, limit: int = 50) -> list[dict]:
        """Return active entries used at least ``min_usage`` times (F2 promotion candidates).

        Read-only: this never archives or mutates anything. Results are ordered by
        usage descending so the most-referenced entries surface first.
        """
        async with get_db() as db:
            cursor = await db.execute(
                """SELECT id, entry_type, scope, scope_identifier, confidence, usage_count
                   FROM knowledge_entries
                   WHERE status = 'active' AND usage_count >= ?
                   ORDER BY usage_count DESC LIMIT ?""",
                (min_usage, limit),
            )
            return [dict(r) for r in await cursor.fetchall()]

    # ── Lifecycle state machine (F3) ───────────────────────────────────

    VALID_STATUSES = {"active", "review", "flagged", "archived"}  # noqa: RUF012

    @staticmethod
    async def set_entry_status(entry_id: str, status: str) -> bool:
        """Transition an entry's lifecycle status. Returns True if a row changed."""
        if status not in KnowledgeBase.VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        async with get_db() as db:
            cursor = await db.execute(
                "UPDATE knowledge_entries SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now(UTC).isoformat(), entry_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def flag_for_review(entry_id: str) -> bool:
        """Flag an entry for human review."""
        return await KnowledgeBase.set_entry_status(entry_id, "review")

    @staticmethod
    async def flag_entry(entry_id: str) -> bool:
        """Flag an entry as questionable (e.g. suspected PII/secrets)."""
        return await KnowledgeBase.set_entry_status(entry_id, "flagged")

    @staticmethod
    async def flag_entries_for_pii(findings: dict[str, set[str]]) -> int:
        """Mark active entries that contain PII/secrets as ``flagged`` (F3 quality gate).

        Closes the loop between PII detection and the lifecycle state machine:
        flagged entries surface in the stats bar's "flagged" count and are
        protected from automatic quality-pass archival (which only touches
        ``active`` rows). Idempotent and status-guarded: only ``active`` entries
        become ``flagged`` (never un-archives or disturbs review/archived rows),
        and detected PII types are merged into metadata without clobbering
        existing fields (e.g. Q->A answers). Returns the number of entries updated.
        """
        if not findings:
            return 0
        now = datetime.now(UTC).isoformat()
        updated = 0
        async with get_db() as db:
            for entry_id, pii_types in findings.items():
                cursor = await db.execute(
                    "SELECT metadata, status FROM knowledge_entries WHERE id = ?",
                    (entry_id,),
                )
                row = await cursor.fetchone()
                if not row:
                    continue
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                existing = set(meta.get("pii_found", []))
                new_types = pii_types - existing
                status = row["status"]
                # Flip active -> flagged; leave any other (review/archived) status
                # untouched so we never un-archive or disturb a review in progress.
                new_status = "flagged" if status == "active" else status
                changed = bool(new_types) or new_status != status
                if not changed:
                    continue
                if new_types:
                    meta["pii_found"] = sorted(existing | new_types)
                    meta["pii_flagged_at"] = now
                await db.execute(
                    "UPDATE knowledge_entries SET status=?, metadata=?, updated_at=? WHERE id=?",
                    (new_status, json.dumps(meta), now, entry_id),
                )
                if new_status != status:
                    updated += 1
            await db.commit()
        return updated

    @staticmethod
    async def approve_entry(entry_id: str) -> bool:
        """Approve a reviewed entry, returning it to the active set."""
        return await KnowledgeBase.set_entry_status(entry_id, "active")

    @staticmethod
    async def archive_entry(entry_id: str) -> bool:
        """Soft-archive an entry (content preserved, excluded from default search)."""
        return await KnowledgeBase.set_entry_status(entry_id, "archived")

    @staticmethod
    async def restore_entry(entry_id: str) -> bool:
        """Restore an archived entry back to active."""
        return await KnowledgeBase.set_entry_status(entry_id, "active")

    @staticmethod
    async def entry_status_counts() -> dict:
        """Count entries by lifecycle status (for the stats bar)."""
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status, COUNT(*) as count FROM knowledge_entries GROUP BY status"
            )
            rows = await cursor.fetchall()
        counts = {status: 0 for status in KnowledgeBase.VALID_STATUSES}
        for r in rows:
            counts[r["status"]] = r["count"]
        return counts

    @staticmethod
    async def orphan_entry_count() -> int:
        """Count entries referencing a session_id that no longer exists."""
        async with get_db() as db:
            cursor = await db.execute(
                """SELECT COUNT(*) as c FROM knowledge_entries ke
                   WHERE ke.source_session_id IS NOT NULL
                     AND ke.source_session_id NOT IN (SELECT id FROM sessions)"""
            )
            return (await cursor.fetchone())["c"]

    @staticmethod
    async def list_orphan_entries(limit: int = 200, offset: int = 0) -> list[dict]:
        """List entries whose source session has been deleted (candidates for triage/delete)."""
        async with get_db() as db:
            cursor = await db.execute(
                """SELECT * FROM knowledge_entries ke
                   WHERE ke.source_session_id IS NOT NULL
                     AND ke.source_session_id NOT IN (SELECT id FROM sessions)
                   ORDER BY updated_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            return await cursor.fetchall()

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
        status: str | None = "active",
    ) -> list[dict]:
        """Full-text search across knowledge entries using FTS5.

        Defaults to active-only (G2) so archived/flagged/review rows don't surface
        in search; pass an explicit ``status`` to include another lifecycle state.
        """
        try:
            async with get_db() as db:
                # Ensure FTS table exists and is populated
                await _ensure_fts_populated(db)

                if entry_type:
                    cursor = await db.execute(
                        """SELECT k.* FROM knowledge_search ks
                           JOIN knowledge_entries k ON ks.entry_id = k.id
                           WHERE knowledge_search MATCH ? AND ks.entry_type = ? AND k.status = ?
                           ORDER BY rank
                           LIMIT ?""",
                        (query, entry_type, status, limit),
                    )
                else:
                    cursor = await db.execute(
                        """SELECT k.* FROM knowledge_search ks
                           JOIN knowledge_entries k ON ks.entry_id = k.id
                           WHERE knowledge_search MATCH ? AND k.status = ?
                           ORDER BY rank
                           LIMIT ?""",
                        (query, status, limit),
                    )
                rows = await cursor.fetchall()
                return _strip_embeddings([dict(r) for r in rows])
        except Exception as e:  # noqa: BLE001
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
        now = datetime.now(UTC).isoformat()

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
                    (result_full[:MAX_RESULT_FULL_CHARS] if result_full else None),
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
            conditions.append("datetime(created_at) >= datetime('now', ?)")
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
        now = datetime.now(UTC).isoformat()

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
            conditions.append("datetime(created_at) >= datetime('now', ?)")
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
        now = datetime.now(UTC).isoformat()

        async with get_db() as db:
            try:
                await db.execute(
                    """INSERT INTO session_tags (id, session_id, tag, source, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (tag_id, session_id, tag, source, now),
                )
                await db.commit()
                return tag_id
            except sqlite3.IntegrityError:
                # Duplicate (session_id, tag) — the UNIQUE constraint. Return ""
                # so callers can treat it as an idempotent no-op. Other errors
                # (connection/IO) propagate instead of being swallowed.
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
        now = datetime.now(UTC).isoformat()

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

    # ── Export / Import ────────────────────────────────────────────────

    EXPORT_TABLES = [  # noqa: RUF012
        "session_summaries", "knowledge_entries", "tool_executions",
        "llm_usage", "session_tags", "file_snapshots",
    ]

    @staticmethod
    async def export_all() -> dict:
        """Export all KB data as a JSON-serializable dict (without embeddings)."""
        from .session import get_db  # local import to avoid circular
        export = {"version": 1, "exported_at": datetime.now(UTC).isoformat(), "data": {}}
        async with get_db() as db:
            for table in KnowledgeBase.EXPORT_TABLES:
                cursor = await db.execute(f"SELECT * FROM {table}")
                rows = [dict(r) for r in await cursor.fetchall()]
                # Strip binary embedding column from knowledge_entries
                for row in rows:
                    row.pop("embedding", None)
                export["data"][table] = rows
        return export

    @staticmethod
    async def import_all(export: dict, source_session_id: str | None = None) -> dict:
        """Import KB data from an export dict. Returns counts per table."""
        from .session import get_db
        counts = {}
        async with get_db() as db:
            for table in KnowledgeBase.EXPORT_TABLES:
                rows = export.get("data", {}).get(table, [])
                if not rows:
                    counts[table] = 0
                    continue
                # Get column names from the first row, skip id and auto-generated fields
                inserted = 0
                for row in rows:
                    cols = []
                    vals = []
                    for k, v in row.items():
                        if k == "id":
                            cols.append(k)
                            import uuid
                            vals.append(str(uuid.uuid4()))
                        elif k in ("created_at", "updated_at"):
                            cols.append(k)
                            vals.append(datetime.now(UTC).isoformat())
                        elif k == "source_session_id" and source_session_id:
                            cols.append(k)
                            vals.append(source_session_id)
                        elif k == "embedding":
                            continue  # skip binary blobs
                        else:
                            cols.append(k)
                            vals.append(v)
                    placeholders = ",".join("?" for _ in cols)
                    try:
                        await db.execute(
                            f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                            vals,
                        )
                        inserted += 1
                    except Exception as e:  # noqa: BLE001
                        log.debug("Skipping row in %s: %s", table, e)
                await db.commit()
                counts[table] = inserted
        # Rebuild FTS indexes after import
        await KnowledgeBase._rebuild_fts()
        return counts

    @staticmethod
    async def _rebuild_fts():
        """Rebuild FTS5 virtual tables after import."""
        from .session import get_db
        try:
            async with get_db() as db:
                await _ensure_fts_populated(db)
        except Exception as e:  # noqa: BLE001
            log.warning("FTS rebuild failed: %s", e)


async def _ensure_fts_populated(db):
    """Ensure FTS tables are populated with current data (fallback for initial setup)."""
    # Note: Triggers in session.py handle incremental updates.
    # This function only runs once during initial setup if triggers failed to create.
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
    except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to populate session_summary_search FTS: %s", e)
    
    try:
        await db.commit()
    except Exception:  # noqa: BLE001, S110
        pass
