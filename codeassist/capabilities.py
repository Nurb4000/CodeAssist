"""Runtime model-capability detection with caching.

Probes the backend's /v1/models endpoint once per minute (fail-closed) for:
  - vision/multimodal capability (used to gate image attachments)
  - the active model name (single-model auto-detection)
  - context window size (only when the backend exposes metadata)

A manual override always wins; auto-detected values are only used when the
override is unset.  Unreachable/unknown backends fail closed (no vision, no
model auto-detect) but are retried on a short 5s window rather than cached
for a full minute — so a backend that comes online after startup (or a
base_url still loading at boot) self-heals instead of staying blind.
"""
import logging
import time

import httpx

log = logging.getLogger(__name__)

VISION_KEYS = ("vision", "multimodal", "image")

CACHE_TTL = 60.0
# Short window used when a backend *couldn't* be probed (base_url not yet
# loaded at startup, transient network error). Lets detection self-heal once
# the backend comes online instead of caching "no vision" for a full minute.
RETRY_TTL = 5.0
PROBE_TIMEOUT = 3.0


def _default_info() -> dict:
    return {"model": None, "vision": False, "context_window": None, "source": None}


_cache: dict = {"expires": 0.0, "info": _default_info()}


def _parse_vision(model_data: dict) -> bool:
    """Return True if the model dict advertises vision support.

    Handles several backend shapes:
      - flat top-level flags (llama.cpp): ``{"vision": True}``
      - nested ``capabilities``/``meta`` dicts (OpenAI-style):
        ``{"capabilities": {"vision": True}}``
      - a ``capabilities`` *list* of strings (FalconLLM / llama.cpp servers):
        ``{"capabilities": ["completion", "multimodal"]}``
    """
    for src in (model_data.get("capabilities"), model_data.get("meta"), model_data):
        if isinstance(src, dict):
            if any(src.get(k) for k in VISION_KEYS):
                return True
        elif isinstance(src, list):
            lowered = [str(c).lower() for c in src]
            if any(k in lowered for k in VISION_KEYS):
                return True
    return False


def _parse_ctx_len(model_data: dict, top_level: dict) -> int | None:
    """Best-effort context window from model metadata or top-level response.

    OpenAI-style backends expose ``context_length``/``ctx_len``; llama.cpp puts
    the runtime window under ``meta.n_ctx`` and the training window under
    ``meta.n_ctx_train``. Prefer the runtime ``n_ctx`` for budgeting since it is
    what the server actually accepts.
    """
    for source in (model_data, top_level):
        meta = source.get("meta") or {}
        ctx = (
            source.get("context_length")
            or source.get("ctx_len")
            or meta.get("ctx_len")
            or meta.get("context_length")
            or meta.get("n_ctx")
            or meta.get("n_ctx_train")
        )
        if ctx is not None:
            try:
                v = int(ctx)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    return None


def context_window_from_body(body: dict) -> int | None:
    """Detect the context window from a ``/v1/models`` response body.

    Iterates both OpenAI-style (``data[]``) and llama.cpp-style (``models[]``)
    entries and returns the first positive window, or ``None`` when the backend
    exposes none. Shared by the live probe and the settings test-connection so
    both surface the same detected value.
    """
    if not isinstance(body, dict):
        return None
    candidates = [m for m in (body.get("data") or []) if isinstance(m, dict)]
    candidates += [m for m in (body.get("models") or []) if isinstance(m, dict)]
    for model in candidates:
        ctx = _parse_ctx_len(model, body)
        if ctx is not None:
            return ctx
    return None


