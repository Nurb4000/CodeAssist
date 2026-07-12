"""Tests for HTTP tool."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tools.http import HTTPTool


class TestHTTPTool:
    """Test HTTP tool operations."""

    @pytest.fixture
    def http_tool(self):
        """Create an HTTPTool instance."""
        return HTTPTool()

    @pytest.mark.asyncio
    async def test_invalid_url(self, http_tool):
        """Test that invalid URL returns error."""
        result = await http_tool.execute(method="GET", url="not-a-url")
        
        assert "Error" in result.output and "http://" in result.output

    @pytest.mark.asyncio
    async def test_schema(self, http_tool):
        """Test that HTTP tool has proper schema."""
        schema = http_tool.schema()
        
        assert schema["name"] == "http"
        assert "description" in schema
        assert "parameters" in schema
        assert "method" in schema["parameters"]["properties"]
        assert "url" in schema["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_required_parameters(self, http_tool):
        """Test that method and url are required."""
        # This should fail because url is missing
        with pytest.raises(Exception):
            await http_tool.execute(method="GET")
