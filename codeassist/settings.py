"""UI-managed settings layered on top of config.toml.

config.toml stays the base source of truth; entries in the `settings` table
override specific fields at boot and can be edited via the admin page. Changes
marked `restart_required` only take effect after a server restart.

Edits are also written back to a sibling ``config.overrides.toml`` so they
survive a data-dir reset even when ``config.toml`` is mounted read-only (e.g. in
Docker). ``Config.load`` merges that file on top of the base config.
"""
from __future__ import annotations

import json
import logging
import tomllib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from .config import Config
from .session import get_db

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# A declarative catalog of every editable setting. `section` is the Config
# attribute (cfg.llm, cfg.server, ...), `field` the attribute within it.
SETTINGS_CATALOG: list[dict[str, Any]] = [
    # --- LLM ---
    {"key": "llm.provider", "section": "llm", "field": "provider", "type": "str",
     "label": "Provider", "description": "openai API or a custom-compatible endpoint.",
     "group": "LLM", "options": ["openai", "custom"], "restart_required": False},
    {"key": "llm.base_url", "section": "llm", "field": "base_url", "type": "str",
     "label": "Base URL", "description": "e.g. http://localhost:8080/v1 (blank = api.openai.com).",
     "group": "LLM", "restart_required": False},
    {"key": "llm.api_key", "section": "llm", "field": "api_key", "type": "str", "secret": True,
     "label": "API Key", "description": "Stored masked. env:VAR is resolved from the environment.",
     "group": "LLM", "restart_required": False},
    {"key": "llm.model", "section": "llm", "field": "model", "type": "str",
     "label": "Model", "description": "Model identifier, e.g. gpt-4o or your server's model name.",
     "group": "LLM", "restart_required": False},
    {"key": "llm.temperature", "section": "llm", "field": "temperature", "type": "float",
     "label": "Temperature", "description": "Sampling temperature (0 = deterministic).",
     "group": "LLM", "restart_required": False},
    {"key": "llm.max_tokens", "section": "llm", "field": "max_tokens", "type": "int",
     "label": "Max tokens", "description": "Max tokens per response.",
     "group": "LLM", "restart_required": False},
    {"key": "llm.context_window", "section": "llm", "field": "context_window", "type": "int",
     "label": "Context window", "description": "Model context window size for budgeting.",
     "group": "LLM", "restart_required": False},
    {"key": "llm.vision", "section": "llm", "field": "vision", "type": "bool",
     "label": "Vision capable", "description": "Manual override; auto-detected otherwise.",
     "group": "LLM", "restart_required": False},
    {"key": "llm.timeout", "section": "llm", "field": "timeout", "type": "int",
     "label": "LLM timeout (s)", "description": "Seconds without LLM output before the request is aborted. Raise for slower hardware (default 360).",
     "group": "LLM", "restart_required": False},

    # --- Server ---
    {"key": "server.host", "section": "server", "field": "host", "type": "str",
     "label": "Host", "description": "Restart required.",
     "group": "Server", "restart_required": True},
    {"key": "server.port", "section": "server", "field": "port", "type": "int",
     "label": "Port", "description": "Restart required.",
     "group": "Server", "restart_required": True},
    {"key": "server.workspace", "section": "server", "field": "workspace", "type": "str",
     "label": "Workspace", "description": "Absolute path. Restart required.",
     "group": "Server", "restart_required": True},
    {"key": "server.password", "section": "server", "field": "password", "type": "str", "secret": True,
     "label": "Password", "description": "Optional access password. Restart required.",
     "group": "Server", "restart_required": True},

    # --- Agent ---
    {"key": "agent.name", "section": "agent", "field": "name", "type": "str",
     "label": "Agent name", "description": "Display name for the assistant.",
     "group": "Agent", "restart_required": False},
    {"key": "agent.default_agent", "section": "agent", "field": "default_agent", "type": "str",
     "label": "Default agent", "description": "Agent key used for new sessions.",
     "group": "Agent", "restart_required": False},
    {"key": "agent.max_iterations", "section": "agent", "field": "max_iterations", "type": "int",
     "label": "Max iterations", "description": "Tool-call loop budget per turn.",
     "group": "Agent", "restart_required": False},

    # --- Tools ---
    {"key": "tools.shell_timeout", "section": "tools", "field": "shell_timeout", "type": "int",
     "label": "Shell timeout", "description": "Seconds before a shell command is killed.",
     "group": "Tools", "restart_required": False},
    {"key": "tools.max_output_chars", "section": "tools", "field": "max_output_chars", "type": "int",
     "label": "Max output chars", "description": "Truncation limit for tool output.",
     "group": "Tools", "restart_required": False},

    # --- Features (subsystems init at boot; toggles need a restart) ---
    {"key": "mcp.enabled", "section": "mcp", "field": "enabled", "type": "bool",
     "label": "MCP servers", "description": "Restart required.",
     "group": "Features", "restart_required": True},
    {"key": "skills.enabled", "section": "skills", "field": "enabled", "type": "bool",
     "label": "Skills", "description": "Restart required.",
     "group": "Features", "restart_required": True},
    {"key": "plugins.enabled", "section": "plugins", "field": "enabled", "type": "bool",
     "label": "Plugins", "description": "Restart required.",
     "group": "Features", "restart_required": True},
    {"key": "lsp.enabled", "section": "lsp", "field": "enabled", "type": "bool",
     "label": "LSP servers", "description": "Restart required.",
     "group": "Features", "restart_required": True},
    {"key": "git.enabled", "section": "git", "field": "enabled", "type": "bool",
     "label": "Git integration", "description": "Restart required.",
     "group": "Features", "restart_required": True},

    # --- Permissions ---
    # "session" is ephemeral: applied live only and never persisted, so it
    # reverts after a server restart. "always" is persisted like any other UI
    # setting and survives restarts. See config.PermissionConfig.
    {"key": "permissions.trust_all", "section": "permissions", "field": "trust_all", "type": "str",
     "label": "Trust all tools",
     "description": "Auto-allow every tool call without confirmation. 'This session' lasts until the server restarts; 'Always' persists. Overrides per-tool rules and skips prompts.",
     "group": "Permissions", "options": ["ask", "session", "always"], "restart_required": False},
]


