"""Tests for runtime model-capability detection (vision + local model/context auto-detect)."""
import asyncio

import pytest

import codeassist.capabilities as cap_mod
from codeassist.capabilities import get_backend_info, _default_info
from codeassist.config import Config, LLMConfig


@pytest.fixture(autouse=True)
def _reset_capabilities_cache():
    """Each test starts from a fresh (expired) capability cache and a clean
    ``_probe_backend``. The ``test_get_backend_info_*`` tests reassign
    ``cap_mod._probe_backend`` and never restore it, so snapshot/restore here to
    keep tests isolated."""
    import codeassist.capabilities as cap_mod

    real_probe = cap_mod._probe_backend
    cap_mod._cache = {"expires": 0.0, "info": _default_info()}
    yield
    cap_mod._probe_backend = real_probe
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
    cfg = _cfg(base_url="http://localhost:8080")

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
    cfg = _cfg(base_url="http://localhost:8080")

    first = await get_backend_info(cfg)
    assert first["model"] == "m1"
    # Force the cache to expire, then probe again.
    cap_mod._cache["expires"] = time.monotonic() - 1
    second = await get_backend_info(cfg)
    assert second["model"] == "m2"


class TestParseVision:
    """_parse_vision must handle nested (capabilities/meta) and flat
    (llama.cpp top-level) vision flags."""

    def test_flat_top_level_vision(self):
        """llama.cpp exposes ``vision: true`` directly on the model object."""
        from codeassist.capabilities import _parse_vision

        assert _parse_vision({"id": "m", "vision": True}) is True
        assert _parse_vision({"id": "m", "vision": False}) is False
        assert _parse_vision({"id": "m"}) is False

    def test_flat_multimodal_and_image_keys(self):
        from codeassist.capabilities import _parse_vision

        assert _parse_vision({"multimodal": True}) is True
        assert _parse_vision({"image": True}) is True

    def test_nested_capabilities_and_meta(self):
        from codeassist.capabilities import _parse_vision

        assert _parse_vision({"capabilities": {"vision": True}}) is True
        assert _parse_vision({"meta": {"multimodal": True}}) is True

    def test_empty_and_non_dict(self):
        from codeassist.capabilities import _parse_vision

        assert _parse_vision({}) is False
        assert _parse_vision({"capabilities": "nope"}) is False


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeAsyncClient:
    def __init__(self, data):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        return _FakeResp(self._data)


@pytest.mark.asyncio
async def test_probe_backend_detects_llama_cpp_vision(monkeypatch):
    """A single-model llama.cpp /v1/models response with top-level ``vision``
    must be detected as vision-capable (regression for gated image uploads)."""
    import codeassist.capabilities as cap_mod

    llama_cpp_body = {
        "object": "list",
        "data": [
            {
                "id": "llama-3.2-3b-vision-q4_0_1717065123",
                "object": "model",
                "created": 1695000000,
                "owned_by": "llama.cpp",
                "vision": True,
            }
        ],
    }
    monkeypatch.setattr(
        cap_mod.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(llama_cpp_body)
    )

    cfg = _cfg(base_url="http://127.0.0.1:8080")
    info = await cap_mod._probe_backend(cfg)
    assert info["vision"] is True
    assert info["model"] == "llama-3.2-3b-vision-q4_0_1717065123"

    # And the public helper used to gate image attachments agrees.
    assert await cap_mod.model_vision_capable(cfg) is True


@pytest.mark.asyncio
async def test_probe_backend_vision_false_when_flag_absent(monkeypatch):
    """A non-vision llama.cpp model must still fail closed (no false positive)."""
    import codeassist.capabilities as cap_mod

    plain_body = {
        "object": "list",
        "data": [{"id": "plain", "object": "model", "owned_by": "llama.cpp"}],
    }
    monkeypatch.setattr(
        cap_mod.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(plain_body)
    )

    cfg = _cfg(base_url="http://127.0.0.1:8080")
    info = await cap_mod._probe_backend(cfg)
    assert info["vision"] is False


