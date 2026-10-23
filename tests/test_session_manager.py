"""Tests for session manager operations."""
import json
from pathlib import Path

import pytest

from codeassist.session import Session, init_db
from codeassist.session_manager import SessionManager
from codeassist.tools import ToolResult


class TestSessionManager:
    """Test session management operations."""

    @pytest.mark.asyncio
    async def test_fork_session(self):
        """Test forking a session."""
        await init_db()
        original = await Session.create(name="Original")
        
        # Add messages
        await original.add_message("user", "Question")
        await original.add_message("assistant", "Answer")
        
        # Fork
        forked = await SessionManager.fork_session(original.id, "Forked")
        
        assert forked.id != original.id
        forked_messages = await forked.get_messages()
        original_messages = await original.get_messages()
        
        assert len(forked_messages) == len(original_messages)

    @pytest.mark.asyncio
    async def test_export_session(self):
        """Test exporting session data."""
        await init_db()
        session = await Session.create(name="Export Test")
        
        await session.add_message("user", "Hello")
        await session.add_message("assistant", "Hi!")
        
        export_data = await SessionManager.export_session(session.id)
        
        assert "messages" in export_data
        assert len(export_data["messages"]) == 2
        assert export_data["session_id"] == session.id

    @pytest.mark.asyncio
    async def test_export_with_redaction(self):
        """Test exporting with PII redaction."""
        await init_db()
        session = await Session.create(name="Redact Test")
        
        # Add message with potential PII
        await session.add_message("user", "My API key is sk-1234567890abcdef")
        
        export_data = await SessionManager.export_session(session.id, redact=True)
        
        # Check that PII is redacted
        user_msg = export_data["messages"][0]
        assert "sk-1234567890abcdef" not in user_msg["content"]
        assert "***REDACTED***" in user_msg["content"]

    @pytest.mark.asyncio
    async def test_export_bundle_lands_in_workspace(self, tmp_path):
        """A share/bundle export must be written under the workspace, not CWD.

        In Docker the process CWD is the app tree (/app), so a CWD-relative
        export would land outside the mounted project and vanish on rebuild.
        """
        await init_db()
        session = await Session.create(name="Bundle Test")
        await session.add_message("user", "Hello")

        workspace = tmp_path / "my-project"
        workspace.mkdir()

        result = await SessionManager.export_session(
            session.id, redact=True, as_bundle=True, workspace=workspace,
        )

        assert isinstance(result, Path)
        assert result.parent == workspace / "runtime" / "exports"
        assert result.is_file()

    @pytest.mark.asyncio
    async def test_import_session(self):
        """Test importing session data."""
        await init_db()
        
        # Create source session
        source = await Session.create(name="Source")
        await source.add_message("user", "Original message")
        
        # Export
        export_data = await SessionManager.export_session(source.id)
        
        # Import to new session
        imported = await SessionManager.import_session(export_data, "Imported")
        
        messages = await imported.get_messages()
        assert len(messages) == 1
        assert messages[0]["content"] == "Original message"

    @pytest.mark.asyncio
    async def test_export_import_preserves_reasoning_content(self):
        """reasoning_content must survive an export->import round trip so a
        reasoning model's thinking block is not lost when sharing sessions."""
        await init_db()
        source = await Session.create(name="Reasoning Source")
        await source.add_message("user", "Question")
        await source.add_message(
            "assistant",
            content="The answer",
            reasoning_content="Let me think about this step by step.",
        )

        export_data = await SessionManager.export_session(source.id)
        imported = await SessionManager.import_session(export_data, "Reasoning Import")

        messages = await imported.get_messages()
        imported_assistant = next(m for m in messages if m["content"] == "The answer")
        assert imported_assistant["reasoning_content"] == "Let me think about this step by step."

    @pytest.mark.asyncio
    async def test_get_session_summary(self):
        """Test getting session summary."""
        await init_db()
        session = await Session.create(name="Summary Test")
        
        await session.add_message("user", "Question 1")
        await session.add_message("assistant", "Answer 1")
        await session.add_message("user", "Question 2")
        
        summary = await SessionManager.get_session_summary(session.id)
        
        assert summary["id"] == session.id
        assert summary["message_count"] == 3
        assert summary["user_message_count"] == 2
        assert summary["assistant_message_count"] == 1

    @pytest.mark.asyncio
    async def test_session_tool_fork(self):
        """Test session tool fork action."""
        await init_db()
        session = await Session.create(name="Tool Test")
        await session.add_message("user", "Test")
        
        from codeassist.session_manager import SessionTool
        tool = SessionTool(session.id)
        
        result = await tool.execute(action="fork", name="Forked via Tool")
        
        # A ToolResult, not a bare str: the agent reads `result.output`.
        assert isinstance(result, ToolResult)
        assert "Forked session" in result.output or "New session ID" in result.output
        assert result.error is False

    @pytest.mark.asyncio
    async def test_session_tool_export(self):
        """Test session tool export action."""
        await init_db()
        session = await Session.create(name="Export Tool Test")
        await session.add_message("user", "Test message")
        
        from codeassist.session_manager import SessionTool
        tool = SessionTool(session.id)
        
        result = await tool.execute(action="export")
        
        # Result should be JSON
        assert isinstance(result, ToolResult)
        data = json.loads(result.output)
        assert "messages" in data

    @pytest.mark.asyncio
    async def test_session_tool_summary(self):
        """Test session tool summary action."""
        await init_db()
        session = await Session.create(name="Summary Tool Test")
        await session.add_message("user", "Test")
        
        from codeassist.session_manager import SessionTool
        tool = SessionTool(session.id)
        
        result = await tool.execute(action="summary")
        
        assert isinstance(result, ToolResult)
        data = json.loads(result.output)
        assert "message_count" in data

    def test_session_tool_schema(self):
        """Test session tool schema."""
        from codeassist.session_manager import SessionTool
        tool = SessionTool("test-id")
        
        schema = tool.schema()
        assert schema["name"] == "session"
        assert "description" in schema
        assert "parameters" in schema


