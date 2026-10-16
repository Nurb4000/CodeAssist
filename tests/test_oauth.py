"""Tests for OAuth state management and token auto-refresh."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from codeassist.oauth import OAuthManager, OAuthProviderConfig, OAuthToken


def _provider(name: str = "github") -> OAuthProviderConfig:
    return OAuthProviderConfig(
        name=name,
        client_id="cid",
        client_secret="csecret",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
        redirect_uri="http://localhost/callback",
    )


def _token(provider: str = "github", *, expired: bool = False,
           refresh_token: str | None = "refresh") -> OAuthToken:
    expires_at = None
    if expired:
        expires_at = datetime.now(UTC) - timedelta(minutes=5)
    return OAuthToken(
        provider=provider,
        access_token="access-old",
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


class TestOAuthState:
    def test_state_stored_with_timestamp(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider())
        _, state = mgr.get_authorization_url("github")
        assert state in mgr._pending_states
        provider, created = mgr._pending_states[state]
        assert provider == "github"
        assert isinstance(created, datetime)

    def test_purge_removes_expired_states(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider())
        _, fresh = mgr.get_authorization_url("github")
        _, stale = mgr.get_authorization_url("github")
        # Backdate the stale state beyond the TTL.
        mgr._pending_states[stale] = ("github", datetime.now(UTC) - mgr._STATE_TTL - timedelta(seconds=1))

        mgr._purge_expired_states()

        assert fresh in mgr._pending_states
        assert stale not in mgr._pending_states

    def test_get_authorization_url_purges_before_adding(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider())
        _, stale = mgr.get_authorization_url("github")
        mgr._pending_states[stale] = ("github", datetime.now(UTC) - mgr._STATE_TTL - timedelta(seconds=1))

        _, fresh = mgr.get_authorization_url("github")

        assert stale not in mgr._pending_states
        assert fresh in mgr._pending_states

    def test_callback_rejects_unknown_state(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider())
        assert mgr.handle_callback("github", "code", "never-issued") is None

    def test_callback_rejects_expired_state(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider())
        _, state = mgr.get_authorization_url("github")
        mgr._pending_states[state] = ("github", datetime.now(UTC) - mgr._STATE_TTL - timedelta(seconds=1))

        with patch("httpx.post") as mock_post:
            assert mgr.handle_callback("github", "code", state) is None
            mock_post.assert_not_called()

    def test_callback_rejects_provider_mismatch(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider("github"))
        mgr.register_provider(_provider("google"))
        _, state = mgr.get_authorization_url("github")
        assert mgr.handle_callback("google", "code", state) is None

    def test_callback_is_single_use(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider())
        _, state = mgr.get_authorization_url("github")

        fake_response = type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"access_token": "tok", "scope": "repo"},
        })()
        with patch("httpx.post", return_value=fake_response):
            assert mgr.handle_callback("github", "code", state) is not None
            # Replaying the same state must now fail.
            assert mgr.handle_callback("github", "code", state) is None


class TestOAuthTokenRefresh:
    @pytest.mark.asyncio
    async def test_get_token_returns_valid_token_unchanged(self):
        mgr = OAuthManager()
        token = _token(expired=False)
        mgr._tokens["github"] = token
        assert await mgr.get_token("github") is token

    @pytest.mark.asyncio
    async def test_get_token_auto_refreshes_expired(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider())
        mgr._tokens["github"] = _token(expired=True)
        refreshed = _token(expired=False)
        refreshed.access_token = "access-new"

        with patch.object(mgr, "refresh_token", new=AsyncMock(return_value=refreshed)) as mock_refresh:
            result = await mgr.get_token("github")

        mock_refresh.assert_awaited_once_with("github")
        assert result is refreshed
        assert result.access_token == "access-new"

    @pytest.mark.asyncio
    async def test_get_token_returns_expired_when_refresh_fails(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider())
        expired = _token(expired=True)
        mgr._tokens["github"] = expired

        with patch.object(mgr, "refresh_token", new=AsyncMock(return_value=None)):
            result = await mgr.get_token("github")

        assert result is expired

    @pytest.mark.asyncio
    async def test_get_token_no_refresh_token_returns_expired(self):
        mgr = OAuthManager()
        mgr._tokens["github"] = _token(expired=True, refresh_token=None)

        with patch.object(mgr, "refresh_token", new=AsyncMock()) as mock_refresh:
            result = await mgr.get_token("github")

        mock_refresh.assert_not_awaited()
        assert result is not None
        assert result.is_expired

    @pytest.mark.asyncio
    async def test_get_access_token_returns_refreshed_string(self):
        mgr = OAuthManager()
        mgr.register_provider(_provider())
        mgr._tokens["github"] = _token(expired=True)
        refreshed = _token(expired=False)
        refreshed.access_token = "access-new"

        with patch.object(mgr, "refresh_token", new=AsyncMock(return_value=refreshed)):
            assert await mgr.get_access_token("github") == "access-new"

    @pytest.mark.asyncio
    async def test_get_access_token_none_when_absent(self):
        mgr = OAuthManager()
        assert await mgr.get_access_token("github") is None
