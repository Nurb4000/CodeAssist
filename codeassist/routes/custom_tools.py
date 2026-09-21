"""Custom tools management API routes."""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/custom-tools", tags=["custom_tools"])


@router.get("")
async def list_custom_tools():
    """List all custom tools discovered from runtime/custom_tools/."""
    from codeassist.custom_tools_loader import get_custom_tool_registry
    from ..server import get_trust_registry
    from codeassist.config import load_config

    config = load_config()
    workspace = Path(config.server.workspace)
    trust_registry = get_trust_registry()
    registry = get_custom_tool_registry(workspace, trust_registry=trust_registry)
    registry.discover()

    return {"tools": registry.list_tools()}


@router.post("/reload")
async def reload_custom_tools():
    """Hot-reload custom tools from disk without restarting the server."""
    from codeassist.custom_tools_loader import get_custom_tool_registry
    from ..server import get_trust_registry
    from codeassist.config import load_config

    config = load_config()
    workspace = Path(config.server.workspace)
    trust_registry = get_trust_registry()
    registry = get_custom_tool_registry(workspace, trust_registry=trust_registry)
    registry.reload()

    return {"message": "Custom tools reloaded", "count": len(registry._tools)}


@router.delete("/{name}")
async def delete_custom_tool(name: str):
    """Delete a custom tool's source file from runtime/custom_tools/."""
    from codeassist.custom_tools_loader import get_custom_tool_registry
    from ..server import get_trust_registry
    from codeassist.config import load_config

    config = load_config()
    workspace = Path(config.server.workspace)
    registry = get_custom_tool_registry(workspace, trust_registry=get_trust_registry())
    # Locate + remove the tool's source file directly (trust-independent). A
    # missing file or one outside the managed dir is reported to the caller.
    path = registry.remove_tool(name)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Custom tool not found: {name}",
        )
    # Refresh the in-memory registry so the removed tool no longer appears.
    registry.reload()
    return {"ok": True, "deleted": str(path)}


@router.get("/export")
async def export_custom_tools():
    """Export all custom tools as a portable JSON manifest."""
    from codeassist.custom_tools_loader import get_custom_tool_registry
    from ..server import get_config, get_trust_registry

    registry = get_custom_tool_registry(get_config().workspace, trust_registry=get_trust_registry())
    return registry.export_tools()


@router.post("/import")
async def import_custom_tools(manifest: dict):
    """Import custom tools from a manifest produced by :func:`export_custom_tools`.

    Files are written verbatim into ``runtime/custom_tools`` and re-enter the
    trust flow as untrusted until approved.
    """
    from codeassist.custom_tools_loader import get_custom_tool_registry
    from ..server import get_config, get_trust_registry

    registry = get_custom_tool_registry(get_config().workspace, trust_registry=get_trust_registry())
    try:
        result = registry.import_tools(manifest)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}
