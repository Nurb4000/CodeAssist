"""UI-managed settings API."""
import ipaddress
import socket
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["settings"])

# Hosts that are never a legitimate LLM backend: cloud metadata services,
# link-local ranges, and the unspecified address. These are blocked outright.
# Deliberately narrower than tools.security.validate_url: loopback/private/
# single-label hosts ARE valid here (a local llama.cpp/Ollama/vLLM box is the
# main use case), so we only forbid the endpoints that exist to be abused.
_METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata",
    "169.254.169.254",
    "fd00:ec2::254",
})


def _url_host(url: str) -> str:
    """Lowercased hostname from a URL, tolerating a bare ``host:port/v1`` form."""
    if not isinstance(url, str):
        return ""
    raw = url.strip()
    if not raw:
        return ""
    if "//" not in raw:
        raw = "//" + raw
    try:
        return (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""


def _is_metadata_host(host: str) -> bool:
    """True for cloud-metadata / link-local / unspecified endpoints."""
    if not host:
        return True
    if host in _METADATA_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_link_local or ip.is_unspecified


def _resolve_is_metadata(host: str) -> bool:
    """Resolve a hostname and flag it if any address is metadata/link-local.

    Guards against a friendly-looking name pointing at 169.254.169.254. DNS
    failure is not fatal here (the fetch itself will report the error).
    """
    try:
        results = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return False
    for _, _, _, _, sockaddr in results:
        if _is_metadata_host(sockaddr[0]):
            return True
    return False


def _catalog_payload(
    spec: dict, cfg, value: str | None,
    *, active_profile: str = "", profile: dict | None = None,
) -> dict:
    """Snapshot one catalog entry for the client (effective value + metadata)."""
    from ..settings import PROFILE_MANAGED_KEYS

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
    if active_profile and spec["key"] in PROFILE_MANAGED_KEYS:
        item["source"] = "profile"
    if spec.get("options"):
        item["options"] = spec["options"]
    if spec.get("secret"):
        # Never return the secret; just whether a key is held (override or the
        # active profile's DB-only credential).
        item["has_value"] = bool(profile and profile.get("api_key")) if active_profile else value is not None
    return item


@router.get("/api/settings")
async def get_settings():
    """List editable settings with effective values and source."""
    from ..server import get_config
    from ..settings import (
        SETTINGS_CATALOG, provider_profile_store, settings_store,
    )

    if not settings_store.is_loaded():
        await settings_store.load()
    cfg = get_config()
    active = str(settings_store.get("llm.active_profile") or "").strip()
    await provider_profile_store.load()
    profile = provider_profile_store.get(active) if active else None
    return {
        "settings": [
            _catalog_payload(
                spec, cfg, settings_store.get(spec["key"]),
                active_profile=active, profile=profile,
            )
            for spec in SETTINGS_CATALOG
        ]
    }


@router.put("/api/settings")
async def update_settings(payload: dict):
    """Persist UI overrides. Restart-required keys are saved but not applied live.

    While a DB provider profile is active, its identity/auth fields are edited
    in place instead of becoming flat overrides, so the profile stays the single
    source of truth and secrets never reach config.overrides.toml.
    """
    from ..server import get_config
    from ..settings import (
        BY_KEY,
        PROFILE_FIELD_FOR_KEY,
        PROFILE_MANAGED_KEYS,
        apply_active_profile,
        apply_settings_overrides,
        coerce,
        provider_profile_store,
        remove_override,
        settings_store,
        validate_setting,
        write_override,
    )

    if not settings_store.is_loaded():
        await settings_store.load()
    cfg = get_config()
    active_before = str(settings_store.get("llm.active_profile") or "").strip()
    applied, restart_required, profile_edits = [], [], []
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
        if key == "llm.active_profile" and coerced:
            await provider_profile_store.load()
            if provider_profile_store.get(coerced) is None:
                raise HTTPException(status_code=404, detail=f"Unknown provider profile: {coerced}")
        if spec["key"] == "permissions.trust_all" and coerced == "session":
            # "This session" is ephemeral: apply it live, drop any persisted
            # override (DB + overrides file) so it reverts after a restart.
            await settings_store.clear(key)
            remove_override(cfg, key)
            section = getattr(cfg, spec["section"])
            setattr(section, spec["field"], coerced)
            applied.append(key)
            continue
        if active_before and key in PROFILE_MANAGED_KEYS:
            await provider_profile_store.update_field(
                active_before, PROFILE_FIELD_FOR_KEY[key], coerced
            )
            profile_edits.append(key)
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
    if "llm.active_profile" in applied:
        if str(settings_store.get("llm.active_profile") or "").strip():
            await apply_active_profile(cfg)
        else:
            # Clearing the profile must restore file/default llm.* values.
            await apply_settings_overrides(cfg)
    elif profile_edits:
        await apply_active_profile(cfg)
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

    Hardened against SSRF and stored-key exfiltration:
      - Only ``http``/``https`` is accepted, and cloud-metadata / link-local /
        unspecified endpoints are refused (they are never an LLM backend).
      - A stored key (flat config or a named profile) is attached **only** when
        the probe targets that credential's own host. A client-supplied
        ``base_url`` pointing somewhere else must bring its own ``api_key``, so
        an attacker cannot redirect a DB-only secret to a host they control.
      - A profile with a blank ``base_url`` owns the default OpenAI host; it
        never inherits the flat configured host just because the profile was
        selected during a probe.
    """
    import httpx

    from ..capabilities import context_window_from_body
    from ..server import get_config
    from ..settings import provider_profile_store

    cfg = get_config()
    configured_base = cfg.llm.base_url or ""
    client_base = ""
    client_key = ""
    profile_name = ""
    if payload:
        client_base = str(payload.get("base_url") or "")
        client_key = str(payload.get("api_key") or "")
        profile_name = str(payload.get("profile") or "")

    profile = None
    if profile_name:
        await provider_profile_store.load()
        profile = provider_profile_store.get(profile_name)
        if profile is None:
            return {"ok": False, "error": f"Unknown provider profile: {profile_name}"}

    profile_base = (profile or {}).get("base_url") or ""
    # A selected profile is self-contained: a blank base_url means the default
    # OpenAI endpoint, not the flat configured backend. Only unprofiled probes
    # inherit ``configured_base``.
    base_url = client_base or profile_base or ("" if profile else configured_base)
    parsed = urlsplit(base_url if "//" in base_url else "//" + base_url) if base_url else None
    scheme = parsed.scheme if parsed and parsed.scheme else "https"
    if scheme not in ("http", "https"):
        return {"ok": False, "error": f"Unsupported scheme: {scheme}"}

    default_host = _url_host("https://api.openai.com/v1")
    target_host = _url_host(base_url) or default_host
    if _is_metadata_host(target_host) or (target_host and _resolve_is_metadata(target_host)):
        return {"ok": False, "error": "Refusing to contact a metadata/link-local endpoint"}
    key_host = (_url_host(profile_base) or default_host) if profile else (_url_host(configured_base) or default_host)

    # Only reuse a stored key when the probe stays on that credential's own
    # host. A foreign base_url must bring its own key, even when a profile was
    # named, so a crafted request cannot exfiltrate a DB-only credential.
    if client_key:
        api_key = client_key
    elif profile:
        api_key = (profile.get("api_key") or "") if target_host == key_host else ""
    elif not client_base or target_host == key_host:
        api_key = cfg.llm.api_key
    else:
        api_key = ""

    endpoint = (base_url or "https://api.openai.com/v1").rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    timeout = 10.0
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
    # The admin model picker filters client-side, so return enough models for a
    # real search instead of the old 25-model sample (capped to bound payloads).
    result = {"ok": True, "endpoint": endpoint, "models": models[:500]}
    if context_window is not None:
        result["context_window"] = context_window
    return result


# --- DB-only provider profiles --------------------------------------------- #


@router.get("/api/provider-profiles")
async def list_provider_profiles():
    """List masked profiles plus the active name (never returns API keys)."""
    from ..settings import provider_profile_store, settings_store

    if not settings_store.is_loaded():
        await settings_store.load()
    await provider_profile_store.load()
    return {
        "active": str(settings_store.get("llm.active_profile") or "").strip(),
        "profiles": [provider_profile_store.masked(p) for p in provider_profile_store.all()],
    }


@router.post("/api/provider-profiles")
async def save_provider_profile(payload: dict):
    """Create or update a profile. A blank api_key means 'keep the stored key'."""
    from ..settings import (
        normalize_profile_values, provider_profile_store, validate_profile_name,
    )

    name = str(payload.get("name") or "").strip()
    error = validate_profile_name(name)
    if error:
        raise HTTPException(status_code=422, detail=error)
    await provider_profile_store.load()
    values = {k: v for k, v in payload.items()
              if k in ("provider", "base_url", "api_key", "model", "context_window",
                       "vision", "audio", "timeout") and k != "name"}
    try:
        values = normalize_profile_values(values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = await provider_profile_store.upsert(name, values)
    return provider_profile_store.masked(row)


@router.delete("/api/provider-profiles/{name}")
async def delete_provider_profile(name: str):
    """Delete a profile; the active one must be deactivated first."""
    from ..server import get_config
    from ..settings import apply_settings_overrides, provider_profile_store, settings_store

    if not settings_store.is_loaded():
        await settings_store.load()
    await provider_profile_store.load()
    if provider_profile_store.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider profile: {name}")
    active = str(settings_store.get("llm.active_profile") or "").strip()
    if active == name:
        raise HTTPException(status_code=409, detail="Deactivate the profile before deleting it")
    await provider_profile_store.delete(name)
    # Keep the active profile's hydrated config consistent if the deleted row
    # was still cached in a long-running process.
    if active:
        await apply_settings_overrides(get_config())
    return {"ok": True, "name": name}
