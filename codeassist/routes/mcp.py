"""MCP server API routes."""
import logging

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/servers", tags=["mcp"])
reload_router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("")
async def list_mcp_servers():
    """List all configured MCP servers."""
    from ..server import get_config
    from codeassist.session import MCPServer
    cfg = get_config()
    if not cfg.mcp.enabled:
        return {"servers": []}
    return {"servers": await MCPServer.list_all()}


@router.post("")
async def create_mcp_server(body: dict):
    """Create a new MCP server configuration. Body must contain 'name' and 'config'."""
    from ..server import get_config
    from codeassist.session import MCPServer
    cfg = get_config()
    if not cfg.mcp.enabled:
        raise HTTPException(status_code=400, detail="MCP is not enabled")
    name = body.get("name")
    server_config = body.get("config", {})
    server = await MCPServer.create(name, server_config)
    from ..server import spawn_reload, reload_mcp_servers

    # Reconcile live connections off the request path (batch edits stay snappy).
    spawn_reload(reload_mcp_servers())
    return {"id": server.id}


@router.put("/{server_id}")
async def update_mcp_server(server_id: str, body: dict):
    """Update an MCP server's name/config and toggle its enabled flag."""
    from ..server import get_config
    from codeassist.session import MCPServer
    cfg = get_config()
    if not cfg.mcp.enabled:
        raise HTTPException(status_code=400, detail="MCP is not enabled")
    server = await MCPServer.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    name = body.get("name")
    config = body.get("config")
    if name is not None or config is not None:
        await server.update(name=name, config=config)
    enabled = body.get("enabled")
    if enabled is not None:
        await server.set_enabled(bool(enabled))
    from ..server import spawn_reload, reload_mcp_servers

    spawn_reload(reload_mcp_servers())
    return {"ok": True}


@router.delete("/{server_id}")
async def delete_mcp_server(server_id: str):
    """Delete an MCP server by ID."""
    from codeassist.session import MCPServer
    from ..server import spawn_reload, reload_mcp_servers

    server = MCPServer(server_id)
    await server.delete()
    spawn_reload(reload_mcp_servers())
    return {"ok": True}


@reload_router.post("/reload")
async def reload_mcp_connections():
    """Manually re-sync live MCP connections with the DB+config server set."""
    from ..server import reload_mcp_servers

    try:
        connected = await reload_mcp_servers()
    except Exception as e:  # pragma: no cover - defensive; reload logs internally
        log.error("Failed to reload MCP servers: %s", e)
        raise HTTPException(status_code=500, detail=f"Reload failed: {e}")
    return {"ok": True, "reconnected": connected}
