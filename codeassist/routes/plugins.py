"""Plugins API routes."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


@router.get("")
async def list_plugins():
    from ..server import get_config, plugin_registry
    cfg = get_config()
    if not cfg.plugins.enabled or not plugin_registry:
        return {"plugins": []}
    return {"plugins": plugin_registry.list_plugins()}
