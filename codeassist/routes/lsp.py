"""LSP server API routes."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/lsp/servers", tags=["lsp"])


@router.get("")
async def list_lsp_servers():
    from session import LSPServer
    return await LSPServer.list_all()


@router.post("")
async def create_lsp_server(body: dict):
    from session import LSPServer
    server = await LSPServer.create(
        name=body.get("name"),
        command=body.get("command"),
        args=body.get("args", []),
        languages=body.get("languages", []),
    )
    return {"id": server.id}


@router.delete("/{server_id}")
async def delete_lsp_server(server_id: str):
    from session import LSPServer
    server = LSPServer(server_id)
    await server.delete()
    return {"ok": True}
