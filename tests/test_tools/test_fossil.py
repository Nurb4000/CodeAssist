"""Tests for fossil RCS tool."""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tools.fossil import FossilTool


class TestFossilTool:
    """Test fossil tool operations."""

    @pytest.fixture
    def fossil_tool(self):
        """Create a FossilTool instance."""
        return FossilTool()

    def test_is_not_fossil_repo(self, tmp_path, fossil_tool):
        """Test that non-fossil directory is detected."""
        fossil_tool.workspace = tmp_path
        
        # Create a regular file (not .fslckout or .fossil)
        (tmp_path / "test.txt").write_text("content")
        
        assert not fossil_tool._is_fossil_repo(tmp_path)

    def test_is_fossil_repo_with_fslckout(self, tmp_path, fossil_tool):
        """Test detection of fossil repo with .fslckout."""
        (tmp_path / ".fslckout").touch()
        
        assert fossil_tool._is_fossil_repo(tmp_path)

    def test_is_fossil_repo_with_fossil(self, tmp_path, fossil_tool):
        """Test detection of fossil repo with .fossil file."""
        (tmp_path / ".fossil").touch()
        
        assert fossil_tool._is_fossil_repo(tmp_path)

    @pytest.mark.asyncio
    async def test_not_fossil_repo_error(self, tmp_path, fossil_tool):
        """Test error when not a fossil repo."""
        fossil_tool.workspace = tmp_path
        
        # Create a regular file
        (tmp_path / "test.txt").write_text("content")
        
        result = await fossil_tool.execute(operation="status")
        
        assert "Error" in result.output and "Not a Fossil repository" in result.output

    @pytest.mark.asyncio
    async def test_status_not_fossil_repo(self, tmp_path, fossil_tool):
        """Test status on non-fossil repo."""
        fossil_tool.workspace = tmp_path
        (tmp_path / "test.txt").write_text("content")
        
        result = await fossil_tool.execute(operation="status")
        
        assert "Error" in result.output

    @pytest.mark.asyncio
    async def test_schema(self, fossil_tool):
        """Test that fossil tool has proper schema."""
        schema = fossil_tool.schema()
        
        assert schema["name"] == "fossil"
        assert "description" in schema
        assert "parameters" in schema
        assert "operation" in schema["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_unknown_operation(self, tmp_path, fossil_tool):
        """Test unknown operation returns error."""
        fossil_tool.workspace = tmp_path
        (tmp_path / ".fslckout").touch()
        
        result = await fossil_tool.execute(operation="unknown_op")
        
        assert "Unknown fossil operation" in result.output

    @pytest.mark.asyncio
    async def test_commit_without_message(self, tmp_path, fossil_tool):
        """Test commit without message returns error."""
        fossil_tool.workspace = tmp_path
        (tmp_path / ".fslckout").touch()
        
        result = await fossil_tool.execute(operation="commit")
        
        assert "Error" in result.output and "message is required" in result.output

    @pytest.mark.asyncio
    async def test_checkout_without_revision(self, tmp_path, fossil_tool):
        """Test checkout without revision returns error."""
        fossil_tool.workspace = tmp_path
        (tmp_path / ".fslckout").touch()
        
        result = await fossil_tool.execute(operation="checkout")
        
        assert "Error" in result.output and "revision is required" in result.output

    @pytest.mark.asyncio
    async def test_close_without_revision(self, tmp_path, fossil_tool):
        """Test close without revision returns error."""
        fossil_tool.workspace = tmp_path
        (tmp_path / ".fslckout").touch()
        
        result = await fossil_tool.execute(operation="close")
        
        assert "Error" in result.output and "revision is required" in result.output

    @pytest.mark.asyncio
    async def test_merge_without_source(self, tmp_path, fossil_tool):
        """Test merge without source branch returns error."""
        fossil_tool.workspace = tmp_path
        (tmp_path / ".fslckout").touch()
        
        result = await fossil_tool.execute(operation="merge")
        
        assert "Error" in result.output and "source_branch is required" in result.output

    @pytest.mark.asyncio
    async def test_revert_without_revision(self, tmp_path, fossil_tool):
        """Test revert without revision returns error."""
        fossil_tool.workspace = tmp_path
        (tmp_path / ".fslckout").touch()
        
        result = await fossil_tool.execute(operation="revert")
        
        assert "Error" in result.output and "revision_to_revert is required" in result.output

    @pytest.mark.asyncio
    async def test_export_without_filename(self, tmp_path, fossil_tool):
        """Test export without filename returns error."""
        fossil_tool.workspace = tmp_path
        (tmp_path / ".fslckout").touch()
        
        result = await fossil_tool.execute(operation="export")
        
        assert "Error" in result.output and "filename is required" in result.output
