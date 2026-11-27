"""MCP server API routes."""
import logging

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/servers", tags=["mcp"])
reload_router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("")
async def list_mcp_servers():
    """List all configured MCP servers."""
    from codeassist.session import MCPServer

    from ..server import get_config
    cfg = get_config()
    if not cfg.mcp.enabled:
        return {"servers": []}
    return {"servers": await MCPServer.list_all()}


@router.post("")
async def create_mcp_server(body: dict):
    """Create a new MCP server configuration. Body must contain 'name' and 'config'."""
    from codeassist.session import MCPServer

    from ..server import get_config
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    cfg = get_config()
    if not cfg.mcp.enabled:
        raise HTTPException(status_code=400, detail="MCP is not enabled")
    server_config = body.get("config", {})
    server = await MCPServer.create(name, server_config)
    from ..server import reload_mcp_servers, spawn_reload

    # Reconcile live connections off the request path (batch edits stay snappy).
    spawn_reload(reload_mcp_servers())
    return {"id": server.id}


@router.put("/{server_id}")
async def update_mcp_server(server_id: str, body: dict):
    """Update an MCP server's name/config and toggle its enabled flag."""
    from codeassist.session import MCPServer

    from ..server import get_config
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
    from ..server import reload_mcp_servers, spawn_reload

    spawn_reload(reload_mcp_servers())
    return {"ok": True}


@router.delete("/{server_id}")
async def delete_mcp_server(server_id: str):
    """Delete an MCP server by ID."""
    from codeassist.session import MCPServer

    from ..server import reload_mcp_servers, spawn_reload

    server = MCPServer(server_id)
    await server.delete()
    spawn_reload(reload_mcp_servers())
    return {"ok": True}


@reload_router.get("/status")
async def get_mcp_status():
    """Report live connection/discovery state, including per-server errors.

    OAuth tokens and header values are deliberately not part of this payload.
    """
    from ..oauth import oauth_manager
    from ..server import mcp_client

    if mcp_client is None:
        return {"enabled": False, "servers": []}
    servers = mcp_client.get_status()
    for status in servers:
        provider = status.get("oauth_provider")
        if provider:
            oauth_status = oauth_manager.get_status(provider)
            status["oauth"] = {
                "configured": bool(oauth_status.get("configured")),
                "authenticated": bool(oauth_status.get("authenticated")),
                "expired": oauth_status.get("expired"),
            }
    return {"enabled": True, "servers": servers}


@reload_router.post("/reload")
async def reload_mcp_connections():
    """Manually re-sync live MCP connections with the DB+config server set."""
    from ..server import reload_mcp_servers

    try:
        connected = await reload_mcp_servers()
    except Exception as e:  # pragma: no cover - defensive; reload logs internally  # noqa: BLE001
        log.error("Failed to reload MCP servers: %s", e)
        raise HTTPException(status_code=500, detail=f"Reload failed: {e}")
    return {"ok": True, "reconnected": connected}


@router.post("/{server_id}/oauth/start")
async def start_mcp_oauth(server_id: str, body: dict | None = None):
    """Return an OAuth authorization URL for an MCP server's configured provider.

    The server config must include `oauth_provider` (or `auth.provider`) and the
    provider must already be configured in the OAuth manager. The browser flow is
    completed through the existing `/api/oauth/callback` endpoint.
    """
    from ..oauth import oauth_manager
    from ..session import MCPServer
    from ..server import get_config

    cfg = get_config()
    if not cfg.mcp.enabled:
        raise HTTPException(status_code=400, detail="MCP is not enabled")
    server = await MCPServer.get(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    config = await server.get_config()
    provider = str(config.get("oauth_provider") or (config.get("auth") or {}).get("provider") or "").strip()
    if not provider:
        raise HTTPException(status_code=400, detail="MCP server has no oauth_provider configured")

    oauth_status = oauth_manager.get_status(provider)
    if not oauth_status.get("configured"):
        raise HTTPException(
            status_code=400,
            detail=f"OAuth provider '{provider}' is not configured; configure it in OAuth settings first",
        )

    body = body or {}
    scopes = body.get("scopes")
    scope_list = [str(s) for s in scopes] if isinstance(scopes, list) else None
    redirect_uri = body.get("redirect_uri")
    # Allow caller to request a specific redirect, but default to existing OAuth callback.
    try:
        if redirect_uri:
            provider_cfg = oauth_manager.get_provider(provider)
            if provider_cfg is not None:
                # Temporarily use requested redirect for URL generation only.
                from ..oauth import OAuthProviderConfig

                temp_cfg = OAuthProviderConfig(
                    name=provider_cfg.name,
                    client_id=provider_cfg.client_id,
                    client_secret=provider_cfg.client_secret,
                    auth_url=provider_cfg.auth_url,
                    token_url=provider_cfg.token_url,
                    redirect_uri=str(redirect_uri),
                    default_scopes=list(provider_cfg.default_scopes),
                )
                oauth_manager.register_provider(temp_cfg)
                auth_url, state = oauth_manager.get_authorization_url(provider, scopes=scope_list)
                # Restore original config to avoid mutating persisted callback behavior.
                oauth_manager.register_provider(provider_cfg)
            else:
                auth_url, state = oauth_manager.get_authorization_url(provider, scopes=scope_list)
        else:
            auth_url, state = oauth_manager.get_authorization_url(provider, scopes=scope_list)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {
        "ok": True,
        "server_id": server_id,
        "provider": provider,
        "authorization_url": auth_url,
        "state": state,
        "redirect_uri": redirect_uri or (oauth_manager.get_provider(provider).redirect_uri if oauth_manager.get_provider(provider) else None),
    }
