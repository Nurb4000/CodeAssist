"""Custom tools management API routes."""
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/custom-tools", tags=["custom_tools"])


@router.get("")
async def list_custom_tools():
    """List all custom tools discovered from .codeassist/custom_tools/."""
    from custom_tools_loader import get_custom_tool_registry
    from ..server import get_trust_registry
    from config import load_config

    config = load_config()
    workspace = Path(config.server.workspace)
    trust_registry = get_trust_registry()
    registry = get_custom_tool_registry(workspace, trust_registry=trust_registry)
    registry.discover()

    return {"tools": registry.list_tools()}


@router.post("/reload")
async def reload_custom_tools():
    """Hot-reload custom tools from disk without restarting the server."""
    from custom_tools_loader import get_custom_tool_registry
    from ..server import get_trust_registry
    from config import load_config

    config = load_config()
    workspace = Path(config.server.workspace)
    trust_registry = get_trust_registry()
    registry = get_custom_tool_registry(workspace, trust_registry=trust_registry)
    registry.reload()

    return {"message": "Custom tools reloaded", "count": len(registry._tools)}
