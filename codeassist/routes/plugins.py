"""Plugins API routes."""
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _discover_registry():
    """Build a fresh disk PluginRegistry and discover plugins from it.

    Uses ``cfg.workspace`` (the same resolved workspace the boot-time global
    registry uses) so delete operates on exactly what ``GET /api/plugins`` lists.
    """
    from codeassist.plugins import PluginRegistry

    from ..server import get_config

    cfg = get_config()
    registry = PluginRegistry(Path(cfg.workspace), cfg.plugins)
    registry.discover()
    return registry


@router.get("")
async def list_plugins():
    """List all loaded plugins."""
    from ..server import get_config, plugin_registry
    cfg = get_config()
    if not cfg.plugins.enabled or not plugin_registry:
        return {"plugins": []}
    return {"plugins": plugin_registry.list_plugins()}


@router.delete("/{name}")
async def delete_plugin(name: str):
    """Delete a discovered plugin's directory from the workspace."""
    from codeassist.plugins import PluginRegistry

    from ..server import plugin_registry as global_registry

    registry = _discover_registry()
    if registry.get_plugin(name) is None:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {name}")

    path = registry.remove_plugin(name)
    if path is None:
        raise HTTPException(
            status_code=409,
            detail=f"Plugin '{name}' is not backed by a workspace directory",
        )

    if isinstance(global_registry, PluginRegistry):
        global_registry._plugins.clear()
        global_registry.discover()
    return {"ok": True, "deleted": str(path)}
