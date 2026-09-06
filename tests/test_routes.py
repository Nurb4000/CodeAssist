"""Tests for tool management API routes."""
from unittest.mock import AsyncMock, MagicMock, patch

import base64
import pytest
from fastapi.testclient import TestClient

from codeassist.routes.tools import router


def make_png_data_url():
    return "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfakepixels").decode()


class TestParseImageAttachments:
    def test_empty(self):
        from codeassist.server import _parse_image_attachments
        attachments, error = _parse_image_attachments([])
        assert attachments == []
        assert error is None

    def test_valid_image(self):
        from codeassist.server import _parse_image_attachments
        data_url = make_png_data_url()
        attachments, error = _parse_image_attachments([data_url])
        assert error is None
        assert len(attachments) == 1
        assert attachments[0]["mime_type"] == "image/png"
        assert attachments[0]["data"] == data_url

    def test_too_many_images(self):
        from codeassist.server import _parse_image_attachments
        data_url = make_png_data_url()
        attachments, error = _parse_image_attachments([data_url] * 5)
        assert attachments == []
        assert error is not None
        assert "Maximum" in error

    def test_invalid_data_url(self):
        from codeassist.server import _parse_image_attachments
        attachments, error = _parse_image_attachments(["not a data url"])
        assert attachments == []
        assert error is not None

    def test_unsupported_mime(self):
        from codeassist.server import _parse_image_attachments
        attachments, error = _parse_image_attachments(
            ["data:image/bmp;base64," + base64.b64encode(b"BMfake").decode()]
        )
        assert attachments == []
        assert error is not None
        assert "Unsupported image type" in error

    def test_oversized_image(self):
        from codeassist.server import _parse_image_attachments, MAX_IMAGE_BYTES
        big = b"x" * (MAX_IMAGE_BYTES + 1)
        attachments, error = _parse_image_attachments(["data:image/png;base64," + base64.b64encode(big).decode()])
        assert attachments == []
        assert error is not None
        assert "too large" in error


@pytest.fixture
def client():
    """Create a test client."""
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestTrustEndpoints:
    """Test trust management endpoints."""

    def test_get_pending_trust_requests_no_registry(self, client):
        """Test getting pending requests when no registry exists."""
        with patch("codeassist.server.get_trust_registry") as mock_get:
            mock_get.return_value = None
            
            response = client.get("/api/tools/trust/pending")
            
            assert response.status_code == 200
            data = response.json()
            assert data["pending"] == []

    def test_get_pending_trust_requests_with_registry(self, client):
        """Test getting pending requests with registry."""
        mock_request1 = MagicMock()
        mock_request1.to_dict.return_value = {"file_path": "/test/tool1.py", "hash": "abc123"}
        
        mock_request2 = MagicMock()
        mock_request2.to_dict.return_value = {"file_path": "/test/tool2.py", "hash": "def456"}
        
        mock_registry = MagicMock()
        mock_registry.get_pending_requests.return_value = [mock_request1, mock_request2]
        
        with patch("codeassist.server.get_trust_registry") as mock_get:
            mock_get.return_value = mock_registry
            
            response = client.get("/api/tools/trust/pending")
            
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 2
            assert len(data["pending"]) == 2
            assert data["pending"][0]["file_path"] == "/test/tool1.py"

    def test_get_trusted_tools_no_registry(self, client):
        """Test getting trusted tools when no registry exists."""
        with patch("codeassist.server.get_trust_registry") as mock_get:
            mock_get.return_value = None
            
            response = client.get("/api/tools/trust/trusted")
            
            assert response.status_code == 200
            data = response.json()
            assert data["trusted"] == []

    def test_get_trusted_tools_with_registry(self, client):
        """Test getting trusted tools with registry."""
        mock_registry = MagicMock()
        mock_registry.list_trusted.return_value = [
            {"file_path": "/test/tool1.py", "hash": "abc123"},
            {"file_path": "/test/tool2.py", "hash": "def456"},
        ]
        
        with patch("codeassist.server.get_trust_registry") as mock_get:
            mock_get.return_value = mock_registry
            
            response = client.get("/api/tools/trust/trusted")
            
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 2
            assert len(data["trusted"]) == 2
            assert data["trusted"][0]["file_path"] == "/test/tool1.py"


class TestToolEndpoints:
    """Test tool management endpoints."""

    def test_reload_tools_success(self, client):
        """Test successful tool reload."""
        with patch("codeassist.server.reload_all_tools") as mock_reload:
            mock_reload.return_value = None
            
            response = client.post("/api/tools/reload")
            
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] == True
            assert "successfully" in data["message"]

    def test_reload_tools_failure(self, client):
        """Test tool reload failure."""
        with patch("codeassist.server.reload_all_tools") as mock_reload:
            mock_reload.side_effect = Exception("Reload failed")
            
            response = client.post("/api/tools/reload")
            
            assert response.status_code == 500
            data = response.json()
            assert "Failed to reload" in data["detail"]

    def test_list_tools_no_registry(self, client):
        """Test listing tools when no registry exists."""
        with patch("codeassist.server.tools", None):
            response = client.get("/api/tools/list")
            
            assert response.status_code == 200
            data = response.json()
            assert data["tools"] == []

    def test_list_tools_with_registry(self, client):
        """Test listing tools with registry."""
        mock_tools = MagicMock()
        mock_tools.list_names.return_value = ["read", "write", "shell"]
        
        with patch("codeassist.server.tools", mock_tools):
            response = client.get("/api/tools/list")
            
            assert response.status_code == 200
            data = response.json()
            assert len(data["tools"]) == 3
            assert "read" in data["tools"]
            assert "write" in data["tools"]
            assert "shell" in data["tools"]
