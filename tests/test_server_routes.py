"""Tests for LSP/MCP server-create route input validation.

The create endpoints accepted a body with no ``name`` and passed ``None`` down
to ``LSPServer.create`` / ``MCPServer.create``, whose ``name`` columns are
``NOT NULL``. That raised an unhandled integrity error and surfaced as a 500. A
missing/empty name is a client error (400), so each route now rejects it up
front, before any database or reload work.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codeassist.routes.mcp as mcp_route
import codeassist.server
from codeassist.routes.lsp import router as lsp_router


def _client(router):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


lsp_client = _client(lsp_router)
mcp_client = _client(mcp_route.router)


@pytest.mark.parametrize("body", [{}, {"name": None}, {"name": ""}])
def test_lsp_create_rejects_missing_name(body):
    # Without the guard this falls through to LSPServer.create and 500s on the
    # NOT NULL name.
    assert lsp_client.post("/api/lsp/servers", json=body).status_code == 400


@pytest.mark.parametrize("body", [{}, {"name": None}, {"name": ""}])
def test_mcp_create_rejects_missing_name(body, monkeypatch):
    # MCP is disabled by default, which would return 400 for an unrelated reason;
    # enable it so the only way to a 400 is the name guard. Without the fix the
    # request falls through to MCPServer.create and 500s on the NOT NULL name.
    monkeypatch.setattr(
        codeassist.server,
        "get_config",
        lambda: SimpleNamespace(mcp=SimpleNamespace(enabled=True)),
    )
    assert mcp_client.post("/api/mcp/servers", json=body).status_code == 400
