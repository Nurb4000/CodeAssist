"""Tests for trust registry."""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codeassist.trust_registry import TrustRegistry, TrustStatus, TrustRequest


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