BY_KEY = {spec["key"]: spec for spec in SETTINGS_CATALOG}

# Declarative per-field constraints merged into the matching catalog entry.
# ``min``/``max`` apply to numbers, ``url`` validates HTTP(S) URLs, ``nonempty``
# requires a non-blank string. These turn naive type-coercion into real
# validation so the admin Settings tab rejects bad values instead of storing
# them and failing silently at the next boot.
_CONSTRAINTS: dict[str, dict] = {
    "llm.provider": {},  # options already enforced in validate_setting
    "llm.base_url": {"url": True},
    "llm.model": {"nonempty": True},
    "llm.temperature": {"min": 0.0, "max": 2.0},
    "llm.max_tokens": {"min": 1},
    "llm.context_window": {"min": 1},
    "llm.timeout": {"min": 5},
    "server.port": {"min": 1, "max": 65535},
    "agent.max_iterations": {"min": 1},
    "tools.shell_timeout": {"min": 1},
    "tools.max_output_chars": {"min": 1},
}
for _spec in SETTINGS_CATALOG:
    _spec.update(_CONSTRAINTS.get(_spec["key"], {}))

# Keys whose TOML location differs from [section][field]. temperature and
# max_tokens live under [llm.parameters] in the file, not directly on [llm].
_TOML_PATH_OVERRIDES: dict[str, list[str]] = {
    "llm.temperature": ["llm", "parameters", "temperature"],
    "llm.max_tokens": ["llm", "parameters", "max_tokens"],
}


def _toml_path_for(spec: dict) -> list[str]:
    return _TOML_PATH_OVERRIDES.get(
        spec["key"], [spec["section"], spec["field"]]
    )


def coerce(value: Any, value_type: str) -> Any:
    """Coerce a raw setting value to its declared type."""
    if value_type == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    return str(value)


def _encode(value: Any) -> str:
    return json.dumps(value) if not isinstance(value, str) else value


def _decode(raw: str, value_type: str) -> Any:
    try:
        return coerce(raw, value_type)
    except (ValueError, TypeError):
        return None


