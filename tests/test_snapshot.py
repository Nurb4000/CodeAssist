"""Snapshot manager tests (review item F1).

The manager shells out to real git; these tests use the system git binary so the
actual git/ownership behavior is exercised.
"""
import subprocess
from pathlib import Path

import pytest

from codeassist.snapshot import SnapshotManager


@pytest.mark.asyncio
async def test_initialize_creates_working_snapshot_repo(tmp_path):
    sm = SnapshotManager(tmp_path)
    await sm.initialize()

    assert sm._initialized is True
    git_dir = tmp_path / ".codeassist" / "snapshot" / ".git"
    assert git_dir.exists()
    mark = tmp_path / ".codeassist" / "snapshot" / ".snapshot-init"
    assert mark.exists()
    # Initial commit exists.
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path / ".codeassist" / "snapshot",
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


@pytest.mark.asyncio
async def test_initialize_registers_safe_directory_for_snapshot_dir(tmp_path):
    sm = SnapshotManager(tmp_path)
    await sm.initialize()

    listed = subprocess.run(
        ["git", "config", "--global", "--get-all", "safe.directory"],
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    # Both the "."-relative and absolute forms git accepts are fine; the manager
    # must have registered the snapshot dir.
    assert str(sm.snapshot_dir) in listed


@pytest.mark.asyncio
async def test_disabled_manager_does_nothing(tmp_path):
    sm = SnapshotManager(tmp_path, enabled=False)
    await sm.initialize()
    assert sm._initialized is False
    assert not (tmp_path / ".codeassist").exists()