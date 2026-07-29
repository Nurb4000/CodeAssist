"""Tests for knowledge base CRUD, search, and analytics."""
import uuid
import pytest

from codeassist.knowledge import KnowledgeBase
from codeassist.session import Session, init_db


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
