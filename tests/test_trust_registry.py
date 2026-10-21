"""Tests for trust registry."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codeassist.trust_registry import TrustRegistry, TrustRequest, TrustStatus


class TestTrustRegistry:
    """Test trust registry functionality."""

    def test_init_default_path(self, tmp_path):
        """Test initialization with default path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / ".codeassist" / "trusted_tools.json"
            registry = TrustRegistry(registry_path=registry_path)
            assert registry.registry_path == registry_path

    def test_compute_hash(self, tmp_path):
        """Test hash computation."""
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        hash1 = TrustRegistry.compute_hash(test_file)
        hash2 = TrustRegistry.compute_hash(test_file)
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex digest length

    def test_compute_hash_different_files(self, tmp_path):
        """Test that different files have different hashes."""
        file1 = tmp_path / "file1.py"
        file2 = tmp_path / "file2.py"
        
        file1.write_text("print('hello')")
        file2.write_text("print('world')")
        
        hash1 = TrustRegistry.compute_hash(file1)
        hash2 = TrustRegistry.compute_hash(file2)
        
        assert hash1 != hash2

    def test_check_trust_new_file(self, tmp_path):
        """Test checking trust for a new file."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        status = registry.check_trust(test_file, file_type="tool")
        assert status == TrustStatus.PENDING_APPROVAL

    def test_check_trust_untrusted_file(self, tmp_path):
        """Test checking trust for an untrusted file."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        # Manually add to registry as untrusted
        registry._registry[str(test_file.resolve())] = {
            "hash": TrustRegistry.compute_hash(test_file),
            "approved": False,
        }
        
        status = registry.check_trust(test_file, file_type="tool")
        assert status == TrustStatus.UNTRUSTED

    def test_check_trust_trusted_file(self, tmp_path):
        """Test checking trust for a trusted file."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        # Manually add to registry as trusted
        registry._registry[str(test_file.resolve())] = {
            "hash": TrustRegistry.compute_hash(test_file),
            "approved": True,
        }
        
        status = registry.check_trust(test_file, file_type="tool")
        assert status == TrustStatus.TRUSTED

    def test_check_trust_nonexistent_file(self, tmp_path):
        """Test checking trust for a non-existent file."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        nonexistent = tmp_path / "nonexistent.py"
        status = registry.check_trust(nonexistent, file_type="tool")
        assert status == TrustStatus.UNTRUSTED

    def test_approve_file(self, tmp_path):
        """Test approving a file."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        # First check to create pending request
        registry.check_trust(test_file, file_type="tool")
        
        # Approve
        registry.approve(test_file)
        
        # Check again - should be trusted
        status = registry.check_trust(test_file, file_type="tool")
        assert status == TrustStatus.TRUSTED

    def test_reject_file(self, tmp_path):
        """Test rejecting a file."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        # First check to create pending request
        registry.check_trust(test_file, file_type="tool")
        
        # Reject
        registry.reject(test_file)
        
        # Check again - should be untrusted
        status = registry.check_trust(test_file, file_type="tool")
        assert status == TrustStatus.UNTRUSTED

    def test_is_trusted(self, tmp_path):
        """Test is_trusted shorthand."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        # Not trusted by default
        assert not registry.is_trusted(test_file)
        
        # Approve and check
        registry.approve(test_file)
        assert registry.is_trusted(test_file)

    def test_pending_requests(self, tmp_path):
        """Test pending requests tracking."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        file1 = tmp_path / "file1.py"
        file2 = tmp_path / "file2.py"
        
        file1.write_text("print('hello')")
        file2.write_text("print('world')")
        
        registry.check_trust(file1, file_type="tool")
        registry.check_trust(file2, file_type="tool")
        
        pending = registry.get_pending_requests()
        assert len(pending) == 2

    def test_clear_pending(self, tmp_path):
        """Test clearing pending requests."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        registry.check_trust(test_file, file_type="tool")
        assert len(registry.get_pending_requests()) == 1
        
        registry.clear_pending(test_file)
        assert len(registry.get_pending_requests()) == 0

    def test_list_trusted(self, tmp_path):
        """Test listing trusted files."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        file1 = tmp_path / "file1.py"
        file2 = tmp_path / "file2.py"
        
        file1.write_text("print('hello')")
        file2.write_text("print('world')")
        
        registry.approve(file1)
        registry.check_trust(file2, file_type="tool")  # Pending, not approved
        
        trusted = registry.list_trusted()
        assert len(trusted) == 1
        assert trusted[0]["path"] == str(file1.resolve())

    def test_approval_callback(self, tmp_path):
        """Test approval callback."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        callback = MagicMock()
        registry.set_approval_callback(callback)
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        registry.check_trust(test_file, file_type="tool")
        
        callback.assert_called_once()
        call_args = callback.call_args[0]
        assert isinstance(call_args[0], TrustRequest)
        assert call_args[1] == False  # approved=False

    def test_hash_change_detected(self, tmp_path):
        """Test that hash changes are detected."""
        registry_path = tmp_path / "registry.json"
        registry = TrustRegistry(registry_path=registry_path)
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        # Approve first
        registry.approve(test_file)
        assert registry.is_trusted(test_file)
        
        # Modify file
        test_file.write_text("print('world')")
        
        # Should now be pending approval
        status = registry.check_trust(test_file, file_type="tool")
        assert status == TrustStatus.PENDING_APPROVAL

    def test_registry_persistence(self, tmp_path):
        """Test that registry persists to disk."""
        registry_path = tmp_path / "registry.json"
        
        # Create and approve a file
        registry1 = TrustRegistry(registry_path=registry_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        registry1.approve(test_file)
        
        # Load registry again
        registry2 = TrustRegistry(registry_path=registry_path)
        assert registry2.is_trusted(test_file)

    def test_trust_request_to_dict(self):
        """Test TrustRequest serialization."""
        request = TrustRequest(
            file_path="/path/to/file.py",
            hash="abc123",
            file_type="tool",
        )
        
        d = request.to_dict()
        assert d["file_path"] == "/path/to/file.py"
        assert d["hash"] == "abc123"
        assert d["file_type"] == "tool"
        assert "timestamp" in d


class TestServerTrustCallback:
    """The server's trust-approval callback must be synchronous (TrustRegistry
    calls it without awaiting) and actually broadcast, and must be registered
    once per registry rather than re-set on every WebSocket connect."""

    @pytest.mark.asyncio
    async def test_callback_broadcasts_approval_request(self, tmp_path, monkeypatch):
        import asyncio

        from codeassist import server

        sent = []

        async def fake_broadcast(message, session_id=None):
            sent.append((message, session_id))

        monkeypatch.setattr(server, "broadcast_message", fake_broadcast)

        registry = TrustRegistry(registry_path=tmp_path / "registry.json")
        server._register_trust_callback(registry)

        # A synchronous callback: TrustRegistry.check_trust calls it directly.
        import inspect
        assert not inspect.iscoroutinefunction(registry._approval_callback)

        target = tmp_path / "tool.py"
        target.write_text("print('hi')")
        assert registry.check_trust(target, file_type="custom_tool") == TrustStatus.PENDING_APPROVAL

        # The scheduled broadcast actually runs (this is the regression: the old
        # async callback's coroutine was dropped and never awaited).
        await asyncio.sleep(0.05)
        assert len(sent) == 1
        message, session_id = sent[0]
        assert message["type"] == "trust_approval_required"
        assert message["file_path"] == str(target.resolve())
        # No connection context in this unit test, so the broadcast is not scoped
        # to a session (falls back to workspace-global delivery).
        assert session_id is None

    @pytest.mark.asyncio
    async def test_approved_flag_suppresses_broadcast(self, tmp_path, monkeypatch):
        import asyncio

        from codeassist import server

        sent = []

        async def fake_broadcast(message):
            sent.append(message)

        monkeypatch.setattr(server, "broadcast_message", fake_broadcast)

        registry = TrustRegistry(registry_path=tmp_path / "registry.json")
        server._register_trust_callback(registry)

        registry._approval_callback(None, True)
        await asyncio.sleep(0.05)
        assert sent == []


class TestWebSocketBroadcastScoping:
    """Server-side broadcast delivery must be scoped per session and only reach
    registered (authenticated) sockets (review item §1.14)."""

    class _FakeWS:
        def __init__(self, sid):
            self.sid = sid
            self.sent = []

        async def send_json(self, message):
            self.sent.append(message)

    @pytest.mark.asyncio
    async def test_scoped_broadcast_reaches_only_that_session(self):
        import asyncio

        from codeassist import server

        a = self._FakeWS("A")
        b = self._FakeWS("B")
        server.register_websocket(a, "A")
        server.register_websocket(b, "B")

        try:
            await server.broadcast_message({"type": "prompt"}, session_id="A")
            assert len(a.sent) == 1
            assert b.sent == [], "session B must not receive session A's prompt"
        finally:
            server.unregister_websocket(a)
            server.unregister_websocket(b)

    @pytest.mark.asyncio
    async def test_unscoped_broadcast_reaches_all_registered(self):
        import asyncio

        from codeassist import server

        a = self._FakeWS("A")
        b = self._FakeWS("B")
        server.register_websocket(a, "A")
        server.register_websocket(b, "B")

        try:
            await server.broadcast_message({"type": "global"})
            assert len(a.sent) == 1
            assert len(b.sent) == 1
        finally:
            server.unregister_websocket(a)
            server.unregister_websocket(b)

    @pytest.mark.asyncio
    async def test_scoped_broadcast_to_unknown_session_sends_nothing(self):
        import asyncio

        from codeassist import server

        a = self._FakeWS("A")
        server.register_websocket(a, "A")
        try:
            await server.broadcast_message({"type": "x"}, session_id="nope")
            assert a.sent == []
        finally:
            server.unregister_websocket(a)

    @pytest.mark.asyncio
    async def test_unregister_drops_socket_from_broadcasts(self):
        import asyncio

        from codeassist import server

        a = self._FakeWS("A")
        server.register_websocket(a, "A")
        server.unregister_websocket(a)
        assert a not in server._active_websockets
        assert a not in server._ws_sessions
        await server.broadcast_message({"type": "z"}, session_id="A")
        assert a.sent == []
