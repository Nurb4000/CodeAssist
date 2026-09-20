"""Tests for session hook - summary generation, knowledge extraction."""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeassist.llm import TextDelta
from codeassist.session import Session, init_db
from codeassist.session_hook import SessionHook
from codeassist.knowledge import KnowledgeBase


@pytest.fixture
def session_hook():
    return SessionHook()




class TestCalculateStats:
    @pytest.mark.asyncio
    async def test_empty_messages(self, session_hook):
        stats = session_hook._calculate_stats([])
        assert stats["message_count"] == 0
        assert stats["tools_used"] == []
        assert stats["files_modified"] == []

    @pytest.mark.asyncio
    async def test_counts_roles(self, session_hook):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "content": "output", "tool_call_id": "c1"},
        ]
        stats = session_hook._calculate_stats(msgs)
        assert stats["message_count"] == 3
        assert stats["user_message_count"] == 1
        assert stats["assistant_message_count"] == 1
        assert stats["tool_call_count"] == 1

    @pytest.mark.asyncio
    async def test_extracts_tool_names(self, session_hook):
        msgs = [
            {"role": "assistant", "content": None,
             "tool_calls": json.dumps([
                 {"function": {"name": "read"}},
                 {"function": {"name": "write"}},
             ])},
        ]
        stats = session_hook._calculate_stats(msgs)
        assert "read" in stats["tools_used"]
        assert "write" in stats["tools_used"]

    @pytest.mark.asyncio
    async def test_handles_malformed_tool_calls(self, session_hook):
        msgs = [
            {"role": "assistant", "content": None,
             "tool_calls": "not valid json"},
        ]
        stats = session_hook._calculate_stats(msgs)
        assert stats["tools_used"] == []

    @pytest.mark.asyncio
    async def test_extracts_duration(self, session_hook):
        msgs = [
            {"role": "user", "content": "first", "created_at": "2025-01-01T00:00:00Z"},
            {"role": "assistant", "content": "last", "created_at": "2025-01-01T01:00:00Z"},
        ]
        stats = session_hook._calculate_stats(msgs)
        assert stats["duration_seconds"] == 3600


class TestExtractFilePaths:
    @pytest.mark.asyncio
    async def test_extracts_quoted_paths(self, session_hook):
        files = session_hook._extract_file_paths('Read "src/main.py"')
        assert "src/main.py" in files

    @pytest.mark.asyncio
    async def test_extracts_assignment_paths(self, session_hook):
        files = session_hook._extract_file_paths('path = "src/config.yaml"')
        assert "src/config.yaml" in files

    @pytest.mark.asyncio
    async def test_skips_non_tracked_extensions(self, session_hook):
        files = session_hook._extract_file_paths('file.pdf')
        assert "file.pdf" not in files

    @pytest.mark.asyncio
    async def test_empty_content(self, session_hook):
        files = session_hook._extract_file_paths("")
        assert files == set()

    @pytest.mark.asyncio
    async def test_handles_long_paths(self, session_hook):
        long_path = "a" * 300 + ".py"
        files = session_hook._extract_file_paths(long_path)
        assert long_path not in files


class TestGenerateSimpleSummary:
    @pytest.mark.asyncio
    async def test_generates_summary(self, session_hook):
        stats = {
            "first_user_message": "Implement login feature",
            "tools_used": ["read", "write", "shell"],
            "files_modified": ["src/auth.py", "tests/test_auth.py"],
            "duration_seconds": 600,
            "message_count": 15,
            "user_message_count": 5,
            "assistant_message_count": 5,
            "tool_call_count": 5,
        }
        result = session_hook._generate_simple_summary([], stats)
        assert "Implement login" in result["summary"]
        assert "read" in result["summary"] or "write" in result["summary"]
        assert "src/auth.py" in result["summary"] or "src" in result["summary"]

    @pytest.mark.asyncio
    async def test_empty_session(self, session_hook):
        stats = {
            "first_user_message": None,
            "tools_used": [],
            "files_modified": [],
            "duration_seconds": 0,
            "message_count": 0,
            "user_message_count": 0,
            "assistant_message_count": 0,
            "tool_call_count": 0,
        }
        result = session_hook._generate_simple_summary([], stats)
        assert "Empty session" in result["summary"]

    @pytest.mark.asyncio
    async def test_extracts_topics(self, session_hook):
        stats = {
            "first_user_message": "Fix database connection pool issue",
            "tools_used": [],
            "files_modified": [],
            "duration_seconds": 0,
            "message_count": 1,
            "user_message_count": 1,
            "assistant_message_count": 0,
            "tool_call_count": 0,
        }
        result = session_hook._generate_simple_summary([], stats)
        assert len(result["topics"]) > 0
        assert any("connection" in t or "database" in t for t in result["topics"])


