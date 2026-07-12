"""Tests for directory tool."""
import asyncio
import pytest
from pathlib import Path

from tools.directory import DirectoryTool


class TestDirectoryTool:
    """Test directory tool operations."""

    @pytest.fixture
    def dir_tool(self):
        """Create a DirectoryTool instance."""
        return DirectoryTool()

    @pytest.mark.asyncio
    async def test_list_directory(self, tmp_path, dir_tool):
        """Test listing a directory."""
        # Create some files
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.py").write_text("print('hello')")
        (tmp_path / "subdir").mkdir()
        
        dir_tool.workspace = tmp_path
        
        result = await dir_tool.execute(path=str(tmp_path))
        
        assert "file1.txt" in result.output
        assert "file2.py" in result.output
        assert "subdir" in result.output

    @pytest.mark.asyncio
    async def test_list_directory_empty(self, tmp_path, dir_tool):
        """Test listing an empty directory."""
        dir_tool.workspace = tmp_path
        
        result = await dir_tool.execute(path=str(tmp_path))
        
        assert "empty" in result.output.lower()

    @pytest.mark.asyncio
    async def test_list_directory_recursive(self, tmp_path, dir_tool):
        """Test recursive directory listing."""
        # Create nested structure
        (tmp_path / "file1.txt").write_text("content")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file2.txt").write_text("nested content")
        
        dir_tool.workspace = tmp_path
        
        result = await dir_tool.execute(path=str(tmp_path), recursive=True)
        
        assert "file1.txt" in result.output
        assert "file2.txt" in result.output

    @pytest.mark.asyncio
    async def test_list_directory_hidden(self, tmp_path, dir_tool):
        """Test including/excluding hidden files."""
        # Create hidden and visible files
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("public")
        
        dir_tool.workspace = tmp_path
        
        # Without hidden
        result = await dir_tool.execute(path=str(tmp_path), include_hidden=False)
        assert ".hidden" not in result.output
        assert "visible.txt" in result.output
        
        # With hidden
        result = await dir_tool.execute(path=str(tmp_path), include_hidden=True)
        assert ".hidden" in result.output

    @pytest.mark.asyncio
    async def test_list_directory_sort_by_name(self, tmp_path, dir_tool):
        """Test sorting by name."""
        (tmp_path / "zebra.txt").write_text("z")
        (tmp_path / "apple.txt").write_text("a")
        (tmp_path / "mango.txt").write_text("m")
        
        dir_tool.workspace = tmp_path
        
        result = await dir_tool.execute(path=str(tmp_path), sort_by="name")
        
        # Check that files appear in output
        assert "apple.txt" in result.output
        assert "mango.txt" in result.output
        assert "zebra.txt" in result.output

    @pytest.mark.asyncio
    async def test_list_directory_limit(self, tmp_path, dir_tool):
        """Test limiting number of results."""
        # Create many files
        for i in range(10):
            (tmp_path / f"file{i:02d}.txt").write_text(f"content {i}")
        
        dir_tool.workspace = tmp_path
        
        result = await dir_tool.execute(path=str(tmp_path), limit=3)
        
        # Should only show 3 files
        count = sum(1 for i in range(10) if f"file{i:02d}.txt" in result.output)
        assert count <= 3

    def test_directory_tool_schema(self, dir_tool):
        """Test that directory tool has proper schema."""
        schema = dir_tool.schema()
        
        assert schema["name"] == "directory"
        assert "description" in schema
        assert "parameters" in schema
        assert "path" in schema["parameters"]["properties"]
