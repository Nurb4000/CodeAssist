"""Tests for session management."""
import asyncio
import pytest

from session import Session, init_db


class TestSession:
    """Test session CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_session(self):
        """Test creating a new session."""
        await init_db()
        session = await Session.create()
        
        assert session.id is not None
        assert len(session.id) > 0

    @pytest.mark.asyncio
    async def test_create_session_with_name(self):
        """Test creating a session with a custom name."""
        await init_db()
        session = await Session.create(name="Test Session")
        
        sessions = await Session.list_all()
        matching = [s for s in sessions if s["id"] == session.id]
        assert len(matching) == 1
        assert matching[0]["name"] == "Test Session"

    @pytest.mark.asyncio
    async def test_list_sessions(self):
        """Test listing all sessions."""
        await init_db()
        
        # Create multiple sessions
        session1 = await Session.create(name="Session 1")
        session2 = await Session.create(name="Session 2")
        
        sessions = await Session.list_all()
        assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_get_or_create_latest(self):
        """Test getting the latest session or creating one."""
        await init_db()
        
        # No sessions exist
        session = await Session.get_or_create_latest()
        assert session.id is not None
        
        # Create another session
        session2 = await Session.create(name="Newest")
        latest = await Session.get_or_create_latest()
        assert latest.id == session2.id

    @pytest.mark.asyncio
    async def test_add_message(self):
        """Test adding a message to a session."""
        await init_db()
        session = await Session.create()
        
        msg_id = await session.add_message("user", "Hello")
        assert msg_id is not None
        
        messages = await session.get_messages()
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_add_multiple_messages(self):
        """Test adding multiple messages."""
        await init_db()
        session = await Session.create()
        
        await session.add_message("user", "Hello")
        await session.add_message("assistant", "Hi!")
        await session.add_message("user", "How are you?")
        
        messages = await session.get_messages()
        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_add_message_with_tool_calls(self):
        """Test adding a message with tool calls."""
        await init_db()
        session = await Session.create()
        
        tool_calls = [
            {
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "read",
                    "arguments": '{"file_path": "test.py"}'
                }
            }
        ]
        
        msg_id = await session.add_message(
            "assistant",
            content="Let me read that file.",
            tool_calls=tool_calls
        )
        
        messages = await session.get_messages()
        assert len(messages) == 1
        assert messages[0]["tool_calls"] is not None

    @pytest.mark.asyncio
    async def test_add_message_with_tool_result(self):
        """Test adding a tool result message."""
        await init_db()
        session = await Session.create()
        
        msg_id = await session.add_message(
            "tool",
            content="File contents here...",
            tool_call_id="call_123"
        )
        
        messages = await session.get_messages()
        assert len(messages) == 1
        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "call_123"

    @pytest.mark.asyncio
    async def test_rename_session(self):
        """Test renaming a session."""
        await init_db()
        session = await Session.create(name="Original Name")
        
        await session.rename("New Name")
        
        sessions = await Session.list_all()
        matching = [s for s in sessions if s["id"] == session.id]
        assert len(matching) == 1
        assert matching[0]["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_delete_session(self):
        """Test deleting a session."""
        await init_db()
        session = await Session.create(name="To Delete")
        
        # Add some messages
        await session.add_message("user", "Test message")
        
        # Delete the session
        await session.delete()
        
        # Verify session is gone
        sessions = await Session.list_all()
        assert len(sessions) == 0
        
        # Verify messages are gone
        messages = await session.get_messages()
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_fork_session(self):
        """Test forking a session."""
        await init_db()
        original = await Session.create(name="Original")
        
        # Add messages to original
        await original.add_message("user", "Question 1")
        await original.add_message("assistant", "Answer 1")
        await original.add_message("user", "Question 2")
        
        # Fork the session
        forked = await original.fork(name="Forked Session")
        
        # Verify fork has same messages
        original_messages = await original.get_messages()
        forked_messages = await forked.get_messages()
        
        assert len(forked_messages) == len(original_messages)
        assert forked.id != original.id

    @pytest.mark.asyncio
    async def test_session_updated_at(self):
        """Test that updated_at is set on message add."""
        await init_db()
        session = await Session.create(name="Test")
        
        # Get initial updated_at
        sessions = await Session.list_all()
        initial_updated = [s for s in sessions if s["id"] == session.id][0]["updated_at"]
        
        # Add a message
        await session.add_message("user", "New message")
        
        # Check updated_at changed
        sessions = await Session.list_all()
        new_updated = [s for s in sessions if s["id"] == session.id][0]["updated_at"]
        
        assert new_updated != initial_updated
