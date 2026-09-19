"""LSP server API routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/lsp/servers", tags=["lsp"])


@router.get("")
async def list_lsp_servers():
    """List all configured Language Server Protocol servers."""
    from codeassist.session import LSPServer
    return {"servers": await LSPServer.list_all()}


@router.post("")
async def create_lsp_server(body: dict):
    """Create a new LSP server configuration. Body must contain 'name' and 'command'."""
    from codeassist.session import LSPServer
    server = await LSPServer.create(
        name=body.get("name"),
        command=body.get("command"),
        args=body.get("args", []),
        languages=body.get("languages", []),
    )
    return {"id": server.id}


@router.put("/{server_id}")
async def update_lsp_server(server_id: str, body: dict):
    """Update an LSP server's fields and toggle its enabled flag."""
    from codeassist.session import LSPServer
    server = await LSPServer.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="LSP server not found")
    name = body.get("name")
    command = body.get("command")
    args = body.get("args")
    languages = body.get("languages")
    if any(v is not None for v in (name, command, args, languages)):
        await server.update(name=name, command=command, args=args, languages=languages)
    enabled = body.get("enabled")
    if enabled is not None:
        await server.set_enabled(bool(enabled))
    return {"ok": True}


@router.delete("/{server_id}")
async def delete_lsp_server(server_id: str):
    """Delete an LSP server by ID."""
    from codeassist.session import LSPServer
    server = LSPServer(server_id)
    await server.delete()
    return {"ok": True}