class TestCalculateQualityScore:
    @pytest.mark.asyncio
    async def test_minimum_score(self, session_hook):
        score = session_hook._calculate_quality_score({
            "duration_seconds": 0,
            "message_count": 0,
            "tools_used": [],
            "files_modified": [],
        })
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_maximum_score(self, session_hook):
        score = session_hook._calculate_quality_score({
            "duration_seconds": 7200,
            "message_count": 30,
            "tools_used": ["read", "write", "shell", "git", "grep", "glob"],
            "files_modified": ["a.py", "b.py", "c.py", "d.py"],
        })
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_partial_score(self, session_hook):
        score = session_hook._calculate_quality_score({
            "duration_seconds": 300,
            "message_count": 8,
            "tools_used": ["read"],
            "files_modified": ["a.py"],
        })
        assert 0.2 < score < 0.8


class TestContentOverlap:
    def test_identical_content(self, session_hook):
        overlap = session_hook._content_overlap("hello world", "hello world")
        assert overlap == 1.0

    def test_partial_overlap(self, session_hook):
        overlap = session_hook._content_overlap("hello world foo", "hello world bar")
        assert overlap == 0.5

    def test_no_overlap(self, session_hook):
        overlap = session_hook._content_overlap("abc def", "ghi jkl")
        assert overlap == 0.0

    def test_empty_strings(self, session_hook):
        assert session_hook._content_overlap("", "hello") == 0.0
        assert session_hook._content_overlap("", "") == 0.0


class TestExtractSnippetAroundMatch:
    @pytest.mark.asyncio
    async def test_extracts_snippet(self, session_hook):
        content = "before " * 50 + "def test_function():" + " after" * 50
        snippet = session_hook._extract_snippet_around_match(
            content, r"def test_function\(\):", context_chars=20
        )
        assert snippet is not None
        assert "def test_function():" in snippet
        assert len(snippet.split("\n")) <= 10

    @pytest.mark.asyncio
    async def test_no_match(self, session_hook):
        snippet = session_hook._extract_snippet_around_match(
            "hello world", r"no match", context_chars=10
        )
        assert snippet is None


