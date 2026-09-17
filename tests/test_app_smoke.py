"""End-to-end smoke tests that boot the real FastAPI app and hit its REST surface.

Guards against the regression (commit 921381d) where the `codeassist/routes/`
package existed but was never registered, so every /api/* endpoint returned 404,
and where route handlers referenced pre-refactor module names (`from session
import ...`) that would 500 at request time.
"""
import pytest
from fastapi.testclient import TestClient

import codeassist.server as server

REST_ENDPOINTS_GET = [
    "/health",
    "/api/config",
    "/api/todos",
    "/api/sessions",
    "/api/sessions/search/tags?tags=wip",
    "/api/kb/stats",
    "/api/kb/entries",
    "/api/kb/sessions",
    "/api/kb/search?q=smoke",
    "/api/kb/analytics/tools",
    "/api/kb/analytics/llm",
    "/api/tools/manage/list",
    "/api/tools/manage/usage",
    "/api/tools/trust/pending",
    "/api/tools/trust/trusted",
    "/api/skills",
    "/api/skills/list",
    "/api/agents",
    "/api/plugins",
    "/api/lsp/servers",
    "/api/mcp/servers",
    "/api/git/repos",
    "/api/custom-tools",
    "/api/knowledge",
]


@pytest.fixture
def live_client(monkeypatch, test_workspace):
    """Boot the real app against an isolated temp workspace and test database."""
    _GLOBALS = (
        "_config", "tools", "skill_registry", "plugin_registry",
        "trust_registry", "lsp_client", "mcp_client",
    )
    saved = {name: getattr(server, name) for name in _GLOBALS}
    monkeypatch.setattr(server, "_config", None)
    cfg = server.get_config()
    monkeypatch.setattr(cfg.server, "workspace", str(test_workspace))
    cfg.workspace = test_workspace.resolve()
    try:
        with TestClient(server.app, raise_server_exceptions=False) as client:
            yield client
    finally:
        # Restore module globals the app lifespan mutated, to avoid leaking
        # server state into other tests (e.g. the live tool registry).
        for name, old in saved.items():
            setattr(server, name, old)


def test_rest_api_is_registered(live_client):
    """The whole REST surface must be mounted on the app (not 404/500)."""
    for path in REST_ENDPOINTS_GET:
        r = live_client.get(path)
        assert r.status_code < 500, f"GET {path} -> {r.status_code}"


def test_health(live_client):
    r = live_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_session_lifecycle(live_client):
    r = live_client.post("/api/sessions")
    assert r.status_code == 200
    sid = r.json()["id"]

    assert live_client.get(f"/api/sessions/{sid}/messages").status_code == 200
    assert live_client.post(f"/api/sessions/{sid}/fork", json={}).status_code == 200
    assert live_client.patch(f"/api/sessions/{sid}", json={"name": "smoke"}).status_code == 200
    assert live_client.post(f"/api/sessions/{sid}/tags", json={"tag": "wip"}).status_code == 200
    assert live_client.get(f"/api/sessions/{sid}/tags").status_code == 200
    assert live_client.post(f"/api/sessions/{sid}/undo").status_code == 200

    export = live_client.post("/api/sessions/export", json={"session_id": sid, "redact": True})
    assert export.status_code == 200
    assert export.json()["version"] == 2


def test_tools_management_specific_routes_not_shadowed(live_client):
    """/api/tools/manage/usage and /manage/scan must not be swallowed by /manage/{tool_name}."""
    assert live_client.get("/api/tools/manage/usage").status_code == 200
    assert live_client.post("/api/tools/manage/scan").status_code == 200
    assert live_client.post("/api/tools/reload").status_code == 200