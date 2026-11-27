"""Tests for dedicated worktree tool."""

import subprocess

import pytest

from codeassist.tools.worktree import WorktreeTool


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "f.txt").write_text("base\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


@pytest.mark.asyncio
async def test_worktree_list_and_create(repo):
    tool = WorktreeTool()
    tool.workspace = repo
    wt = repo / ".worktrees" / "wt-feature"
    result = await tool.execute(action="create", path=str(wt), new_branch="feature/test")
    assert not result.error, result.output
    assert "Created worktree" in result.output

    listed = await tool.execute(action="list")
    assert not listed.error, listed.output
    assert str(wt.resolve()) in listed.output or str(wt) in listed.output
    assert "feature/test" in listed.output


@pytest.mark.asyncio
async def test_worktree_remove_and_prune(repo):
    tool = WorktreeTool()
    tool.workspace = repo
    wt = repo / ".worktrees" / "wt-remove"
    created = await tool.execute(action="create", path=str(wt), new_branch="feature/remove")
    assert not created.error, created.output

    removed = await tool.execute(action="remove", path=str(wt))
    assert not removed.error, removed.output
    assert "Removed worktree" in removed.output

    pruned = await tool.execute(action="prune")
    assert not pruned.error, pruned.output


@pytest.mark.asyncio
async def test_worktree_create_requires_args(repo):
    tool = WorktreeTool()
    tool.workspace = repo
    result = await tool.execute(action="create", path=str(repo.parent / "wt"))
    assert result.error
    assert "branch or new_branch is required" in result.output