class TestClassifyAndCreateKnowledge:
    @pytest.mark.asyncio
    async def test_classifies_decision(self, session_hook):
        result = session_hook._classify_and_create_knowledge(
            "We decided to use FastAPI", "api_patterns", "s1"
        )
        assert result is not None
        assert "decision" in result["tags"]

    @pytest.mark.asyncio
    async def test_classifies_convention(self, session_hook):
        result = session_hook._classify_and_create_knowledge(
            "Files are organized in modules by feature", "config_patterns", "s1"
        )
        assert result is not None
        # "organized" matches convention indicators
        assert result["entry_type"] == "convention"

    @pytest.mark.asyncio
    async def test_classifies_bug_fix(self, session_hook):
        result = session_hook._classify_and_create_knowledge(
            "Fixed the bug by adding null check", "error_handling", "s1"
        )
        assert result is not None
        assert "bug_fix" in result["tags"]

    @pytest.mark.asyncio
    async def test_returns_none_for_irrelevant(self, session_hook):
        result = session_hook._classify_and_create_knowledge(
            "The weather is nice today", "api_patterns", "s1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_defaults_to_pattern(self, session_hook):
        result = session_hook._classify_and_create_knowledge(
            "def fetch_data(): pass", "async_patterns", "s1"
        )
        assert result is not None
        assert result["entry_type"] == "pattern"


class TestExtractTopicFromQuestion:
    def test_identifies_testing_topic(self, session_hook):
        topic = session_hook._extract_topic_from_question("How do I run pytest?")
        assert topic == "testing"

    def test_identifies_database_topic(self, session_hook):
        topic = session_hook._extract_topic_from_question("What's the SQL schema?")
        assert topic == "database"

    def test_identifies_api_topic(self, session_hook):
        topic = session_hook._extract_topic_from_question("How to create an API endpoint?")
        assert topic == "api"

    def test_identifies_error_topic(self, session_hook):
        topic = session_hook._extract_topic_from_question("Why am I getting this exception?")
        assert topic == "error_handling"

    def test_defaults_to_general(self, session_hook):
        topic = session_hook._extract_topic_from_question("What's your name?")
        assert topic == "general"


class TestAnalyzeShellCommand:
    def test_identifies_test_command(self, session_hook):
        result = session_hook._analyze_shell_command("pytest tests/ -v")
        assert result is not None
        assert "testing" in result["tags"]

    def test_identifies_git_command(self, session_hook):
        result = session_hook._analyze_shell_command("git commit -m 'fix'")
        assert result is not None
        assert "git" in result["tags"]

    def test_identifies_pip_command(self, session_hook):
        result = session_hook._analyze_shell_command("pip install requests")
        assert result is not None
        assert "dependencies" in result["tags"]

    def test_identifies_docker_command(self, session_hook):
        result = session_hook._analyze_shell_command("docker compose up")
        assert result is not None
        assert "docker" in result["tags"]

    def test_identifies_build_command(self, session_hook):
        result = session_hook._analyze_shell_command("npm run build")
        assert result is not None
        assert "build" in result["tags"]

    def test_returns_none_for_unknown(self, session_hook):
        result = session_hook._analyze_shell_command("echo hello")
        assert result is None


class TestOnSessionEnd:
    @pytest.mark.asyncio
    async def test_no_messages_does_nothing(self, session_hook):
        await init_db()
        session = await Session.create("Empty Session")
        # Should not raise
        await session_hook.on_session_end(session)

    @pytest.mark.asyncio
    async def test_creates_summary_for_normal_session(self, session_hook):
        await init_db()
        session = await Session.create("Test Session")
        await session.add_message("user", "Implement login feature")
        await session.add_message("assistant", "I'll help you with that")

        await session_hook.on_session_end(session)

        # Verify summary was created
        summary = await KnowledgeBase.get_session_summary(session.id)
        assert summary is not None
        assert "Implement login" in summary["summary"]

    @pytest.mark.asyncio
    async def test_extracts_knowledge_from_tool_calls(self, session_hook):
        await init_db()
        session = await Session.create("Tool Session")
        await session.add_message("user", "Read src/main.py and fix the bug")

        # Add assistant message with tool calls
        tc = [
            {"function": {"name": "read", "arguments": '{"file_path": "src/main.py"}'}},
            {"function": {"name": "write", "arguments": '{"file_path": "src/main.py", "content": "fixed"}'}},
        ]
        await session.add_message("assistant", content=None, tool_calls=tc)
        await session.add_message("tool", content="File read successfully", tool_call_id="call_1")
        await session.add_message("tool", content="File written successfully", tool_call_id="call_2")

        await session_hook.on_session_end(session)

        # Knowledge should have been extracted
        entries = await KnowledgeBase.search_knowledge(min_confidence=0.0)
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_extracts_from_shell_command(self, session_hook):
        await init_db()
        session = await Session.create("Shell Session")
        await session.add_message("user", "Run the tests")

        tc = [
            {"function": {"name": "shell", "arguments": '{"command": "pytest tests/ -v"}'}},
        ]
        await session.add_message("assistant", content=None, tool_calls=tc)

        await session_hook.on_session_end(session)

        entries = await KnowledgeBase.search_knowledge(min_confidence=0.0)
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_does_not_extract_knowledge_twice(self, session_hook):
        await init_db()
        session = await Session.create("Dedup Session")
        await session.add_message("user", "Fix the bug")
        tc = [
            {"function": {"name": "write", "arguments": '{"file_path": "test.py"}'}},
        ]
        await session.add_message("assistant", content=None, tool_calls=tc)
        await session.add_message("tool", content="Done", tool_call_id="call_1")

        await session_hook.on_session_end(session)
        await session_hook.on_session_end(session)

        # Calling twice should not raise, summary should exist
        summary = await KnowledgeBase.get_session_summary(session.id)
        assert summary is not None


class TestLLMSummary:
    @pytest.mark.asyncio
    async def test_generate_llm_summary(self):
        mock_llm = MagicMock()
        async def mock_stream(messages):
            yield TextDelta(content='{"summary": "LLM generated summary", "topics": ["python", "testing"], "goals": ["fix bug"]}')
        mock_llm.stream = mock_stream

        hook = SessionHook(llm_client=mock_llm)
        await init_db()
        await Session.create("LLM Session")

        result = await hook._generate_llm_summary(
            "session-1",
            [{"role": "user", "content": "Fix test suite"}],
            {"message_count": 1, "tools_used": [], "files_modified": [], "first_user_message": "Fix test suite"}
        )
        assert result["summary"] == "LLM generated summary"
        assert "python" in result["topics"]

    @pytest.mark.asyncio
    async def test_llm_summary_fallback_on_bad_json(self):
        mock_llm = MagicMock()
        async def mock_stream(messages):
            yield TextDelta(content="This is not JSON")
        mock_llm.stream = mock_stream

        hook = SessionHook(llm_client=mock_llm)
        await init_db()

        result = await hook._generate_llm_summary(
            "session-2",
            [{"role": "user", "content": "Hello"}],
            {"message_count": 1, "tools_used": [], "files_modified": []}
        )
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_on_session_end_with_llm(self):
        mock_llm = MagicMock()
        async def mock_stream(messages):
            yield TextDelta(content='{"summary": "LLM summary", "topics": ["test"], "goals": ["done"]}')
        mock_llm.stream = mock_stream

        hook = SessionHook(llm_client=mock_llm)
        await init_db()
        session = await Session.create("LLM Session End")
        await session.add_message("user", "Hello from LLM test")

        await hook.on_session_end(session)

        summary = await KnowledgeBase.get_session_summary(session.id)
        assert summary is not None


class TestExtractorIsolation:
    @pytest.mark.asyncio
    async def test_one_extractor_failure_does_not_skip_rest(self, session_hook):
        await init_db()
        session = await Session.create("G5 Isolation Session")

        called = []

        async def raising_tool_calls(session_id, messages):
            called.append("_extract_from_tool_calls")
            raise RuntimeError("boom")

        async def recording(name):
            async def _inner(session_id, messages):
                called.append(name)
                return []
            return _inner

        session_hook._extract_from_tool_calls = raising_tool_calls
        for name in (
            "_extract_from_code_patterns",
            "_extract_from_user_questions",
            "_extract_from_errors",
            "_extract_from_file_operations",
            "_detect_repetitive_patterns",
        ):
            setattr(session_hook, name, await recording(name))

        await session_hook._extract_knowledge(session.id, [], {})

        # The raising extractor still ran, and every other extractor ran too.
        assert "_extract_from_tool_calls" in called
        for name in (
            "_extract_from_code_patterns",
            "_extract_from_user_questions",
            "_extract_from_errors",
            "_extract_from_file_operations",
            "_detect_repetitive_patterns",
        ):
            assert name in called


class TestNearDuplicateMerge:
    @pytest.mark.asyncio
    async def test_similar_content_merges_not_duplicates(self, session_hook):
        await init_db()
        await session_hook._create_knowledge_if_new(
            entry_type="pattern", scope="file",
            content="Implement login feature with JWT tokens and refresh handler",
            source_session_id="s-merge-a", confidence=0.7, tags=["auth"],
        )
        # Nearly identical (one extra word) -> should merge into the first entry.
        await session_hook._create_knowledge_if_new(
            entry_type="pattern", scope="file",
            content="Implement login feature with JWT tokens and refresh handler now",
            source_session_id="s-merge-b", confidence=0.9, tags=["security"],
        )

        entries = await KnowledgeBase.search_knowledge(
            entry_type="pattern", scope="file", min_confidence=0.0
        )
        assert len(entries) == 1
        entry = entries[0]
        assert json.loads(entry["tags"]) == sorted(["auth", "security"])

    @pytest.mark.asyncio
    async def test_distinct_content_creates_separate_entry(self, session_hook):
        await init_db()
        await session_hook._create_knowledge_if_new(
            entry_type="pattern", scope="file",
            content="Implement login feature with JWT tokens",
            source_session_id="s-dist-a", confidence=0.7, tags=["auth"],
        )
        await session_hook._create_knowledge_if_new(
            entry_type="pattern", scope="file",
            content="Refactor the database migration script for postgres",
            source_session_id="s-dist-b", confidence=0.7, tags=["db"],
        )

        entries = await KnowledgeBase.search_knowledge(
            entry_type="pattern", scope="file", min_confidence=0.0
        )
        assert len(entries) == 2

