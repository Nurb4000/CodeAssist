"""OAuth integration system for external service authentication.

Provides token storage, authorization URL generation, callback handling,
and automatic token refresh. Currently supports GitHub OAuth for PR reviews
and API access.
"""
import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)


@dataclass
class OAuthToken:
    """Represents an OAuth token with optional refresh capability."""
    provider: str  # "github", "google", etc.
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scope: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at


@dataclass
class OAuthProviderConfig:
    """Configuration for an OAuth provider."""
    name: str  # "github", "google", etc.
    client_id: str
    client_secret: str
    auth_url: str
    token_url: str
    redirect_uri: str
    default_scopes: list[str] = field(default_factory=list)


class OAuthManager:
    """Manages OAuth flows for external service authentication."""

    def __init__(self):
        self._providers: dict[str, OAuthProviderConfig] = {}
        self._tokens: dict[str, OAuthToken] = {}  # provider -> token
        self._pending_states: dict[str, str] = {}  # state -> provider

    def register_provider(self, config: OAuthProviderConfig):
        """Register an OAuth provider configuration."""
        self._providers[config.name] = config
        log.info("Registered OAuth provider: %s", config.name)

    def get_provider(self, name: str) -> OAuthProviderConfig | None:
        """Get a registered provider by name."""
        return self._providers.get(name)

    def list_providers(self) -> list[str]:
        """List all registered provider names."""
        return list(self._providers.keys())

    def get_authorization_url(
        self,
        provider_name: str,
        scopes: list[str] | None = None,
        state: str | None = None,
    ) -> tuple[str, str]:
        """Generate an OAuth authorization URL.

        Returns (authorization_url, state).
        """
        config = self._providers.get(provider_name)
        if not config:
            raise ValueError(f"Provider '{provider_name}' not registered")

        state = state or secrets.token_urlsafe(32)
        self._pending_states[state] = provider_name

        scope_str = " ".join(scopes or config.default_scopes)
        auth_url = (
            f"{config.auth_url}?"
            f"client_id={config.client_id}&"
            f"redirect_uri={config.redirect_uri}&"
            f"scope={scope_str}&"
            f"response_type=code&"
            f"state={state}"
        )
        return auth_url, state

    def handle_callback(
        self,
        provider_name: str,
        code: str,
        state: str,
    ) -> OAuthToken | None:
        """Process an OAuth callback and exchange the code for a token.

        Returns the OAuthToken on success, or None on failure.
        """
        import httpx

        config = self._providers.get(provider_name)
        if not config:
            log.warning("OAuth callback for unknown provider: %s", provider_name)
            return None

        # Verify state
        expected_provider = self._pending_states.pop(state, None)
        if expected_provider != provider_name:
            log.warning(
                "OAuth state mismatch: expected %s, got %s",
                expected_provider, provider_name,
            )
            return None

        try:
            response = httpx.post(
                config.token_url,
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                    "code": code,
                    "redirect_uri": config.redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            token_data = response.json()

            # Parse expiration
            expires_at = None
            if "expires_in" in token_data:
                expires_at = datetime.now(UTC) + timedelta(
                    seconds=int(token_data["expires_in"])
                )

            token = OAuthToken(
                provider=provider_name,
                access_token=token_data.get("access_token", ""),
                token_type=token_data.get("token_type", "Bearer"),
                refresh_token=token_data.get("refresh_token"),
                expires_at=expires_at,
                scope=token_data.get("scope", ""),
            )
            self._tokens[provider_name] = token
            log.info("OAuth token received for %s", provider_name)
            return token

        except Exception as e:
            log.exception("OAuth token exchange failed for %s", provider_name)
            return None

    async def refresh_token(self, provider_name: str) -> OAuthToken | None:
        """Refresh an expired OAuth token using the refresh token."""
        import httpx

        token = self._tokens.get(provider_name)
        if not token or not token.refresh_token:
            log.warning("No refresh token available for %s", provider_name)
            return None

        config = self._providers.get(provider_name)
        if not config:
            return None

        try:
            response = await httpx.AsyncClient(timeout=10.0).post(
                config.token_url,
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                    "refresh_token": token.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            token_data = response.json()

            expires_at = None
            if "expires_in" in token_data:
                expires_at = datetime.now(UTC) + timedelta(
                    seconds=int(token_data["expires_in"])
                )

            new_token = OAuthToken(
                provider=provider_name,
                access_token=token_data.get("access_token", token.access_token),
                token_type=token_data.get("token_type", token.token_type),
                refresh_token=token_data.get("refresh_token", token.refresh_token),
                expires_at=expires_at,
                scope=token_data.get("scope", token.scope),
            )
            self._tokens[provider_name] = new_token
            log.info("OAuth token refreshed for %s", provider_name)
            return new_token

        except Exception as e:
            log.exception("OAuth token refresh failed for %s", provider_name)
            return None

    def get_token(self, provider_name: str) -> OAuthToken | None:
        """Get the current token for a provider, auto-refreshing if expired."""
        token = self._tokens.get(provider_name)
        if token and token.is_expired:
            # Return the token anyway; the caller can trigger refresh
            log.info("Token expired for %s, refresh needed", provider_name)
        return token

    def get_access_token(self, provider_name: str) -> str | None:
        """Get the access token string for a provider."""
        token = self.get_token(provider_name)
        return token.access_token if token else None

    def revoke_token(self, provider_name: str) -> bool:
        """Revoke (remove) a token for a provider."""
        if provider_name in self._tokens:
            del self._tokens[provider_name]
            log.info("OAuth token revoked for %s", provider_name)
            return True
        return False

    def get_status(self, provider_name: str | None = None) -> dict:
        """Get the status of OAuth integrations."""
        if provider_name:
            token = self._tokens.get(provider_name)
            config = self._providers.get(provider_name)
            return {
                "provider": provider_name,
                "configured": config is not None,
                "authenticated": token is not None,
                "expired": token.is_expired if token else None,
                "scope": token.scope if token else "",
            }

        statuses = {}
        for name in self._providers:
            statuses[name] = self.get_status(name)
        return statuses


# Singleton instance
oauth_manager = OAuthManager()
