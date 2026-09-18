"""Runtime model-capability detection with caching.

Currently only vision capability is probed (used to gate image attachments).
The effective vision setting is a manual override ([llm] vision = true) PLUS
automatic detection from the backend's /v1/models capabilities when available.
When the backend is unknown/unreachable the value is False (fail closed).
"""
import logging
import time

import httpx

log = logging.getLogger(__name__)

CAPABILITY_KEYS = ("vision", "multimodal", "image")

_cache: dict = {"expires": 0.0, "vision": False}

CACHE_TTL = 60.0
PROBE_TIMEOUT = 3.0


def _has_vision_capability(model: dict) -> bool:
    caps = model.get("capabilities") or model.get("meta") or {}
    if isinstance(caps, dict):
        return any(caps.get(key) for key in CAPABILITY_KEYS)
    return False


async def fetch_model_vision_capable(cfg) -> bool:
    """Probe the backend /v1/models endpoint for multimodal support."""
    base = (cfg.llm.base_url or "").strip().rstrip("/")
    if not base or not base.startswith(("http://", "https://")):
        return False
    try:
        headers = {}
        if cfg.llm.api_key:
            headers["Authorization"] = f"Bearer {cfg.llm.api_key}"
        timeout = httpx.Timeout(PROBE_TIMEOUT)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{base}/models", headers=headers)
            response.raise_for_status()
            data = response.json()
        if isinstance(data, dict):
            if any(data.get(key) is True for key in CAPABILITY_KEYS):
                return True
            models = data.get("data") or []
            return any(_has_vision_capability(m) for m in models if isinstance(m, dict))
    except Exception as exc:  # network errors, timeouts, bad JSON, 401s
        log.debug("Vision capability probe failed: %s", exc)
    return False


async def model_vision_capable(cfg) -> bool:
    """Whether the configured model can accept images (override OR auto-detect)."""
    if cfg.llm.vision:
        return True
    now = time.monotonic()
    if now > _cache["expires"]:
        _cache["vision"] = await fetch_model_vision_capable(cfg)
        _cache["expires"] = now + CACHE_TTL
    return _cache["vision"]