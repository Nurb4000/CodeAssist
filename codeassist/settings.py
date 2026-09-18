"""UI-managed settings layered on top of config.toml.

config.toml stays the base source of truth; entries in the `settings` table
override specific fields at boot and can be edited via the admin page. Changes
marked `restart_required` only take effect after a server restart.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .session import get_db

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
]


BY_KEY = {spec["key"]: spec for spec in SETTINGS_CATALOG}


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
            async with get_db() as db:
                async with db.execute("SELECT key, value FROM settings") as cursor:
                    rows = await cursor.fetchall()
            self._cache = {row["key"]: row["value"] for row in rows}
            self._loaded = True
        except Exception as e:
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
        except Exception as e:
            log.debug("Failed to persist setting %s: %s", key, e)

    async def clear(self, key: str) -> None:
        self._cache.pop(key, None)
        try:
            async with get_db() as db:
                await db.execute("DELETE FROM settings WHERE key = ?", [key])
                await db.commit()
        except Exception as e:
            log.debug("Failed to clear setting %s: %s", key, e)


settings_store = SettingsStore()


async def apply_settings_overrides(cfg: Any) -> None:
    """Apply persisted UI overrides onto the live Config object (at boot)."""
    try:
        await settings_store.load()
    except Exception as e:
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