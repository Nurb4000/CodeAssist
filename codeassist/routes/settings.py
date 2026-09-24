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
    # "Session" trust-all is a runtime-only preference (no persisted override);
    # label its source so the UI can show it as active for the server lifetime.
    if spec["key"] == "permissions.trust_all" and effective == "session":
        item["source"] = "runtime"
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
    from ..settings import (
        BY_KEY,
        coerce,
        remove_override,
        settings_store,
        validate_setting,
        write_override,
    )

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
        error = validate_setting(spec, coerced)
        if error:
            raise HTTPException(status_code=422, detail=error)
        if spec["key"] == "permissions.trust_all" and coerced == "session":
            # "This session" is ephemeral: apply it live, drop any persisted
            # override (DB + overrides file) so it reverts after a restart.
            await settings_store.clear(key)
            remove_override(cfg, key)
            section = getattr(cfg, spec["section"])
            setattr(section, spec["field"], coerced)
            applied.append(key)
            continue
        await settings_store.set(key, coerced)
        # Persist non-secret edits back to config.overrides.toml so they survive
        # a data-dir reset. Secrets stay DB-only (never written to disk).
        if not spec.get("secret"):
            write_override(cfg, key, coerced)
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
    from ..settings import BY_KEY, clear_override, remove_override, settings_store

    if BY_KEY.get(key) is None:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")
    if not settings_store.is_loaded():
        await settings_store.load()
    cfg = get_config()
    # Remove from the overrides file first so a fresh Config.load() inside
    # clear_override doesn't re-merge the value we're deleting.
    remove_override(cfg, key)
    await clear_override(key, cfg)
    return {"ok": True, "key": key}


@router.post("/api/settings/restart")
async def request_restart():
    """Re-initialize config-driven subsystems from the on-disk config.

    Applies restart-required feature toggles (MCP/skills/plugins/LSP/git) live.
    Server bind changes (host/port/password) still need a container/process
    restart and are reported by the caller via ``/api/status``.
    """
    from ..server import reload_configured_subsystems

    await reload_configured_subsystems()
    return {"ok": True}


@router.post("/api/config/test-connection")
async def test_connection(payload: dict | None = None):
    """Ping the LLM backend, list models, and surface the detected context window.

    The context window is echoed back so the Settings UI can adopt it (a local
    llama.cpp server advertises its real window via meta.n_ctx, which beats the
    128k default for budgeting).
    """
    import httpx

    from ..capabilities import context_window_from_body
    from ..server import get_config

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
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if response.status_code != 200:
        return {"ok": False, "error": f"HTTP {response.status_code}", "endpoint": endpoint}
    context_window = None
    try:
        body = response.json()
        models = [m.get("id") for m in body.get("data", []) if m.get("id")]
        context_window = context_window_from_body(body)
    except Exception:  # noqa: BLE001
        models = []
    result = {"ok": True, "endpoint": endpoint, "models": models[:25]}
    if context_window is not None:
        result["context_window"] = context_window
    return result