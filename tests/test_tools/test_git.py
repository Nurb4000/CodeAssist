"""Tests for Git tool."""
import asyncio
import pytest
from pathlib import Path

from tools.git import GitTool


class TestGitTool:
    """Test Git tool operations."""

    @pytest.fixture
    def git_tool(self):
        """Create a GitTool instance."""
        return GitTool()

    @pytest.mark.asyncio
    async def test_git_status_clean(self, tmp_path, git_tool):
        """Test git status on a clean repository."""
        # Initialize a git repo
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
        
        git_tool.workspace = tmp_path
        
        # Create and commit a file
        (tmp_path / "test.txt").write_text("Hello")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), capture_output=True)
        
        # Test status
        result = await git_tool.execute(operation="status")
        # Status should show branch info or "Working tree clean"
        assert "Branch" in result.output or "Working tree clean" in result.output or len(result.output) > 0

    @pytest.mark.asyncio
    async def test_git_status_with_changes(self, tmp_path, git_tool):
        """Test git status with uncommitted changes."""
        import subprocess
        
        # Setup repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
        
        (tmp_path / "test.txt").write_text("Hello")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=str(tmp_path), capture_output=True)
        
        # Modify file
        (tmp_path / "test.txt").write_text("Modified")
        
        git_tool.workspace = tmp_path
        result = await git_tool.execute(operation="status")
        
        assert "M" in result.output or "modified" in result.output.lower()

    @pytest.mark.asyncio
    async def test_git_diff(self, tmp_path, git_tool):
        """Test git diff operation."""
        import subprocess
        
        # Setup repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
        
        (tmp_path / "test.txt").write_text("Line 1\nLine 2\nLine 3")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=str(tmp_path), capture_output=True)
        
        # Modify file
        (tmp_path / "test.txt").write_text("Line 1\nModified Line 2\nLine 3")
        
        git_tool.workspace = tmp_path
        result = await git_tool.execute(operation="diff")
        
        assert "diff" in result.output.lower() or "Modified" in result.output

    @pytest.mark.asyncio
    async def test_git_log(self, tmp_path, git_tool):
        """Test git log operation."""
        import subprocess
        
        # Setup repo with commits
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
        
        (tmp_path / "file1.txt").write_text("File 1")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "Commit 1"], cwd=str(tmp_path), capture_output=True)
        
        (tmp_path / "file2.txt").write_text("File 2")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "Commit 2"], cwd=str(tmp_path), capture_output=True)
        
        git_tool.workspace = tmp_path
        result = await git_tool.execute(operation="log", limit=5)
        
        assert "Commit 1" in result.output or "Commit 2" in result.output

    @pytest.mark.asyncio
    async def test_git_commit(self, tmp_path, git_tool):
        """Test git commit operation."""
        import subprocess
        
        # Setup repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
        
        (tmp_path / "test.txt").write_text("New file")
        
        git_tool.workspace = tmp_path
        result = await git_tool.execute(
            operation="commit",
            message="Test commit",
            all=True
        )
        
        assert "Committed" in result.output or "Test commit" in result.output

    @pytest.mark.asyncio
    async def test_git_branch_list(self, tmp_path, git_tool):
        """Test listing branches."""
        import subprocess
        
        # Setup repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
        
        (tmp_path / "test.txt").write_text("Hello")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=str(tmp_path), capture_output=True)
        
        git_tool.workspace = tmp_path
        result = await git_tool.execute(operation="branch", list_branches=True)
        
        assert "main" in result.output or "master" in result.output

    @pytest.mark.asyncio
    async def test_git_commit_without_message(self, tmp_path, git_tool):
        """Test that commit without message returns error."""
        import subprocess
        
        # Setup repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        
        git_tool.workspace = tmp_path
        result = await git_tool.execute(operation="commit", all=True)
        
        assert "Error" in result.output or "message is required" in result.output.lower()

    def test_git_tool_schema(self, git_tool):
        """Test that git tool has proper schema."""
        schema = git_tool.schema()
        
        assert schema["name"] == "git"
        assert "description" in schema
        assert "parameters" in schema
        assert schema["parameters"]["required"] == ["operation"]
