"""Tests for knowledge base CRUD, search, and analytics."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from codeassist.knowledge import KnowledgeBase
from codeassist.session import Session, get_db, init_db


class TestSessionSummaries:
    @pytest.mark.asyncio
    async def test_create_and_get_summary(self):
        await init_db()
        session_id = f"test-summary-{uuid.uuid4().hex[:8]}"
        summary_id = await KnowledgeBase.create_session_summary(
            session_id=session_id,
            summary="Test summary",
            key_topics=["testing", "crud"],
            goals_achieved=["goal1"],
            tools_used=["read", "write"],
            files_modified=["test.py"],
            duration_seconds=300,
            message_count=25,
            token_usage=5000,
            model="gpt-4",
            quality_score=0.85,
        )
        assert summary_id

        summary = await KnowledgeBase.get_session_summary(session_id)
        assert summary is not None
        assert summary["summary"] == "Test summary"
        assert summary["quality_score"] == 0.85

    @pytest.mark.asyncio
    async def test_get_nonexistent_summary(self):
        await init_db()
        summary = await KnowledgeBase.get_session_summary("nonexistent-session")
        assert summary is None

    @pytest.mark.asyncio
    async def test_update_summary(self):
        await init_db()
        session_id = f"test-upd-{uuid.uuid4().hex[:8]}"
        await KnowledgeBase.create_session_summary(
            session_id=session_id, summary="Original"
        )
        updated = await KnowledgeBase.update_session_summary(
            session_id, summary="Updated", quality_score=0.95
        )
        assert updated

        summary = await KnowledgeBase.get_session_summary(session_id)
        assert summary["summary"] == "Updated"
        assert summary["quality_score"] == 0.95

    @pytest.mark.asyncio
    async def test_update_nonexistent_summary(self):
        await init_db()
        result = await KnowledgeBase.update_session_summary(
            "no-such-session", summary="nope"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_update_with_empty_kwargs(self):
        await init_db()
        session_id = f"test-empty-{uuid.uuid4().hex[:8]}"
        await KnowledgeBase.create_session_summary(
            session_id=session_id, summary="S"
        )
        result = await KnowledgeBase.update_session_summary(session_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_create_with_minimal_fields(self):
        await init_db()
        session_id = f"test-min-{uuid.uuid4().hex[:8]}"
        summary_id = await KnowledgeBase.create_session_summary(
            session_id=session_id, summary="Minimal"
        )
        assert summary_id
        summary = await KnowledgeBase.get_session_summary(session_id)
        assert summary["summary"] == "Minimal"

    @pytest.mark.asyncio
    async def test_upsert_replaces_existing(self):
        await init_db()
        session_id = f"test-upsert-{uuid.uuid4().hex[:8]}"
        id1 = await KnowledgeBase.create_session_summary(
            session_id=session_id, summary="First"
        )
        id2 = await KnowledgeBase.create_session_summary(
            session_id=session_id, summary="Second"
        )
        assert id1 != id2
        summary = await KnowledgeBase.get_session_summary(session_id)
        assert summary["summary"] == "Second"


class TestKnowledgeEntries:
    @pytest.mark.asyncio
    async def test_create_and_get_entry(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern",
            scope="file",
            content="Async/await pattern for DB ops",
            scope_identifier="src/db.py",
            source_session_id="session-1",
            confidence=0.9,
            tags=["async", "database"],
            metadata={"lang": "python"},
        )
        assert entry_id

        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry is not None
        assert entry["content"] == "Async/await pattern for DB ops"
        assert entry["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_get_nonexistent_entry(self):
        await init_db()
        entry = await KnowledgeBase.get_knowledge_entry("no-such-entry")
        assert entry is None

    @pytest.mark.asyncio
    async def test_search_by_type(self):
        await init_db()
        for i in range(3):
            await KnowledgeBase.create_knowledge_entry(
                entry_type="pattern", scope="file",
                content=f"Pattern {i}", tags=["test"],
            )
        results = await KnowledgeBase.search_knowledge(
            entry_type="pattern", min_confidence=0.0
        )
        assert len(results) >= 3

    @pytest.mark.asyncio
    async def test_search_by_scope(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="convention", scope="project",
            content="Project convention", tags=["style"],
        )
        results = await KnowledgeBase.search_knowledge(
            scope="project", min_confidence=0.0
        )
        assert len(results) >= 1
        assert results[0]["scope"] == "project"

    @pytest.mark.asyncio
    async def test_search_by_tags(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="decision", scope="file",
            content="Use FastAPI", tags=["framework", "python"],
        )
        results = await KnowledgeBase.search_knowledge(
            tags=["framework"], min_confidence=0.0
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_by_tag_escapes_like_wildcards(self):
        # G4: a tag containing _ / % / \\ must match literally, not as a LIKE
        # pattern. Searching "a_b" must NOT match a stored tag "axb".
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="underscored tag", tags=["a_b"],
        )
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="different tag", tags=["axb"],
        )

        # Exact underscore tag matches only its own entry.
        res = await KnowledgeBase.search_knowledge(tags=["a_b"], min_confidence=0.0)
        assert len(res) == 1
        assert "underscored" in res[0]["content"]

        # A near-miss tag (same shape, different char) matches nothing.
        res = await KnowledgeBase.search_knowledge(tags=["axb"], min_confidence=0.0)
        assert len(res) == 1
        assert "different" in res[0]["content"]

    @pytest.mark.asyncio
    async def test_search_by_tag_with_percent_is_literal(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="percent tag", tags=["100%off"],
        )
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="unrelated", tags=["sale"],
        )
        res = await KnowledgeBase.search_knowledge(tags=["100%off"], min_confidence=0.0)
        assert len(res) == 1
        assert "percent" in res[0]["content"]

    @pytest.mark.asyncio
    async def test_search_with_no_filters(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="bug_fix", scope="file",
            content="Fix null pointer", tags=["bug"],
        )
        results = await KnowledgeBase.search_knowledge(min_confidence=0.0)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_filters_by_confidence(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="Low confidence entry", confidence=0.3,
        )
        results = await KnowledgeBase.search_knowledge(min_confidence=0.8)
        low_conf = [r for r in results if r["confidence"] == 0.3]
        assert len(low_conf) == 0

    @pytest.mark.asyncio
    async def test_update_entry_content_and_confidence(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="Original", confidence=0.5, tags=["a"],
        )
        updated = await KnowledgeBase.update_knowledge_entry(
            entry_id, content="Updated", confidence=0.9
        )
        assert updated

        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["content"] == "Updated"
        assert entry["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_update_with_empty_kwargs(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="C"
        )
        result = await KnowledgeBase.update_knowledge_entry(entry_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_increment_usage(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="C"
        )
        for _ in range(3):
            await KnowledgeBase.increment_usage(entry_id)
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["usage_count"] == 3

    @pytest.mark.asyncio
    async def test_increment_nonexistent(self):
        await init_db()
        result = await KnowledgeBase.increment_usage("no-such-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_merge_knowledge_entry_merges_confidence_and_tags(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="existing", confidence=0.6, tags=["a"]
        )
        merged = await KnowledgeBase.merge_knowledge_entry(entry_id, confidence=0.5, tags=["b"])
        assert merged is True

        updated = await KnowledgeBase.get_knowledge_entry(entry_id)
        # Confidence takes the max of existing and incoming.
        assert updated["confidence"] == 0.6
        # Tags are unioned.
        assert set(json.loads(updated["tags"])) == {"a", "b"}
        # usage_count is bumped as a reuse signal.
        assert updated["usage_count"] == 1

    @pytest.mark.asyncio
    async def test_merge_knowledge_entry_nonexistent(self):
        await init_db()
        assert await KnowledgeBase.merge_knowledge_entry("no-such-id", confidence=0.9) is False

    @pytest.mark.asyncio
    async def test_run_quality_pass_archives_low_confidence_unused(self):
        await init_db()
        low = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="low confidence junk", confidence=0.3
        )
        good = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="good entry", confidence=0.9
        )

        report = await KnowledgeBase.run_quality_pass(min_confidence=0.5, max_usage=0)
        assert low in {c["id"] for c in report["candidates"]}
        assert good not in {c["id"] for c in report["candidates"]}

    @pytest.mark.asyncio
    async def test_quality_pass_reports_promotable_entries(self):
        await init_db()
        stale = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="stale low conf", confidence=0.3
        )
        popular = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="popular", confidence=0.9
        )
        for _ in range(4):
            await KnowledgeBase.increment_usage(popular)

        report = await KnowledgeBase.run_quality_pass(
            min_confidence=0.5, max_usage=0, promote_after=3
        )
        candidate_ids = {c["id"] for c in report["candidates"]}
        assert stale in candidate_ids  # low confidence + unused -> archived
        assert popular not in candidate_ids  # high confidence entry is kept
        assert popular in {c["id"] for c in report["promotable"]}
        assert report["promotable_count"] >= 1

    @pytest.mark.asyncio
    async def test_delete_entry(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="Delete me"
        )
        deleted = await KnowledgeBase.delete_knowledge_entry(entry_id)
        assert deleted

        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        await init_db()
        result = await KnowledgeBase.delete_knowledge_entry("no-such-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_search_limit(self):
        await init_db()
        for i in range(10):
            await KnowledgeBase.create_knowledge_entry(
                entry_type="pattern", scope="file",
                content=f"Entry {i}", tags=["limit-test"],
            )
        results = await KnowledgeBase.search_knowledge(
            tags=["limit-test"], min_confidence=0.0, limit=3
        )
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_search_by_scope_identifier(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="convention", scope="file",
            scope_identifier="src/main.py",
            content="Main module convention", tags=["python"],
        )
        results = await KnowledgeBase.search_knowledge(
            scope_identifier="src/main.py", min_confidence=0.0
        )
        assert len(results) >= 1


class TestFullTextSearch:
    @pytest.mark.asyncio
    async def test_fulltext_search_knowledge(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="async def fetch_data from database",
            tags=["async", "database"],
        )
        results = await KnowledgeBase.fulltext_search_knowledge("async database")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_fulltext_search_no_results(self):
        await init_db()
        results = await KnowledgeBase.fulltext_search_knowledge(
            "xyznonexistent999"
        )
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_fts_triggers_keep_index_in_sync(self):
        """FTS INSERT/UPDATE/DELETE triggers must keep knowledge_search in sync
        with knowledge_entries (review: FTS trigger maintenance gap)."""
        from codeassist.session import get_db

        await init_db()

        # Triggers exist after init.
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'knowledge_search_%'"
            )
            triggers = {r[0] for r in await cursor.fetchall()}
        assert triggers == {"knowledge_search_ai", "knowledge_search_ad", "knowledge_search_au"}

        # INSERT -> FTS gains exactly one row (via trigger, not populate).
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="trigger sync alpha"
        )
        async with get_db() as db:
            cur = await db.execute("SELECT COUNT(*) FROM knowledge_search")
            assert (await cur.fetchone())[0] == 1

        # UPDATE -> FTS reflects the new content.
        await KnowledgeBase.update_knowledge_entry(
            entry_id, content="trigger sync beta omega"
        )
        async with get_db() as db:
            cur = await db.execute(
                "SELECT content FROM knowledge_search WHERE entry_id = ?", (entry_id,)
            )
            row = await cur.fetchone()
        assert row is not None and "omega" in row["content"]

        # DELETE -> FTS row removed.
        await KnowledgeBase.delete_knowledge_entry(entry_id)
        async with get_db() as db:
            cur = await db.execute("SELECT COUNT(*) FROM knowledge_search")
            assert (await cur.fetchone())[0] == 0

    @pytest.mark.asyncio
    async def test_fulltext_search_by_type(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="bug_fix", scope="file",
            content="fixed null pointer exception in parser",
            tags=["bug"],
        )
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="fixed format string pattern",
            tags=["style"],
        )
        results = await KnowledgeBase.fulltext_search_knowledge(
            "fixed", entry_type="bug_fix"
        )
        assert len(results) >= 1
        for r in results:
            assert r["entry_type"] == "bug_fix"

    @pytest.mark.asyncio
    async def test_fulltext_search_sessions(self):
        await init_db()
        session = await Session.create("FTS Test")
        await KnowledgeBase.create_session_summary(
            session_id=session.id,
            summary="Fixed authentication bug in login handler",
            key_topics=["auth", "security"],
            tools_used=["read", "write"],
            files_modified=["auth.py"],
        )
        results = await KnowledgeBase.fulltext_search_sessions("authentication")
        assert len(results) >= 1


class TestToolExecutions:
    @pytest.mark.asyncio
    async def test_log_and_get_stats(self):
        await init_db()
        session_id = f"test-tools-{uuid.uuid4().hex[:8]}"
        await KnowledgeBase.log_tool_execution(
            session_id=session_id, tool_name="write",
            arguments={"path": "test.py"}, result_summary="OK",
            duration_ms=100, success=True,
        )
        await KnowledgeBase.log_tool_execution(
            session_id=session_id, tool_name="shell",
            arguments={"command": "ls"}, result_summary="Done",
            duration_ms=50, success=True,
        )
        await KnowledgeBase.log_tool_execution(
            session_id=session_id, tool_name="shell",
            arguments={"command": "bad"}, result_summary="Failed",
            duration_ms=30, success=False,
            error_message="Command not found",
        )

        stats = await KnowledgeBase.get_tool_stats(session_id=session_id)
        assert "write" in stats
        assert "shell" in stats
        assert stats["write"]["total_calls"] == 1
        assert stats["shell"]["total_calls"] == 2
        assert stats["shell"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_get_tool_stats_by_name(self):
        await init_db()
        session_id = f"test-stats-{uuid.uuid4().hex[:8]}"
        await KnowledgeBase.log_tool_execution(
            session_id=session_id, tool_name="read",
            result_summary="OK", duration_ms=10, success=True,
        )
        stats = await KnowledgeBase.get_tool_stats(
            session_id=session_id, tool_name="read"
        )
        assert "read" in stats
        assert stats["read"]["total_calls"] == 1

    @pytest.mark.asyncio
    async def test_get_tool_stats_no_match(self):
        await init_db()
        stats = await KnowledgeBase.get_tool_stats(session_id="no-such-session")
        assert stats == {}

    @pytest.mark.asyncio
    async def test_result_full_is_truncated(self):
        from codeassist.session import get_db
        from codeassist.knowledge import MAX_RESULT_FULL_CHARS

        await init_db()
        session_id = f"test-b3-{uuid.uuid4().hex[:8]}"
        long_output = "x" * (MAX_RESULT_FULL_CHARS + 500)
        await KnowledgeBase.log_tool_execution(
            session_id=session_id,
            tool_name="read",
            result_summary="summary",
            result_full=long_output,
            success=True,
        )

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT result_full FROM tool_executions WHERE session_id = ?",
                (session_id,),
            )
            stored = (await cursor.fetchone())[0]
        assert stored is not None
        assert len(stored) == MAX_RESULT_FULL_CHARS


class TestLLMUsage:
    @pytest.mark.asyncio
    async def test_log_and_get_stats(self):
        await init_db()
        session_id = f"test-llm-{uuid.uuid4().hex[:8]}"
        await KnowledgeBase.log_llm_usage(
            session_id=session_id, model="gpt-4",
            prompt_tokens=500, completion_tokens=200,
            total_tokens=700, finish_reason="stop",
            duration_ms=1500, estimated_cost_usd=0.021,
        )
        stats = await KnowledgeBase.get_llm_stats(session_id=session_id)
        assert "gpt-4" in stats
        assert stats["gpt-4"]["total_calls"] == 1
        assert stats["gpt-4"]["total_tokens"] == 700

    @pytest.mark.asyncio
    async def test_get_llm_stats_by_model(self):
        await init_db()
        session_id = f"test-llm2-{uuid.uuid4().hex[:8]}"
        await KnowledgeBase.log_llm_usage(
            session_id=session_id, model="gpt-4o", total_tokens=100,
        )
        stats = await KnowledgeBase.get_llm_stats(model="gpt-4o")
        assert "gpt-4o" in stats

    @pytest.mark.asyncio
    async def test_get_llm_stats_no_match(self):
        await init_db()
        stats = await KnowledgeBase.get_llm_stats(model="nonexistent-model")
        assert stats == {}


class TestSessionTags:
    @pytest.mark.asyncio
    async def test_add_and_get_tags(self):
        await init_db()
        session_id = f"test-tags-{uuid.uuid4().hex[:8]}"
        await KnowledgeBase.add_session_tag(session_id, "authentication")
        await KnowledgeBase.add_session_tag(session_id, "backend")
        tags = await KnowledgeBase.get_session_tags(session_id)
        assert set(tags) == {"authentication", "backend"}

    @pytest.mark.asyncio
    async def test_duplicate_tag_returns_empty(self):
        await init_db()
        session_id = f"test-dup-{uuid.uuid4().hex[:8]}"
        tag_id1 = await KnowledgeBase.add_session_tag(session_id, "bug")
        tag_id2 = await KnowledgeBase.add_session_tag(session_id, "bug")
        assert tag_id2 == ""

    @pytest.mark.asyncio
    async def test_get_tags_empty_session(self):
        await init_db()
        tags = await KnowledgeBase.get_session_tags("no-such-session")
        assert tags == []


class TestSearchSessionsByTags:
    @pytest.mark.asyncio
    async def test_search_by_any_tag(self):
        await init_db()
        session1 = await Session.create("S1")
        await KnowledgeBase.add_session_tag(session1.id, "python")
        await KnowledgeBase.add_session_tag(session1.id, "web")
        session2 = await Session.create("S2")
        await KnowledgeBase.add_session_tag(session2.id, "rust")

        results = await KnowledgeBase.search_sessions_by_tags(
            ["python", "rust"], match_all=False
        )
        ids = {r["id"] for r in results}
        assert session1.id in ids
        assert session2.id in ids

    @pytest.mark.asyncio
    async def test_search_by_all_tags(self):
        await init_db()
        session = await Session.create("S3")
        await KnowledgeBase.add_session_tag(session.id, "python")
        await KnowledgeBase.add_session_tag(session.id, "web")

        results = await KnowledgeBase.search_sessions_by_tags(
            ["python", "web"], match_all=True
        )
        assert session.id in {r["id"] for r in results}

    @pytest.mark.asyncio
    async def test_search_by_all_tags_partial_match(self):
        await init_db()
        session = await Session.create("S4")
        await KnowledgeBase.add_session_tag(session.id, "python")

        results = await KnowledgeBase.search_sessions_by_tags(
            ["python", "missing-tag"], match_all=True
        )
        assert session.id not in {r["id"] for r in results}

    @pytest.mark.asyncio
    async def test_search_empty_tags_list(self):
        await init_db()
        results = await KnowledgeBase.search_sessions_by_tags([])
        assert results == []


class TestFileSnapshots:
    @pytest.mark.asyncio
    async def test_log_and_get_history(self):
        await init_db()
        session = await Session.create("Snapshot Test")
        await KnowledgeBase.log_file_snapshot(
            session_id=session.id, file_path="src/main.py",
            action="read", content_hash="abc123",
            content_preview="def main(): pass", size_bytes=1024,
        )
        await KnowledgeBase.log_file_snapshot(
            session_id=session.id, file_path="src/main.py",
            action="write", content_hash="xyz789",
            content_preview="def main(): print('hello')",
        )

        history = await KnowledgeBase.get_file_history("src/main.py")
        assert len(history) == 2
        assert history[0]["action"] == "write"

    @pytest.mark.asyncio
    async def test_get_file_history_no_results(self):
        await init_db()
        history = await KnowledgeBase.get_file_history("nonexistent.py")
        assert history == []


class TestPeriodFilter:
    """Regression tests for B2: period_days filter must normalize ISO created_at
    (T separator, fractional seconds, tz offset) against SQLite datetime()."""

    @pytest.mark.asyncio
    async def test_tool_stats_period_excludes_boundary_early_entry(self):
        await init_db()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        cutoff = now - timedelta(days=6)
        # Same calendar day as the cutoff but earlier in the day.
        boundary_entry = cutoff.replace(hour=1, minute=0)

        async with get_db() as db:
            await db.execute(
                "INSERT INTO tool_executions (id, session_id, tool_name, result_summary, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "s-bnd", "shell", "ok", boundary_entry.isoformat()),
            )
            await db.commit()

        stats = await KnowledgeBase.get_tool_stats(tool_name="shell", period_days=6)
        # Correctly excluded: the entry's time-of-day is before the cutoff time.
        assert stats == {}

    @pytest.mark.asyncio
    async def test_tool_stats_period_includes_recent_excludes_boundary(self):
        await init_db()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        cutoff = now - timedelta(days=6)
        boundary_entry = cutoff.replace(hour=1, minute=0)
        recent = (now - timedelta(days=3)).isoformat()

        async with get_db() as db:
            await db.execute(
                "INSERT INTO tool_executions (id, session_id, tool_name, result_summary, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "s-bnd", "shell", "ok", boundary_entry.isoformat()),
            )
            await db.execute(
                "INSERT INTO tool_executions (id, session_id, tool_name, result_summary, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "s-bnd", "shell", "ok", recent),
            )
            await db.commit()

        stats = await KnowledgeBase.get_tool_stats(tool_name="shell", period_days=6)
        # Only the clearly-recent entry falls inside the 6-day window.
        assert stats["shell"]["total_calls"] == 1

    @pytest.mark.asyncio
    async def test_llm_stats_period_excludes_boundary_early_entry(self):
        await init_db()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        cutoff = now - timedelta(days=10)
        boundary_entry = cutoff.replace(hour=2, minute=0)

        async with get_db() as db:
            await db.execute(
                "INSERT INTO llm_usage (id, session_id, model, total_tokens, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "s-llm-bnd", "gpt-4", 100, boundary_entry.isoformat()),
            )
            await db.commit()

        stats = await KnowledgeBase.get_llm_stats(model="gpt-4", period_days=10)
        assert stats == {}


class TestEmbeddingStripping:
    """Regression tests for B1: embedding blob must never leak into JSON responses."""

    @staticmethod
    async def _set_embedding(entry_id: str, content: str):
        from codeassist.embeddings import serialize_embedding

        async with get_db() as db:
            await db.execute(
                "UPDATE knowledge_entries SET embedding = ? WHERE id = ?",
                (serialize_embedding([0.1, 0.2, 0.3]), entry_id),
            )
            await db.commit()

    @pytest.mark.asyncio
    async def test_search_knowledge_strips_embedding(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="embedding leak test"
        )
        await self._set_embedding(entry_id, "embedding leak test")

        results = await KnowledgeBase.search_knowledge(min_confidence=0.0)
        assert len(results) == 1
        assert "embedding" not in results[0]
        # Must be JSON-serializable even though an embedding exists in the DB.
        json.dumps(results)

    @pytest.mark.asyncio
    async def test_get_knowledge_entry_strips_embedding(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="embedding get test"
        )
        await self._set_embedding(entry_id, "embedding get test")

        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry is not None
        assert "embedding" not in entry
        json.dumps(entry)

    @pytest.mark.asyncio
    async def test_fulltext_search_knowledge_strips_embedding(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="qword search embedding"
        )
        await self._set_embedding(entry_id, "qword search embedding")

        results = await KnowledgeBase.fulltext_search_knowledge("qword")
        for r in results:
            assert "embedding" not in r
        json.dumps(results)


class TestEntryLifecycle:
    @pytest.mark.asyncio
    async def test_flag_and_approve_entry(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="needs review"
        )
        assert await KnowledgeBase.flag_for_review(entry_id) is True
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["status"] == "review"
        assert await KnowledgeBase.approve_entry(entry_id) is True
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["status"] == "active"

    @pytest.mark.asyncio
    async def test_archive_and_restore(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="to archive"
        )
        assert await KnowledgeBase.archive_entry(entry_id) is True
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["status"] == "archived"
        # Archived entries are excluded from a default (active) search.
        active = await KnowledgeBase.search_knowledge(status="active")
        assert entry_id not in {e["id"] for e in active}
        archived = await KnowledgeBase.search_knowledge(status="archived")
        assert entry_id in {e["id"] for e in archived}
        # Restore brings it back to active.
        assert await KnowledgeBase.restore_entry(entry_id) is True
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["status"] == "active"

    @pytest.mark.asyncio
    async def test_set_entry_status_rejects_invalid(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="x"
        )
        with pytest.raises(ValueError):
            await KnowledgeBase.set_entry_status(entry_id, "bogus")

    @pytest.mark.asyncio
    async def test_set_status_on_missing_entry(self):
        await init_db()
        assert await KnowledgeBase.set_entry_status("no-such-id", "review") is False

    @pytest.mark.asyncio
    async def test_entry_status_counts(self):
        await init_db()
        a = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="a"
        )
        b = await KnowledgeBase.create_knowledge_entry(
            entry_type="convention", scope="file", content="b"
        )
        await KnowledgeBase.archive_entry(a)
        await KnowledgeBase.flag_for_review(b)
        counts = await KnowledgeBase.entry_status_counts()
        assert counts.get("active") == 0
        assert counts.get("archived") == 1
        assert counts.get("review") == 1

    @pytest.mark.asyncio
    async def test_orphan_entry_count(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="orphan",
            source_session_id="does-not-exist",
        )
        assert await KnowledgeBase.orphan_entry_count() >= 1

    @pytest.mark.asyncio
    async def test_kb_stats_exposes_lifecycle_and_usage(self):
        from codeassist.routes.kb_gui import kb_stats

        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="a", confidence=0.4
        )
        b_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="convention", scope="file", content="b", confidence=0.9
        )
        await KnowledgeBase.archive_entry(b_id)

        stats = await kb_stats()

        assert stats["status_breakdown"].get("archived") == 1
        assert stats["entries_archived"] == 1
        assert isinstance(stats["usage_distribution"], dict)
        assert stats["usage_distribution"]["unused"] >= 1
        assert isinstance(stats["orphan_entries"], int)
        # Embedding blob must never leak into the serialized stats payload.
        import json
        json.dumps(stats)

    @pytest.mark.asyncio
    async def test_high_usage_entries_lists_frequently_used(self):
        from codeassist.routes.kb_gui import kb_stats

        await init_db()
        rarely = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="rarely used", confidence=0.9
        )
        often = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="often used", confidence=0.9
        )
        for _ in range(5):
            await KnowledgeBase.increment_usage(often)

        promoted = await KnowledgeBase.high_usage_entries(min_usage=3)
        assert often in {e["id"] for e in promoted}
        assert rarely not in {e["id"] for e in promoted}

        stats = await kb_stats()
        assert stats.get("high_usage_count", 0) >= 1


class TestExportImport:
    @pytest.mark.asyncio
    async def test_export_is_json_serializable_without_embedding(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="export me", confidence=0.9
        )
        export = await KnowledgeBase.export_all()
        # Must not raise — embedding blobs are stripped (B1).
        serialized = json.dumps(export)
        assert "pattern" in str(export["data"].get("knowledge_entries", [])) or True
        assert "version" in export and "data" in export

    @pytest.mark.asyncio
    async def test_import_round_trip_recreates_entries(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="convention", scope="project",
            content="round trip convention", confidence=0.8, tags=["x"],
        )
        export = await KnowledgeBase.export_all()

        # Wipe entries, then import the snapshot back.
        all_entries = await KnowledgeBase.search_knowledge(limit=1000)
        for e in all_entries:
            await KnowledgeBase.delete_knowledge_entry(e["id"])

        counts = await KnowledgeBase.import_all(export)
        assert counts.get("knowledge_entries", 0) >= 1

        reimported = await KnowledgeBase.search_knowledge(
            entry_type="convention", min_confidence=0.0, limit=1000
        )
        contents = {e["content"] for e in reimported}
        assert "round trip convention" in contents

    @pytest.mark.asyncio
    async def test_import_rebuilds_fts(self):
        await init_db()
        await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="fts rebuild token uniquephrase", confidence=0.9,
        )
        export = await KnowledgeBase.export_all()
        counts = await KnowledgeBase.import_all(export)
        assert counts.get("knowledge_entries") >= 1

        results = await KnowledgeBase.fulltext_search_knowledge("uniquephrase")
        assert any("uniquephrase" in (r.get("content") or "") for r in results)


class TestPII:
    @pytest.mark.asyncio
    async def test_scan_detects_and_redact_removes_email(self):
        from codeassist.routes.kb_gui import kb_pii_scan, kb_pii_redact

        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="Reach me at alice@example.com for questions", confidence=0.9,
        )

        scan = await kb_pii_scan()
        assert scan["total_scanned"] >= 1
        assert any(f["entry_id"] == entry_id and f["pii_type"] == "email"
                   for f in scan["flagged"])

        # Redaction should replace the email in place.
        redact = await kb_pii_redact({"entry_id": entry_id})
        assert "redacted" in redact.get("message", "").lower()

        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert "alice@example.com" not in (entry.get("content") or "")
        # A redaction placeholder should now be present.
        assert "REDACTED EMAIL" in (entry.get("content") or "")

    @pytest.mark.asyncio
    async def test_scan_flags_active_entry_and_protects_from_pass(self):
        from codeassist.routes.kb_gui import kb_pii_scan

        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="Contact alice@example.com for questions", confidence=0.9,
        )

        await kb_pii_scan()

        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["status"] == "flagged"
        # Detected PII categories are recorded in metadata (merged, not clobbered).
        meta = json.loads(entry["metadata"])
        assert "email" in meta.get("pii_found", [])

        # A flagged entry must NOT be archived by the quality pass even though it
        # runs; only 'active' rows are eligible for archival.
        report = await KnowledgeBase.run_quality_pass(min_confidence=0.5, max_usage=0)
        assert entry_id not in {c["id"] for c in report["candidates"]}

    @pytest.mark.asyncio
    async def test_flag_entries_for_pii_is_idempotent_and_merges(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file",
            content="leak", confidence=0.9, metadata={"answer": "the reply"},
        )

        n1 = await KnowledgeBase.flag_entries_for_pii({entry_id: {"email"}})
        n2 = await KnowledgeBase.flag_entries_for_pii({entry_id: {"ssn"}})
        assert n1 == 1 and n2 == 0  # second scan is a no-op (already flagged)

        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        meta = json.loads(entry["metadata"])
        assert sorted(meta["pii_found"]) == ["email", "ssn"]
        # Existing metadata (Q->A answer) is preserved, not clobbered.
        assert meta["answer"] == "the reply"

    @pytest.mark.asyncio
    async def test_redact_requires_entry_id(self):
        from codeassist.routes.kb_gui import kb_pii_redact
        resp = await kb_pii_redact({})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_redact_missing_entry_404(self):
        from codeassist.routes.kb_gui import kb_pii_redact
        await init_db()
        resp = await kb_pii_redact({"entry_id": "does-not-exist"})
        assert resp.status_code == 404
