"""MCP server API routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/mcp/servers", tags=["mcp"])


@router.get("")
async def list_mcp_servers():
    """List all configured MCP servers."""
    from ..server import get_config
    from codeassist.session import MCPServer
    cfg = get_config()
    if not cfg.mcp.enabled:
        return {"servers": []}
    return await MCPServer.list_all()


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
    return {"id": server.id}


@router.delete("/{server_id}")
async def delete_mcp_server(server_id: str):
    """Delete an MCP server by ID."""
    from codeassist.session import MCPServer
    server = MCPServer(server_id)
    await server.delete()
    return {"ok": True}
