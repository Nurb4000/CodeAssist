"""Tests for apply_patch tool."""
import asyncio
import pytest
from pathlib import Path

from tools.apply_patch import ApplyPatchTool


class TestApplyPatchTool:
    """Test apply_patch tool operations."""

    @pytest.fixture
    def patch_tool(self):
        """Create an ApplyPatchTool instance."""
        return ApplyPatchTool()

    @pytest.mark.asyncio
    async def test_apply_patch_empty_content(self, tmp_path, patch_tool):
        """Test that empty patch content returns error."""
        patch_tool.workspace = tmp_path
        
        result = await patch_tool.execute(patch_content="")
        
        assert "Error" in result.output or "empty" in result.output.lower()

    @pytest.mark.asyncio
    async def test_apply_patch_dry_run(self, tmp_path, patch_tool):
        """Test dry run mode."""
        import subprocess
        
        # Setup git repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
        
        (tmp_path / "test.txt").write_text("Original content")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=str(tmp_path), capture_output=True)
        
        # Create a patch
        (tmp_path / "test.txt").write_text("Modified content")
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD",
            cwd=str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        patch_content = stdout.decode('utf-8')
        
        # Revert the change
        (tmp_path / "test.txt").write_text("Original content")
        
        patch_tool.workspace = tmp_path
        
        # Test dry run
        result = await patch_tool.execute(patch_content=patch_content, dry_run=True)
        
        assert "Dry run" in result.output or "would apply" in result.output.lower()

    @pytest.mark.asyncio
    async def test_apply_patch_valid_patch(self, tmp_path, patch_tool):
        """Test applying a valid patch."""
        import subprocess
        
        # Setup git repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
        
        (tmp_path / "test.txt").write_text("Original content")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=str(tmp_path), capture_output=True)
        
        # Modify file and create patch
        (tmp_path / "test.txt").write_text("Modified content")
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD",
            cwd=str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        patch_content = stdout.decode('utf-8')
        
        # Revert the change
        (tmp_path / "test.txt").write_text("Original content")
        
        patch_tool.workspace = tmp_path
        
        # Apply patch
        result = await patch_tool.execute(patch_content=patch_content)
        
        assert "successfully" in result.output.lower() or "Patch applied" in result.output

    def test_patch_tool_schema(self, patch_tool):
        """Test that patch tool has proper schema."""
        schema = patch_tool.schema()
        
        assert schema["name"] == "apply_patch"
        assert "description" in schema
        assert "parameters" in schema
        assert "patch_content" in schema["parameters"]["properties"]
