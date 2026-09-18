"""UI-managed settings API."""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["settings"])


def _catalog_payload(spec: dict, cfg, value: str | None) -> dict:
    """Snapshot one catalog entry for the client (effective value + metadata)."""
    section = getattr(cfg, spec["section"])
    effective = getattr(section, spec["field"])
    item = {
        "key": spec["key"],
        "type": spec["type"],
        "label": spec["label"],
        "description": spec["description"],
        "group": spec["group"],
        "restart_required": spec["restart_required"],
        "source": "ui" if value is not None else "file",
        "value": None if spec.get("secret") else effective,
    }
    if spec.get("options"):
        item["options"] = spec["options"]
    if spec.get("secret"):
        # Never return the secret; just whether we hold an override for it.
        item["has_value"] = value is not None
    return item


@router.get("/api/settings")
async def get_settings():
    """List editable settings with effective values and source."""
    from ..server import get_config
    from ..settings import SETTINGS_CATALOG, settings_store

    if not settings_store.is_loaded():
        await settings_store.load()
    cfg = get_config()
    return {
        "settings": [
            _catalog_payload(spec, cfg, settings_store.get(spec["key"]))
            for spec in SETTINGS_CATALOG
        ]
    }


@router.put("/api/settings")
async def update_settings(payload: dict):
    """Persist UI overrides. Restart-required keys are saved but not applied live."""
    from ..server import get_config
    from ..settings import BY_KEY, coerce, settings_store

    if not settings_store.is_loaded():
        await settings_store.load()
    cfg = get_config()
    applied, restart_required = [], []
    for key, value in payload.items():
        spec = BY_KEY.get(key)
        if not spec or value is None:
            continue
        if isinstance(value, dict):
            value = value.get("value")
        if spec.get("secret"):
            candidate = str(value or "").strip()
            if not candidate:
                continue  # unchanged placeholder
        try:
            coerced = coerce(value, spec["type"])
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=f"Invalid value for {key}")
        await settings_store.set(key, coerced)
        if spec.get("restart_required"):
            restart_required.append(key)
        else:
            section = getattr(cfg, spec["section"])
            setattr(section, spec["field"], coerced)
            applied.append(key)
    return {"applied": applied, "restart_required": restart_required}


@router.delete("/api/settings/{key}")
async def delete_setting(key: str):
    """Remove a UI override, reverting to the config.toml (or default) value."""
    from ..server import get_config
    from ..settings import BY_KEY, clear_override, settings_store

    if BY_KEY.get(key) is None:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")
    if not settings_store.is_loaded():
        await settings_store.load()
    await clear_override(key, get_config())
    return {"ok": True, "key": key}


@router.post("/api/config/test-connection")
async def test_connection(payload: dict | None = None):
    """Ping the LLM backend and list available models (optionally with proposed overrides)."""
    import httpx

    from ..server import get_config
    from ..settings import BY_KEY, coerce

    cfg = get_config()
    base_url = cfg.llm.base_url
    api_key = cfg.llm.api_key
    if payload:
        base_url = str(payload.get("base_url") or base_url or "")
        api_key = str(payload.get("api_key") or api_key or "")
    endpoint = (base_url or "https://api.openai.com/v1").rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    timeout = 10.0
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            response = await client.get(endpoint, headers=headers)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if response.status_code != 200:
        return {"ok": False, "error": f"HTTP {response.status_code}", "endpoint": endpoint}
    try:
        body = response.json()
        models = [m.get("id") for m in body.get("data", []) if m.get("id")]
    except Exception:
        models = []
    return {"ok": True, "endpoint": endpoint, "models": models[:25]}