"""Tests for session management."""
import asyncio
import pytest

from codeassist.session import Session, init_db


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

    @pytest.mark.asyncio
    async def test_add_message_with_attachments(self):
        """Test adding a message with image attachments."""
        await init_db()
        session = await Session.create()

        attachments = [
            {
                "attachment_type": "image",
                "mime_type": "image/png",
                "file_name": "diagram.png",
                "data": "data:image/png;base64,iVBORw0KGgo=",
            }
        ]

        msg_id = await session.add_message("user", "Look at this", attachments=attachments)
        assert msg_id is not None

        messages = await session.get_messages()
        assert len(messages) == 1
        assert messages[0]["content"] == "Look at this"
        assert messages[0]["attachments"] == attachments

    @pytest.mark.asyncio
    async def test_fork_session_preserves_attachments(self):
        """Test that forking a session copies attachments."""
        await init_db()
        original = await Session.create(name="Original")

        attachments = [
            {
                "attachment_type": "image",
                "mime_type": "image/jpeg",
                "file_name": "shot.jpg",
                "data": "data:image/jpeg;base64,/9j/4AAQ",
            }
        ]
        await original.add_message("user", "Check this", attachments=attachments)

        forked = await original.fork(name="Forked")

        original_messages = await original.get_messages()
        forked_messages = await forked.get_messages()

        assert len(forked_messages) == len(original_messages)
        assert forked_messages[0]["attachments"] == original_messages[0]["attachments"]

    @pytest.mark.asyncio
    async def test_delete_session_removes_attachments(self):
        """Test that deleting a session cleans up attachments."""
        await init_db()
        session = await Session.create()

        await session.add_message(
            "user",
            "With image",
            attachments=[{
                "attachment_type": "image",
                "mime_type": "image/png",
                "file_name": "pic.png",
                "data": "data:image/png;base64,abc123",
            }],
        )

        await session.delete()

        sessions = await Session.list_all()
        assert len(sessions) == 0

        import sqlite3
        from codeassist.session import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM message_attachments"
            ).fetchone()[0]
            assert count == 0
        finally:
            conn.close()


class TestAgentSelection:
    """Per-session agent selection persistence (review item I / agent switcher)."""

    @pytest.mark.asyncio
    async def test_set_and_get_agent_name_round_trip(self):
        await init_db()
        session = await Session.create(name="Agent Pick")
        assert await session.get_agent_name() is None

        await session.set_agent_name("research")
        assert await session.get_agent_name() == "research"

        await session.set_agent_name("default")
        assert await session.get_agent_name() == "default"

    @pytest.mark.asyncio
    async def test_agent_name_is_per_session(self):
        await init_db()
        s1 = await Session.create(name="One")
        s2 = await Session.create(name="Two")

        await s1.set_agent_name("default")
        assert await s1.get_agent_name() == "default"
        assert await s2.get_agent_name() is None


class TestAutoTitle:
    """Sessions with untouched timestamp names get auto-titled from the first user message."""

    @pytest.mark.asyncio
    async def test_first_user_message_replaces_default_title(self):
        from codeassist.session import is_default_title
        await init_db()
        s = await Session.create()
        sessions = await Session.list_all()
        original = next(s_ for s_ in sessions if s_["id"] == s.id)["name"]
        assert is_default_title(original)

        await s.add_message("user", "Fix the login timeout bug")
        sessions = await Session.list_all()
        title = next(s_ for s_ in sessions if s_["id"] == s.id)["name"]
        assert title == "Fix the login timeout bug"

    @pytest.mark.asyncio
    async def test_second_user_message_does_not_retitle(self):
        from codeassist.session import is_default_title
        await init_db()
        s = await Session.create()
        await s.add_message("user", "Refactor auth module")
        await s.add_message("user", "Add test coverage")
        sessions = await Session.list_all()
        title = next(s_ for s_ in sessions if s_["id"] == s.id)["name"]
        assert title == "Refactor auth module"

    @pytest.mark.asyncio
    async def test_renamed_session_not_overwritten(self):
        await init_db()
        s = await Session.create(name="My Project")
        await s.add_message("user", "Help me build a CLI")
        sessions = await Session.list_all()
        title = next(s_ for s_ in sessions if s_["id"] == s.id)["name"]
        assert title == "My Project"

    @pytest.mark.asyncio
    async def test_long_title_truncated_at_word(self):
        from codeassist.session import derive_title
        long = "Investigate the strange race condition in the async worker pool" * 2
        title = derive_title(long)
        assert len(title) <= 62
        assert title.endswith('…')
        assert not title.endswith(' ')


class TestPinning:
    """Pin/unpin sessions and pinned-first ordering."""

    @pytest.mark.asyncio
    async def test_set_pinned(self):
        await init_db()
        s = await Session.create(name="Pin me")
        await Session.set_pinned(s.id, True)
        sessions = await Session.list_all()
        row = next(s_ for s_ in sessions if s_["id"] == s.id)
        assert row["is_pinned"] == 1

    @pytest.mark.asyncio
    async def test_pinned_sessions_sort_first(self):
        import asyncio
        await init_db()
        s1 = await Session.create(name="First")
        await asyncio.sleep(0.05)
        s2 = await Session.create(name="Second")
        await Session.set_pinned(s2.id, True)
        sessions = await Session.list_all()
        ids = [s_["id"] for s_ in sessions]
        assert ids.index(s2.id) < ids.index(s1.id)

    @pytest.mark.asyncio
    async def test_list_all_includes_summary_field(self):
        await init_db()
        s = await Session.create(name="Summary test")
        sessions = await Session.list_all()
        row = next(s_ for s_ in sessions if s_["id"] == s.id)
        assert "summary" in row
        assert row["summary"] is None