class SettingsStore:
    """Persisted UI overrides, cached in memory after load."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._loaded = False

    async def load(self) -> None:
        try:
            async with get_db() as db, db.execute("SELECT key, value FROM settings") as cursor:
                rows = await cursor.fetchall()
            self._cache = {row["key"]: row["value"] for row in rows}
            self._loaded = True
        except Exception as e:  # noqa: BLE001
            log.debug("Failed to load settings overrides: %s", e)

    def is_loaded(self) -> bool:
        return self._loaded

    def get(self, key: str) -> str | None:
        return self._cache.get(key)

    def all(self) -> dict[str, str]:
        return dict(self._cache)

    async def set(self, key: str, value: Any) -> None:
        encoded = _encode(value)
        self._cache[key] = encoded
        try:
            async with get_db() as db:
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    [key, encoded, _now_iso()],
                )
                await db.commit()
        except Exception as e:  # noqa: BLE001
            log.debug("Failed to persist setting %s: %s", key, e)

    async def clear(self, key: str) -> None:
        self._cache.pop(key, None)
        try:
            async with get_db() as db:
                await db.execute("DELETE FROM settings WHERE key = ?", [key])
                await db.commit()
        except Exception as e:  # noqa: BLE001
            log.debug("Failed to clear setting %s: %s", key, e)


settings_store = SettingsStore()


async def apply_settings_overrides(cfg: Any) -> None:
    """Apply persisted UI overrides onto the live Config object (at boot)."""
    try:
        await settings_store.load()
    except Exception as e:  # noqa: BLE001
        log.debug("Settings override load skipped: %s", e)
        return
    for spec in SETTINGS_CATALOG:
        raw = settings_store.get(spec["key"])
        if raw is None:
            continue
        decoded = _decode(raw, spec["type"])
        if decoded is None:
            log.debug("Ignoring invalid override for %s: %r", spec["key"], raw)
            continue
        section = getattr(cfg, spec["section"], None)
        if section is None:
            continue
        setattr(section, spec["field"], decoded)
    log.info("Applied %d settings override(s) from DB", len(settings_store.all()))


async def clear_override(key: str, cfg: Any) -> None:
    """Remove a UI override, restoring the file/default value into the live config."""
    spec = BY_KEY.get(key)
    if not spec:
        return
    await settings_store.clear(key)
    if spec.get("restart_required"):
        return
    # Restore the value config.toml (or the dataclass default) would provide.
    fresh = Config.load()
    fresh_section = getattr(fresh, spec["section"], None)
    if fresh_section is None:
        return
    live_section = getattr(cfg, spec["section"])
    setattr(live_section, spec["field"], getattr(fresh_section, spec["field"]))


def validate_setting(spec: dict, value: Any) -> str | None:
    """Return an error message if ``value`` violates the spec's constraints.

    ``None`` means the (already coerced) value is acceptable. Checks enum
    options, numeric bounds, HTTP(S) URL format, and non-empty strings.
    """
    if "options" in spec and value not in spec["options"]:
        return f"{spec['label']} must be one of {spec['options']}"
    lo = spec.get("min")
    hi = spec.get("max")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if lo is not None and value < lo:
            return f"{spec['label']} must be >= {lo}"
        if hi is not None and value > hi:
            return f"{spec['label']} must be <= {hi}"
    if spec.get("url") and value not in ("", None):
        parsed = urlparse(str(value))
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return f"{spec['label']} must be a valid HTTP(S) URL"
    if spec.get("nonempty") and str(value).strip() == "":
        return f"{spec['label']} cannot be empty"
    return None


# --- config.overrides.toml sync ------------------------------------------- #


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{_toml_escape(str(value))}"'


def _toml_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )


def _toml_dump(data: dict) -> str:
    """Serialize a nested dict of scalar leaves to TOML text.

    Only what the settings API writes is needed (tables of scalars); empty
    sub-tables are skipped so deleted keys disappear on the next write.
    """
    lines: list[str] = []

    def write_table(path: list[str], table: dict) -> None:
        body = []
        for key, val in table.items():
            if isinstance(val, dict):
                if val:  # non-empty -> nested table header
                    write_table(path + [key], val)
            else:
                body.append(f"{key} = {_toml_scalar(val)}")
        if body:
            header = ".".join(path)
            if header:
                lines.append(f"[{header}]")
            lines.extend(body)

    write_table([], data)
    return "\n".join(lines) + ("\n" if lines else "")


def _nested_set(data: dict, path: list[str], value: Any) -> None:
    *parents, leaf = path
    cur = data
    for key in parents:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[leaf] = value


def _nested_delete(data: dict, path: list[str]) -> None:
    """Pop the leaf at ``path`` and prune any tables that become empty."""
    *parents, leaf = path
    cur = data
    for key in parents:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            return
        cur = nxt
    cur.pop(leaf, None)
    # Walk back up, dropping parent dicts that are now empty.
    for depth in range(len(parents), 0, -1):
        node = data
        for key in parents[: depth - 1]:
            node = node.get(key, {})
            if not isinstance(node, dict):
                return
        child = parents[depth - 1]
        inner = node.get(child)
        if isinstance(inner, dict) and not inner:
            node.pop(child, None)


def write_override(cfg: Config, key: str, value: Any) -> None:
    """Persist a single override into ``config.overrides.toml`` (best effort)."""
    spec = BY_KEY.get(key)
    if not spec:
        return
    path = _toml_path_for(spec)
    opath = cfg.overrides_path
    data: dict = {}
    if opath.exists():
        try:
            with open(opath, "rb") as f:
                data = tomllib.load(f)
        except Exception:  # pragma: no cover - corrupt file, start fresh  # noqa: BLE001
            data = {}
    _nested_set(data, path, value)
    try:
        opath.parent.mkdir(parents=True, exist_ok=True)
        opath.write_text(_toml_dump(data), encoding="utf-8")
    except Exception as e:  # pragma: no cover - read-only mount, etc.  # noqa: BLE001
        log.warning("Could not write overrides file %s: %s", opath, e)


def remove_override(cfg: Config, key: str) -> None:
    """Remove a single override from ``config.overrides.toml`` (best effort)."""
    spec = BY_KEY.get(key)
    if not spec:
        return
    opath = cfg.overrides_path
    if not opath.exists():
        return
    try:
        with open(opath, "rb") as f:
            data = tomllib.load(f)
    except Exception:  # pragma: no cover  # noqa: BLE001
        return
    _nested_delete(data, _toml_path_for(spec))
    try:
        opath.write_text(_toml_dump(data), encoding="utf-8")
    except Exception as e:  # pragma: no cover  # noqa: BLE001
        log.warning("Could not rewrite overrides file %s: %s", opath, e)