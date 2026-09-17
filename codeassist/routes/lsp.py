"""LSP server API routes."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/lsp/servers", tags=["lsp"])


@router.get("")
async def list_lsp_servers():
    """List all configured Language Server Protocol servers."""
    from codeassist.session import LSPServer
    return await LSPServer.list_all()


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


@router.delete("/{server_id}")
async def delete_lsp_server(server_id: str):
    """Delete an LSP server by ID."""
    from codeassist.session import LSPServer
    server = LSPServer(server_id)
    await server.delete()
    return {"ok": True}
