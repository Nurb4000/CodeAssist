"""Phase C2: DB-only provider profiles, activation, and model probing."""
import pytest

import codeassist.session as session_mod
from codeassist.config import Config
from codeassist.settings import (
    apply_settings_overrides,
    provider_profile_store,
    settings_store,
)


async def _init():
    await session_mod.init_db()


PROFILE = {
    "name": "local",
    "provider": "custom",
    "base_url": "http://localhost:11434/v1",
    "api_key": "profile-secret",
    "model": "llama3",
    "context_window": 32768,
    "vision": True,
    "audio": False,
    "timeout": 120,
}


@pytest.mark.asyncio
async def test_profile_store_roundtrip_and_masking():
    await _init()
    await provider_profile_store.upsert("local", PROFILE)

    reloaded = type(provider_profile_store)()
    await reloaded.load()
    stored = reloaded.get("local")
    assert stored["api_key"] == "profile-secret"
    assert stored["context_window"] == 32768
    assert stored["vision"] == 1

    masked = reloaded.masked(stored)
    assert masked["api_key"] is None
    assert masked["has_key"] is True
    assert masked["name"] == "local"


@pytest.mark.asyncio
async def test_blank_key_keeps_existing_secret():
    await _init()
    await provider_profile_store.upsert("local", PROFILE)
    await provider_profile_store.upsert("local", {"model": "llama3:8b", "api_key": ""})
    stored = provider_profile_store.get("local")
    assert stored["api_key"] == "profile-secret"
    assert stored["model"] == "llama3:8b"


@pytest.mark.asyncio
async def test_active_profile_hydrates_config_and_wins_over_flat_override():
    await _init()
    await provider_profile_store.upsert("local", PROFILE)
    await settings_store.set("llm.active_profile", "local")
    await settings_store.set("llm.model", "flat-wins-only-without-profile")

    cfg = Config()
    await apply_settings_overrides(cfg)
    assert cfg.llm.active_profile == "local"
    assert cfg.llm.provider == "custom"
    assert cfg.llm.base_url == "http://localhost:11434/v1"
    assert cfg.llm.api_key == "profile-secret"
    assert cfg.llm.model == "llama3"
    assert cfg.llm.context_window == 32768
    assert cfg.llm.vision is True
    # Non-profile generation knobs keep their configured/global values.
    assert cfg.llm.max_tokens == 8192


@pytest.mark.asyncio
async def test_missing_active_profile_does_not_break_boot():
    await _init()
    await settings_store.set("llm.active_profile", "deleted-profile")
    await settings_store.set("llm.model", "file-model")
    cfg = Config()
    await apply_settings_overrides(cfg)
    assert cfg.llm.active_profile == "deleted-profile"
    assert cfg.llm.model == "file-model"


def test_profile_routes_mask_secrets_and_guard_active_delete(live_client):
    from codeassist import server

    created = live_client.post("/api/provider-profiles", json=PROFILE)
    assert created.status_code == 200, created.text
    assert created.json()["api_key"] is None
    assert created.json()["has_key"] is True

    listed = live_client.get("/api/provider-profiles").json()
    assert listed["active"] == ""
    assert listed["profiles"][0]["has_key"] is True
    assert listed["profiles"][0]["api_key"] is None

    live_client.put("/api/settings", json={"llm.active_profile": "local"})
    assert server.get_config().llm.model == "llama3"
    settings = live_client.get("/api/settings").json()["settings"]
    model = next(s for s in settings if s["key"] == "llm.model")
    key = next(s for s in settings if s["key"] == "llm.api_key")
    source = next(s for s in settings if s["key"] == "llm.active_profile")
    assert model["source"] == "profile"
    assert key["value"] is None and key["has_value"] is True
    assert source["value"] == "local"

    assert live_client.delete("/api/provider-profiles/local").status_code == 409
    live_client.put("/api/settings", json={"llm.active_profile": ""})
    assert server.get_config().llm.active_profile == ""
    assert server.get_config().llm.model != "llama3"  # back to file/default
    assert live_client.delete("/api/provider-profiles/local").status_code == 200


