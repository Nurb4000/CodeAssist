"""Runtime model-capability detection with caching.

Probes the backend's /v1/models endpoint once per minute (fail-closed) for:
  - vision/multimodal capability (used to gate image attachments)
  - the active model name (single-model auto-detection)
  - context window size (only when the backend exposes metadata)

A manual override always wins; auto-detected values are only used when the
override is unset.  Unknown/unreachable backends → fail-closed (no vision,
no model auto-detect, default context window).
"""
import logging
import time

import httpx

log = logging.getLogger(__name__)

VISION_KEYS = ("vision", "multimodal", "image")

CACHE_TTL = 60.0
PROBE_TIMEOUT = 3.0


def _default_info() -> dict:
    return {"model": None, "vision": False, "context_window": None, "source": None}


_cache: dict = {"expires": 0.0, "info": _default_info()}


def _parse_vision(model_data: dict) -> bool:
    """Return True if the model dict advertises vision support."""
    caps = model_data.get("capabilities") or model_data.get("meta") or {}
    if isinstance(caps, dict):
        return any(caps.get(k) for k in VISION_KEYS)
    return False


def _parse_ctx_len(model_data: dict, top_level: dict) -> int | None:
    """Best-effort context window from model metadata or top-level response."""
    for source in (model_data, top_level):
        ctx = (
            source.get("context_length")
            or source.get("ctx_len")
            or (source.get("meta") or {}).get("ctx_len")
            or (source.get("meta") or {}).get("context_length")
        )
        if ctx is not None:
            try:
                v = int(ctx)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    return None


async def _probe_backend(cfg) -> dict:
    """Fetch /v1/models and extract vision, model name, context window."""
    result = _default_info()
    base = (cfg.llm.base_url or "").strip().rstrip("/")
    if not base or not base.startswith(("http://", "https://")):
        return result
    try:
        headers = {}
        if cfg.llm.api_key:
            headers["Authorization"] = f"Bearer {cfg.llm.api_key}"
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.debug("Backend probe failed: %s", exc)
        return result
    if not isinstance(data, dict):
        return result
    top_level = data
    models = [m for m in (data.get("data") or []) if isinstance(m, dict)]
    if not models:
        # Some servers put top-level vision/context flags directly.
        result["vision"] = any(top_level.get(k) is True for k in VISION_KEYS)
        result["context_window"] = _parse_ctx_len({}, top_level)
        return result
    # Vision: any model with vision → whole endpoint is vision-capable.
    result["vision"] = any(_parse_vision(m) for m in models)
    # Model name: use the single model id if exactly one model is served,
    # or fall back to the configured model (explicit config wins later).
    if len(models) == 1:
        mid = models[0].get("id", "")
        if mid:
            result["model"] = mid
    # Context window: take the first available from any model.
    for m in models:
        ctx = _parse_ctx_len(m, top_level)
        if ctx is not None:
            result["context_window"] = ctx
            break
    return result


async def _refresh(cfg) -> dict:
    info = await _probe_backend(cfg)
    _cache["info"] = info
    _cache["expires"] = time.monotonic() + CACHE_TTL
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
