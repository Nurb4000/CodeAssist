"""Tests for process tool."""
import asyncio
import pytest

from tools.process import ProcessTool


class TestProcessTool:
    """Test process tool operations."""

    @pytest.fixture
    def proc_tool(self):
        """Create a ProcessTool instance."""
        return ProcessTool()

    @pytest.mark.asyncio
    async def test_list_empty_processes(self, proc_tool):
        """Test listing when no processes are running."""
        result = await proc_tool.execute(action="list")
        
        assert "No running processes" in result.output

    @pytest.mark.asyncio
    async def test_start_process(self, proc_tool, tmp_path):
        """Test starting a simple process."""
        proc_tool.workspace = tmp_path
        
        # Start a sleep process
        result = await proc_tool.execute(
            action="start",
            command="sleep 3600"
        )
        
        assert "Process started" in result.output
        assert "PID:" in result.output

    @pytest.mark.asyncio
    async def test_start_process_without_command(self, proc_tool):
        """Test starting without command returns error."""
        result = await proc_tool.execute(action="start")
        
        assert "Error" in result.output or "command is required" in result.output.lower()

    @pytest.mark.asyncio
    async def test_stop_nonexistent_process(self, proc_tool):
        """Test stopping a process that doesn't exist."""
        result = await proc_tool.execute(action="stop", process_id="99999")
        
        assert "Error" in result.output or "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_status_nonexistent_process(self, proc_tool):
        """Test getting status of nonexistent process."""
        result = await proc_tool.execute(action="status", process_id="99999")
        
        assert "Error" in result.output or "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_logs_without_process_id(self, proc_tool):
        """Test getting logs without process ID."""
        result = await proc_tool.execute(action="logs")
        
        assert "Error" in result.output or "process_id is required" in result.output.lower()

    @pytest.mark.asyncio
    async def test_invalid_action(self, proc_tool):
        """Test invalid action returns error."""
        result = await proc_tool.execute(action="invalid")
        
        assert "Error" in result.output or "unknown action" in result.output.lower()

    def test_process_tool_schema(self, proc_tool):
        """Test that process tool has proper schema."""
        schema = proc_tool.schema()
        
        assert schema["name"] == "process"
        assert "description" in schema
        assert "parameters" in schema
        assert "action" in schema["parameters"]["properties"]
