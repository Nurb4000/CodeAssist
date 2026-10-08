"""REST API for OAuth integration management."""
from fastapi import APIRouter, HTTPException, Query

from ..oauth import OAuthProviderConfig, oauth_manager

router = APIRouter(prefix="/api/oauth", tags=["oauth"])


@router.get("/providers")
async def list_providers():
    """List all registered OAuth providers and their status."""
    return {"providers": oauth_manager.list_providers()}


@router.post("/providers/{provider_name}/configure")
async def configure_provider(
    provider_name: str,
    client_id: str = Query(...),
    client_secret: str = Query(...),
    redirect_uri: str = Query("http://localhost:7878/api/oauth/callback"),
):
    """Configure an OAuth provider."""
    # Build default URLs based on provider name
    urls = {
        "github": {
            "auth_url": "https://github.com/login/oauth/authorize",
            "token_url": "https://github.com/login/oauth/access_token",
        },
        "google": {
            "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
        },
    }

    provider_urls = urls.get(provider_name, {})
    if not provider_urls:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider_name}'. Supported: {list(urls.keys())}",
        )

    config = OAuthProviderConfig(
        name=provider_name,
        client_id=client_id,
        client_secret=client_secret,
        auth_url=provider_urls["auth_url"],
        token_url=provider_urls["token_url"],
        redirect_uri=redirect_uri,
    )
    oauth_manager.register_provider(config)
    return {"ok": True, "provider": provider_name}


@router.get("/providers/{provider_name}/authorize")
async def get_authorization_url(
    provider_name: str,
    scopes: str = Query("", description="Space-separated OAuth scopes"),
):
    """Get an OAuth authorization URL for the user to visit."""
    try:
        scope_list = [s for s in scopes.split() if s] if scopes else None
        auth_url, state = oauth_manager.get_authorization_url(
            provider_name, scopes=scope_list,
        )
        return {
            "authorization_url": auth_url,
            "state": state,
            "provider": provider_name,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/providers/{provider_name}/status")
async def get_provider_status(provider_name: str):
    """Get the authentication status for a provider."""
    status = oauth_manager.get_status(provider_name)
    if not status.get("configured"):
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{provider_name}' not configured",
        )
    return status


@router.post("/providers/{provider_name}/revoke")
async def revoke_token(provider_name: str):
    """Revoke (disconnect) an OAuth provider."""
    if not oauth_manager.revoke_token(provider_name):
        raise HTTPException(
            status_code=404,
            detail=f"No token found for '{provider_name}'",
        )
    return {"ok": True, "provider": provider_name}


@router.post("/providers/{provider_name}/refresh")
async def refresh_token(provider_name: str):
    """Refresh an expired OAuth token."""
    token = await oauth_manager.refresh_token(provider_name)
    if not token:
        raise HTTPException(
            status_code=400,
            detail=f"Token refresh failed for '{provider_name}'",
        )
    return {
        "ok": True,
        "provider": provider_name,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
    }


@router.get("/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    provider: str = Query("github"),
):
    """Handle OAuth callback from the provider.

    This endpoint is called by the browser after the user authorizes the app.
    In a typical setup, the frontend redirects to this URL with the code and state.
    """
    token = oauth_manager.handle_callback(provider, code, state)
    if not token:
        return {
            "ok": False,
            "error": f"OAuth callback failed for '{provider}'",
        }
    return {
        "ok": True,
        "provider": provider,
        "scope": token.scope,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
    }