class TestSessionToolContextIsolation:
    """Regression tests for per-turn session binding (ContextVar isolation).

    ``SessionTool`` is a single process-wide instance; ``execute()`` falls back
    to a shared ``current_session_id`` when the model omits ``session_id``.
    Without a turn-bound ContextVar, two sessions streaming concurrently would
    resolve that fallback to whichever connection bound last and fork/export/
    summary/share the wrong conversation. The turn binds its own session via
    :meth:`SessionTool.active` (mirroring the ``todo`` tool), so each resolves
    its own id. See §15.21 / §2 fragile item 2.
    """

    @pytest.mark.asyncio
    async def test_resolve_session_precedence(self):
        from codeassist.session_manager import SessionTool

        tool = SessionTool(current_session_id="sess-shared")

        # An explicit arg wins over the binding and the shared fallback.
        assert tool._resolve_session("sess-explicit") == "sess-explicit"
        # Otherwise the turn-bound ContextVar wins over the shared fallback.
        with tool.active("sess-a"):
            assert tool._resolve_session(None) == "sess-a"
            # ...but an explicit arg still wins inside a binding.
            assert tool._resolve_session("sess-b") == "sess-b"

    @pytest.mark.asyncio
    async def test_interleaved_turns_target_their_own_session(self, monkeypatch):
        from types import SimpleNamespace

        from codeassist.session_manager import SessionManager, SessionTool

        tool = SessionTool(current_session_id="sess-shared")

        seen = []

        async def _fake_fork(session_id, name=None):
            seen.append(session_id)
            return SimpleNamespace(id="new-" + session_id)

        monkeypatch.setattr(SessionManager, "fork_session", _fake_fork)

        # Simulate interleaving: A's turn begins, then B's starts and finishes
        # entirely inside A's, then A resumes. With a shared attribute both forks
        # would target "sess-shared" (or whichever bound last), not their own id.
        with tool.active("sess-a"):
            with tool.active("sess-b"):
                await tool.execute(action="fork")
            await tool.execute(action="fork")

        assert seen == ["sess-b", "sess-a"], f"wrong session targeted: {seen}"

    @pytest.mark.asyncio
    async def test_binding_released_after_turn(self, monkeypatch):
        from types import SimpleNamespace

        from codeassist.session_manager import SessionManager, SessionTool

        tool = SessionTool(current_session_id="sess-shared")

        seen = []

        async def _fake_fork(session_id, name=None):
            seen.append(session_id)
            return SimpleNamespace(id="x")

        monkeypatch.setattr(SessionManager, "fork_session", _fake_fork)

        with tool.active("sess-a"):
            await tool.execute(action="fork")
        # After the turn the ContextVar is reset; a bare call falls back to the
        # shared per-connect id, not the finished turn's session.
        await tool.execute(action="fork")

        assert seen == ["sess-a", "sess-shared"], f"binding leaked into later turn: {seen}"