async def _probe_backend(cfg) -> dict | None:
    """Fetch /v1/models and extract vision, model name, context window.

    Returns ``None`` when the backend could *not* be probed (no/invalid
    base_url, network error, or unparseable response) so the caller can retry
    soon instead of caching a misleading "no vision" result. A real response
    always yields a dict (vision may legitimately be False).
    """
    result = _default_info()
    base = (cfg.llm.base_url or "").strip().rstrip("/")
    if not base or not base.startswith(("http://", "https://")):
        return None
    try:
        headers = {}
        if cfg.llm.api_key:
            headers["Authorization"] = f"Bearer {cfg.llm.api_key}"
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("Backend probe failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    top_level = data
    # OpenAI-style servers expose models under ``data``; FalconLLM / some
    # llama.cpp servers use a separate top-level ``models`` array whose items
    # carry a ``capabilities`` *list* (e.g. ["completion", "multimodal"]).
    data_models = [m for m in (data.get("data") or []) if isinstance(m, dict)]
    native_models = [m for m in (data.get("models") or []) if isinstance(m, dict)]
    candidate_models = data_models + native_models
    if not candidate_models:
        # No model entries in the response. Some servers put top-level
        # vision/context flags directly; honor those as definitive. But an
        # empty payload is usually a transient/warmup/error response from the
        # backend — don't cache it as a definitive "no vision" (which would
        # blind image uploads for a full minute). Return None to retry soon.
        if any(top_level.get(k) is True for k in VISION_KEYS):
            result["vision"] = True
            return result
        return None
    # Vision: any model with vision → whole endpoint is vision-capable.
    result["vision"] = any(_parse_vision(m) for m in candidate_models)
    # Model name: prefer the OpenAI-style ``data`` id, fall back to the native
    # ``models`` name/model field; use it only when a single model is served.
    primary = data_models or native_models
    if len(primary) == 1:
        mid = primary[0].get("id") or primary[0].get("name") or primary[0].get("model")
        if mid:
            result["model"] = mid
    # Context window: take the first available from any model.
    result["context_window"] = context_window_from_body(top_level)
    return result


async def _refresh(cfg) -> dict:
    info = await _probe_backend(cfg)
    if info is None:
        # Couldn't probe the backend (e.g. base_url not loaded yet). Cache a
        # neutral result with a short TTL so we retry soon rather than
        # poisoning the cache with a definitive "no vision" for 60s.
        info = _default_info()
        _cache["expires"] = time.monotonic() + RETRY_TTL
    else:
        _cache["expires"] = time.monotonic() + CACHE_TTL
    _cache["info"] = info
    return info


# ── public helpers ──────────────────────────────────────────────────────────

async def model_vision_capable(cfg) -> bool:
    """Whether the configured model can accept images (override OR auto-detect)."""
    if cfg.llm.vision:
        return True
    now = time.monotonic()
    if now > _cache["expires"]:
        await _refresh(cfg)
    return _cache["info"]["vision"]


async def get_backend_info(cfg) -> dict:
    """Cached snapshot of auto-detected backend properties.

    Returns ``{model, vision, context_window, source}`` where:
      - ``model``    — detected model id (single-model auto-detect) or ``None``
      - ``context_window`` — context length (if the backend exposes it) or ``None``
      - ``source``   — ``"backend"`` when auto-detected, ``None`` otherwise
    """
    now = time.monotonic()
    if now > _cache["expires"]:
        await _refresh(cfg)
    info = _cache["info"]
    return {
        "model": info.get("model"),
        "vision": info.get("vision", False),
        "context_window": info.get("context_window"),
        "source": "backend" if (info.get("model") or info.get("context_window")) else None,
    }


async def effective_context_window(cfg) -> int:
    """Context window used for token budgeting.

    Local/self-hosted backends (a non-OpenAI ``base_url``) advertise their real
    window via auto-detection, which we prefer so budgeting matches the model
    (e.g. a 1M-context llama.cpp build instead of the 128k default). External
    OpenAI keepers fall back to the configured value.
    """
    base = (cfg.llm.base_url or "").strip().lower()
    external = (not base) or "api.openai.com" in base
    detected = (await get_backend_info(cfg)).get("context_window")
    return detected if (not external and detected) else cfg.llm.context_window
