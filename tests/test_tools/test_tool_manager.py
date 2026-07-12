"""Tests for tool manager tool."""
import pytest

from tools.tool_manager import ToolManagerTool


class TestToolManagerTool:
    """Test tool manager tool operations."""

    @pytest.fixture
    def tool_mgr(self):
        """Create a ToolManagerTool instance."""
        return ToolManagerTool()

    @pytest.mark.asyncio
    async def test_list_tools(self, tool_mgr):
        """Test listing available tools."""
        result = await tool_mgr.execute(action="list")
        
        assert "Available Tools" in result.output
        # Should have at least some basic tools
        assert "read" in result.output.lower() or "write" in result.output.lower()

    @pytest.mark.asyncio
    async def test_reload_tools(self, tool_mgr):
        """Test reloading tools."""
        result = await tool_mgr.execute(action="reload")
        
        assert "reloaded successfully" in result.output.lower() or "Error" not in result.output

    @pytest.mark.asyncio
    async def test_invalid_action(self, tool_mgr):
        """Test invalid action returns error."""
        result = await tool_mgr.execute(action="invalid")
        
        assert "Error" in result.output or "unknown action" in result.output.lower()

    def test_tool_manager_schema(self, tool_mgr):
        """Test that tool manager has proper schema."""
        schema = tool_mgr.schema()
        
        assert schema["name"] == "tool_manager"
        assert "description" in schema
        assert "parameters" in schema
        assert "action" in schema["parameters"]["properties"]
