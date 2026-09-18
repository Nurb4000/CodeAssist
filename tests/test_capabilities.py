"""Tests for runtime model-capability detection (vision + local model/context auto-detect)."""
import asyncio

import pytest

import codeassist.capabilities as cap_mod
from codeassist.capabilities import get_backend_info, _default_info
from codeassist.config import Config, LLMConfig


@pytest.fixture(autouse=True)
def _reset_capabilities_cache():
    """Each test starts from a fresh (expired) capability cache."""
    cap_mod._cache = {"expires": 0.0, "info": _default_info()}
    yield
    cap_mod._cache = {"expires": 0.0, "info": _default_info()}


def _cfg(**llm_overrides):
    llm = LLMConfig()
    for k, v in llm_overrides.items():
        setattr(llm, k, v)
    return Config(llm=llm)


@pytest.mark.asyncio
async def test_get_backend_info_fails_closed_when_no_base_url():
    """A blank base_url (default external provider) must report no detection."""
    cfg = _cfg(base_url="")
    info = await get_backend_info(cfg)
    assert info["model"] is None
    assert info["context_window"] is None
    assert info["source"] is None


@pytest.mark.asyncio
async def test_get_backend_info_fails_closed_for_external_openai():
    """External providers (api.openai.com) must not auto-detect the model."""
    cfg = _cfg(base_url="https://api.openai.com/v1")

    async def _probe(cfg):
        return {**_default_info(), "model": "gpt-4o", "context_window": 128000, "source": "backend"}

    import codeassist.capabilities as cap_mod

    cap_mod._probe_backend = _probe  # type: ignore[method-assign]
    info = await get_backend_info(cfg)
    assert info["model"] == "gpt-4o"
    assert info["context_window"] == 128000


@pytest.mark.asyncio
async def test_get_backend_info_detects_single_model_and_context():
    """A local backend serving exactly one model with metadata is auto-detected."""
    cfg = _cfg(base_url="http://10.0.1.27:8080")

    async def _probe(cfg):
        return {
            **_default_info(),
            "model": "Ornith-1.5-35B-Q4_K_M.gguf",
            "context_window": 1_048_576,
            "vision": False,
            "source": "backend",
        }

    import codeassist.capabilities as cap_mod

    cap_mod._probe_backend = _probe  # type: ignore[method-assign]
    info = await get_backend_info(cfg)
    assert info["model"] == "Ornith-1.5-35B-Q4_K_M.gguf"
    assert info["context_window"] == 1_048_576
    assert info["source"] == "backend"


@pytest.mark.asyncio
async def test_get_backend_info_cache_is_refreshed():
    """The cache must be invalidated after CACHE_TTL so a new probe runs."""
    import time

    import codeassist.capabilities as cap_mod

    calls = {"n": 0}

    async def _probe(cfg):
        calls["n"] += 1
        return {**_default_info(), "model": f"m{calls['n']}"}

    cap_mod._probe_backend = _probe  # type: ignore[method-assign]
    cfg = _cfg(base_url="http://10.0.1.27:8080")

    first = await get_backend_info(cfg)
    assert first["model"] == "m1"
    # Force the cache to expire, then probe again.
    cap_mod._cache["expires"] = time.monotonic() - 1
    second = await get_backend_info(cfg)
    assert second["model"] == "m2"


def test_api_config_exposes_detected_fields(live_client, monkeypatch):
    """The /api/config route surfaces the detected model + context window."""
    async def _fake_get_backend_info(cfg):
        return {"model": "local-model", "vision": False, "context_window": 1048576, "source": "backend"}

    monkeypatch.setattr(cap_mod, "get_backend_info", _fake_get_backend_info)
    async def _fake_vision(cfg):
        return False

    monkeypatch.setattr(cap_mod, "model_vision_capable", _fake_vision)

    body = live_client.get("/api/config").json()
    assert body["detected_model"] == "local-model"
    assert body["effective_context_window"] == 1048576
    assert body["backend_source"] == "backend"
    assert body["backend_external"] is False