@pytest.mark.asyncio
async def test_unprobeable_backend_does_not_poison_cache():
    """A backend that can't be probed yet (empty base_url at startup) must use
    the short retry TTL, not cache 'no vision' for the full 60s. Otherwise a
    base_url loaded after boot stays blind until the cache expires."""
    import time

    import codeassist.capabilities as cap_mod

    cfg = _cfg(base_url="")  # no backend configured yet
    assert await cap_mod._probe_backend(cfg) is None

    before = time.monotonic()
    await cap_mod._refresh(cfg)
    remaining = cap_mod._cache["expires"] - time.monotonic()
    # Short retry window, not the full cache TTL.
    assert 0 <= remaining <= cap_mod.RETRY_TTL + 0.5
    # And a later probe once base_url is set succeeds and caches for full TTL.
    cfg.llm.base_url = "http://127.0.0.1:8080/v1"

    async def _fake_probe(c):
        return {**cap_mod._default_info(), "vision": True, "model": "m"}

    cap_mod._probe_backend = _fake_probe  # type: ignore[method-assign]
    await cap_mod._refresh(cfg)
    assert cap_mod._cache["expires"] - time.monotonic() > cap_mod.CACHE_TTL - 1


@pytest.mark.asyncio
async def test_empty_model_payload_is_transient_not_definitive(monkeypatch):
    """A 200 response with no model entries (warmup/error payload) must NOT be
    cached as a definitive 'no vision' — it should signal retry (None) so a
    backend that comes online isn't blind for a full minute."""
    import codeassist.capabilities as cap_mod

    empty_body = {"object": "list", "data": []}  # no models anywhere
    monkeypatch.setattr(
        cap_mod.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(empty_body)
    )
    cfg = _cfg(base_url="http://127.0.0.1:8080")
    assert await cap_mod._probe_backend(cfg) is None


@pytest.mark.asyncio
async def test_probe_backend_detects_falcon_multimodal_capabilities(monkeypatch):
    """The FalconLLM/llama.cpp /v1/models shape puts ``multimodal`` in a
    top-level ``models[]`` array as a *capabilities list*, not on the OpenAI
    ``data[]`` objects. This is the real-world case behind gated image uploads.
    """
    import codeassist.capabilities as cap_mod

    falcon_body = {
        "models": [
            {
                "name": "/opt/llama.cpp/Models/Ornith-1.5-35B-1M-Q4_K_M.gguf",
                "model": "/opt/llama.cpp/Models/Ornith-1.5-35B-1M-Q4_K_M.gguf",
                "capabilities": ["completion", "multimodal"],
            }
        ],
        "object": "list",
        "data": [
            {
                "id": "/opt/llama.cpp/Models/Ornith-1.5-35B-1M-Q4_K_M.gguf",
                "object": "model",
                "owned_by": "llamacpp",
                "meta": {"n_ctx": 700160, "n_ctx_train": 1048576},
            }
        ],
    }
    monkeypatch.setattr(
        cap_mod.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(falcon_body)
    )

    cfg = _cfg(base_url="http://127.0.0.1:8080")
    info = await cap_mod._probe_backend(cfg)
    assert info["vision"] is True
    # Single model -> its id is auto-detected too.
    assert info["model"].endswith("Ornith-1.5-35B-1M-Q4_K_M.gguf")
    assert await cap_mod.model_vision_capable(cfg) is True


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


def test_api_config_reports_agent_id_not_display_name(live_client):
    """Regression: /api/config must report the agent *id* (registry key), never the
    human display name. The chat UI matches this against a mode-list item's id to
    render its short label and mark it active; returning the display name made the
    selector revert to 'CodeAssist' on reload instead of the configured default."""
    agents = live_client.get("/api/agents").json()
    agent_ids = {a["id"] for a in agents}
    body = live_client.get("/api/config").json()

    assert body["agent_name"] in agent_ids
    # The regression only bites when an id differs from its display name.
    assert any(a["id"] != a["name"] for a in agents)

