"""Tests for session manager operations."""
import json
import pytest

from session import Session, init_db
from session_manager import SessionManager


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
        
        from session_manager import SessionTool
        tool = SessionTool(session.id)
        
        result = await tool.execute(action="fork", name="Forked via Tool")
        
        assert "Forked session" in result or "New session ID" in result

    @pytest.mark.asyncio
    async def test_session_tool_export(self):
        """Test session tool export action."""
        await init_db()
        session = await Session.create(name="Export Tool Test")
        await session.add_message("user", "Test message")
        
        from session_manager import SessionTool
        tool = SessionTool(session.id)
        
        result = await tool.execute(action="export")
        
        # Result should be JSON
        data = json.loads(result)
        assert "messages" in data

    @pytest.mark.asyncio
    async def test_session_tool_summary(self):
        """Test session tool summary action."""
        await init_db()
        session = await Session.create(name="Summary Tool Test")
        await session.add_message("user", "Test")
        
        from session_manager import SessionTool
        tool = SessionTool(session.id)
        
        result = await tool.execute(action="summary")
        
        data = json.loads(result)
        assert "message_count" in data

    def test_session_tool_schema(self):
        """Test session tool schema."""
        from session_manager import SessionTool
        tool = SessionTool("test-id")
        
        schema = tool.schema()
        assert schema["name"] == "session"
        assert "description" in schema
        assert "parameters" in schema