def test_save_settings_updates_active_profile_not_flat_overrides(live_client):
    from codeassist import server
    from codeassist.settings import settings_store

    live_client.post("/api/provider-profiles", json=PROFILE)
    live_client.put("/api/settings", json={"llm.active_profile": "local"})
    res = live_client.put("/api/settings", json={"llm.model": "llama3:70b"})
    assert res.status_code == 200, res.text
    assert res.json()["applied"] == ["llm.model"]

    assert provider_profile_store.get("local")["model"] == "llama3:70b"
    assert settings_store.get("llm.model") is None
    assert server.get_config().llm.model == "llama3:70b"


def test_unknown_profile_activation_is_rejected(live_client):
    res = live_client.put("/api/settings", json={"llm.active_profile": "does-not-exist"})
    assert res.status_code == 404


def test_invalid_profile_values_are_rejected(live_client):
    bad_name = live_client.post("/api/provider-profiles", json={**PROFILE, "name": "bad name"})
    assert bad_name.status_code == 422
    bad_url = live_client.post("/api/provider-profiles", json={**PROFILE, "base_url": "file:///etc/passwd"})
    assert bad_url.status_code == 422
    bad_provider = live_client.post("/api/provider-profiles", json={**PROFILE, "provider": "nope"})
    assert bad_provider.status_code == 422


class _RecordingClient:
    last_headers = None
    last_url = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        type(self).last_headers = headers
        type(self).last_url = url

        class _Resp:
            status_code = 200

            def json(self):
                return {"data": [{"id": "a"}, {"id": "b"}]}

        return _Resp()


def test_probe_uses_profile_key_only_on_its_own_host(live_client, monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", _RecordingClient)
    live_client.post("/api/provider-profiles", json=PROFILE)

    ok = live_client.post("/api/config/test-connection", json={"profile": "local"})
    assert ok.json()["ok"] is True
    assert ok.json()["models"] == ["a", "b"]
    assert _RecordingClient.last_headers["Authorization"] == "Bearer profile-secret"

    _RecordingClient.last_headers = None
    foreign = live_client.post("/api/config/test-connection", json={
        "profile": "local", "base_url": "https://attacker.example/v1",
    })
    assert foreign.json()["ok"] is True
    assert not _RecordingClient.last_headers, "profile key must not leave its host"


def test_probe_blank_profile_base_uses_default_host_not_configured_host(live_client, monkeypatch):
    from codeassist import server

    monkeypatch.setattr(server.get_config().llm, "base_url", "https://global.example/v1")
    monkeypatch.setattr("httpx.AsyncClient", _RecordingClient)
    profile = {**PROFILE, "name": "openai-default", "base_url": "",
               "api_key": "default-profile-key"}
    live_client.post("/api/provider-profiles", json=profile)

    res = live_client.post("/api/config/test-connection", json={"profile": "openai-default"})
    assert res.json()["endpoint"] == "https://api.openai.com/v1/models"
    assert _RecordingClient.last_url == "https://api.openai.com/v1/models"
    assert _RecordingClient.last_headers["Authorization"] == "Bearer default-profile-key"

    # A caller forcing the flat configured host must not receive the profile key.
    _RecordingClient.last_headers = None
    foreign = live_client.post("/api/config/test-connection", json={
        "profile": "openai-default", "base_url": "https://global.example/v1",
    })
    assert foreign.json()["ok"] is True
    assert not _RecordingClient.last_headers


def test_probe_reports_unknown_profile(live_client, monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", _RecordingClient)
    _RecordingClient.last_headers = None
    res = live_client.post("/api/config/test-connection", json={"profile": "missing"})
    assert res.json()["ok"] is False
    assert "missing" in res.json()["error"]
    assert _RecordingClient.last_headers is None


def test_clearing_active_profile_restores_flat_values(live_client):
    from codeassist import server

    live_client.post("/api/provider-profiles", json=PROFILE)
    live_client.put("/api/settings", json={"llm.model": "flat-model"})
    live_client.put("/api/settings", json={"llm.active_profile": "local"})
    assert server.get_config().llm.model == "llama3"

    res = live_client.delete("/api/settings/llm.active_profile")
    assert res.status_code == 200
    assert server.get_config().llm.model == "flat-model"
