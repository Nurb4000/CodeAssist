"""Tests for D4 follow-up behaviors: session workspaces, PTY persistence/events."""

import json

import pytest
from fastapi.testclient import TestClient

from codeassist import server


@pytest.fixture
def d4_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEASSIST_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("CODEASSIST_DB_PATH", str(tmp_path / "d4.db"))
    server._config = None
    server._tools = None
    limiter = getattr(server.app.state, "limiter", None)
    if limiter is not None:
        limiter.enabled = False
    from codeassist.session import init_db

    import asyncio
    asyncio.run(init_db())

    with TestClient(server.app) as client:
        yield client, tmp_path

    if limiter is not None:
        limiter.enabled = True


def test_create_and_patch_session_workspace(d4_client):
    client, tmp_path = d4_client
    wt = tmp_path / "wt1"
    wt.mkdir()

    created = client.post("/api/sessions", json={"workspace": str(wt)})
    assert created.status_code == 200
    sid = created.json()["id"]
    assert created.json()["workspace"] == str(wt.resolve())

    # patch to default
    cleared = client.patch(f"/api/sessions/{sid}", json={"workspace": None})
    assert cleared.status_code == 200

    # patch to worktree
    set_ws = client.patch(f"/api/sessions/{sid}", json={"workspace": str(wt)})
    assert set_ws.status_code == 200

    sessions = client.get("/api/sessions").json()
    row = next(s for s in sessions if s["id"] == sid)
    assert row.get("workspace") == str(wt.resolve())


def test_session_workspace_must_be_inside_configured_workspace(d4_client):
    client, tmp_path = d4_client
    outside = tmp_path.parent / "outside-d4"
    outside.mkdir(exist_ok=True)
    try:
        res = client.post("/api/sessions", json={"workspace": str(outside)})
        assert res.status_code == 400
    finally:
        # best effort cleanup
        try:
            outside.rmdir()
        except Exception:
            pass


def test_list_worktrees_endpoint(d4_client):
    client, tmp_path = d4_client
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    wt = tmp_path / ".worktrees" / "wt1"
    subprocess.run(["git", "worktree", "add", "-b", "feat/wt1", str(wt)], cwd=tmp_path, check=True, capture_output=True)

    res = client.get("/api/sessions/worktrees")
    assert res.status_code == 200
    paths = [w["path"] for w in res.json()["worktrees"]]
    assert str(wt.resolve()) in paths or str(wt) in paths
