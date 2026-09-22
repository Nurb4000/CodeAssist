"""UI-managed settings: store, boot overrides, and /api/settings routes."""
import pytest

import codeassist.session as session_mod
from codeassist.config import Config
from codeassist.settings import SettingsStore, apply_settings_overrides, clear_override, coerce, settings_store


async def _init():
    """Create the settings table in the (per-test) test database."""
    await session_mod.init_db()


@pytest.mark.asyncio
async def test_store_roundtrip():
    await _init()
    store = SettingsStore()
    await store.load()
    assert store.get("llm.model") is None

    await store.set("llm.model", "my-model")
    await store.set("mcp.enabled", True)

    reloaded = SettingsStore()
    await reloaded.load()
    assert reloaded.get("llm.model") == "my-model"
    assert reloaded.get("mcp.enabled") == "true"  # JSON-encoded bool

    await reloaded.clear("llm.model")
    assert reloaded.get("llm.model") is None
    assert reloaded.get("mcp.enabled") == "true"


@pytest.mark.asyncio
async def test_apply_overrides_mutates_config():
    await _init()
    await settings_store.set("llm.model", "override-model")
    await settings_store.set("mcp.enabled", True)
    await settings_store.set("tools.max_output_chars", "9999")

    cfg = Config()
    await apply_settings_overrides(cfg)

    assert cfg.llm.model == "override-model"
    assert cfg.mcp.enabled is True
    assert cfg.tools.max_output_chars == 9999
    # Un-overridden settings keep their defaults.
    assert cfg.llm.temperature == 0.0


@pytest.mark.asyncio
async def test_apply_ignores_invalid_values():
    await _init()
    await settings_store.set("llm.temperature", "not-a-number")
    cfg = Config()
    await apply_settings_overrides(cfg)
    assert cfg.llm.temperature == 0.0


@pytest.mark.asyncio
async def test_clear_restores_file_value(monkeypatch):
    # Isolate from any ambient config.toml so reset targets the dataclass
    # default (""), not a base file value. clear_override reads Config.load().
    import codeassist.settings as s_mod

    monkeypatch.setattr(s_mod.Config, "load", staticmethod(lambda: Config()))
    await _init()
    await settings_store.set("llm.model", "ui-model")
    cfg = Config()
    cfg.llm.model = "ui-model"
    await clear_override("llm.model", cfg)
    assert settings_store.get("llm.model") is None
    assert cfg.llm.model == ""  # back to dataclass default (no baked-in model)


def test_coerce_types():
    assert coerce("true", "bool") is True
    assert coerce("off", "bool") is False
    assert coerce("12", "int") == 12
    assert coerce("1.5", "float") == 1.5
    assert coerce(42, "str") == "42"


def test_get_settings_lists_catalog(live_client):
    data = live_client.get("/api/settings")
    assert data.status_code == 200
    body = data.json()
    settings = body["settings"]
    keys = {s["key"] for s in settings}
    assert "llm.model" in keys
    assert "server.port" in keys
    llm = next(s for s in settings if s["key"] == "llm.model")
    assert llm["source"] == "file"
    assert llm["group"] == "LLM"
    secret = next(s for s in settings if s["key"] == "llm.api_key")
    assert secret["value"] is None
    assert secret["has_value"] is False


def test_put_settings_applies_live_and_flags_restart(live_client):
    import codeassist.server as server

    res = live_client.put("/api/settings", json={
        "llm.model": "edited-model",
        "server.port": 9999,
        "not_a_real_key": 5,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] == ["llm.model"]
    assert body["restart_required"] == ["server.port"]
    # Live config mutated for runtime-safe keys; restart-required not applied yet.
    assert server.get_config().llm.model == "edited-model"
    assert server.get_config().server.port == 8090

    # GET reflects the override.
    data = live_client.get("/api/settings").json()
    llm = next(s for s in data["settings"] if s["key"] == "llm.model")
    assert llm["source"] == "ui"
    assert llm["value"] == "edited-model"


def test_put_settings_validates_types(live_client):
    res = live_client.put("/api/settings", json={"llm.temperature": "abc"})
    assert res.status_code == 422


def test_put_settings_secret_placeholder_ignored(live_client):
    import codeassist.server as server

    # A non-empty secret overwrites the file value and applies live.
    res = live_client.put("/api/settings", json={"llm.api_key": "secret-123"})
    assert res.status_code == 200
    assert res.json()["applied"] == ["llm.api_key"]
    assert server.get_config().llm.api_key == "secret-123"

    # Empty secret value = "leave unchanged" placeholder; must not wipe it.
    res = live_client.put("/api/settings", json={"llm.api_key": ""})
    assert res.status_code == 200
    assert res.json()["applied"] == []
    assert server.get_config().llm.api_key == "secret-123"


def test_delete_setting_resets(live_client, monkeypatch):
    import codeassist.settings as s_mod

    # Reset must target the dataclass default (""), not any ambient config.toml.
    monkeypatch.setattr(s_mod.Config, "load", staticmethod(lambda: Config()))

    import codeassist.server as server

    live_client.put("/api/settings", json={"llm.model": "edited-model"})
    assert server.get_config().llm.model == "edited-model"

    res = live_client.delete("/api/settings/llm.model")
    assert res.status_code == 200
    assert server.get_config().llm.model == ""  # resets to no-default dataclass value

    res = live_client.delete("/api/settings/does.not.exist")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_put_settings_restart_flag_survives_restart_apply(live_client):
    """Saving a restart-required key must persist it so it applies at next boot."""
    res = live_client.put("/api/settings", json={"server.port": 9999})
    assert res.json()["restart_required"] == ["server.port"]

    # Simulated reboot: the store persisted it, so apply_settings_overrides picks it up.
    await settings_store.load()
    assert settings_store.get("server.port") is not None
    cfg = Config()
    await apply_settings_overrides(cfg)
    assert cfg.server.port == 9999


def test_test_connection_ok(live_client, monkeypatch):
    class FakeResponse:
        def __init__(self, status_code=200):
            self.status_code = status_code

        def json(self):
            if self.status_code == 200:
                return {"data": [{"id": "model-a"}, {"id": "model-b"}]}
            return {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return FakeResponse(500 if "fail" in url else 200)

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    ok = live_client.post("/api/config/test-connection", json={"base_url": "http://x/v1"})
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert ok.json()["models"] == ["model-a", "model-b"]

    failing = live_client.post("/api/config/test-connection", json={"base_url": "http://fail/v1"})
    assert failing.json()["ok"] is False