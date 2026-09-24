import os
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    # Provider is a required enum; "openai" is the neutral default and works for
    # both OpenAI and OpenAI-compatible backends (llama.cpp, vLLM) via base_url.
    provider: str = "openai"
    # No baked-in model default: LLM identity is configured in the UI (Settings
    # > LLM) and persisted in the DB between sessions. An empty model simply
    # means "not configured yet" — the app boots fine and chat prompts the user
    # to set one. base_url/api_key follow the same pattern (empty until set).
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.0
    max_tokens: int = 8192
    context_window: int = 128000
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    embedding_model: str = ""
    vision: bool = False
    # Seconds without LLM output before the request/stream is declared timed
    # out. Generous for slower local hardware (360s = 3x the historical 120s).
    timeout: int = 360


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8090
    workspace: str = "."
    password: str = ""


@dataclass
class AgentConfig:
    max_iterations: int = 30
    name: str = "CodeAssist"
    default_agent: str = "default"
    subagent_depth: int = 1  # Max nested subagent depth (0 = no subagents)
    # Auto-creation settings
    auto_create_skills: bool = True
    auto_create_tools: bool = False  # Disabled by default (security)
    max_auto_creations: int = 3  # Per session limit
    min_confidence: float = 0.7  # Threshold for auto-creation


@dataclass
class ToolConfig:
    shell_timeout: int = 120
    max_output_chars: int = 20000
    webfetch_max_chars: int = 30000
    websearch_max_chars: int = 30000
    websearch_engine: str = "duckduckgo"
    tool_output_max_tokens: int = 4000


@dataclass
class MCPConfig:
    enabled: bool = False
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class SkillsConfig:
    enabled: bool = True
    directories: list[str] = field(
        default_factory=lambda: ["codeassist/skills", "runtime/skills"]
    )


@dataclass
class PluginConfig:
    enabled: bool = False
    directories: list[str] = field(default_factory=lambda: ["codeassist/plugins"])


@dataclass
class LSPConfig:
    enabled: bool = False
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class GitConfig:
    enabled: bool = True
    auto_detect: bool = True


@dataclass
class ToolOutputConfig:
    max_lines: int = 2000
    max_bytes: int = 51200
    retention_days: int = 7


@dataclass
class InstructionsConfig:
    paths: list[str] = field(default_factory=list)
    disable_project_config: bool = False


@dataclass
class PermissionConfig:
    # Trust-all mode for tool confirmations:
    #   "ask"     — prompt the user for every tool call (default)
    #   "session" — auto-allow everything until the server restarts (ephemeral;
    #               set from the admin Settings UI, never persisted so it reverts
    #               on a restart)
    #   "always"  — auto-allow everything, persisted across restarts
    trust_all: str = "ask"


@dataclass
class CompactionConfig:
    enabled: bool = True
    mode: str = "llm"  # "llm" or "text"
    threshold_pct: int = 75
    keep_recent: int = 20
    tail_turns: int = 2  # Recent turns to preserve intact
    preserve_recent_tokens: int = 4000  # Token budget for recent context
    model: str = ""  # Optional: cheaper model for compaction (empty = use main model)
    tool_result_max_tokens: int = 4000


def _deep_merge(base: dict, override: Any) -> dict:
    """Recursively merge ``override`` into ``base`` (mutates and returns base)."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    plugins: PluginConfig = field(default_factory=PluginConfig)
    lsp: LSPConfig = field(default_factory=LSPConfig)
    git: GitConfig = field(default_factory=GitConfig)
    tool_output: ToolOutputConfig = field(default_factory=ToolOutputConfig)
    instructions: InstructionsConfig = field(default_factory=InstructionsConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    workspace: Path = field(default_factory=lambda: Path.cwd())

    @property
    def overrides_path(self) -> Path:
        """Sibling ``config.overrides.toml`` holding UI-synced file-managed edits."""
        src = getattr(self, "_source", None)
        if src is None:
            src = Path("config.toml").resolve()
        return Path(src).parent / "config.overrides.toml"

    @classmethod
    def load(cls, path: str | Path = "config.toml") -> "Config":
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path, "rb") as f:
            raw = tomllib.load(f)

        # Merge UI-managed overrides (config.overrides.toml) on top of the base
        # config. This file is written back by the settings API so edits survive
        # a data-dir reset even when config.toml itself is mounted read-only
        # (e.g. in Docker). A missing/invalid overrides file is non-fatal.
        overrides_path = Path(path).resolve().parent / "config.overrides.toml"
        if overrides_path.exists():
            try:
                with open(overrides_path, "rb") as of:
                    raw = _deep_merge(raw, tomllib.load(of))
            except Exception as e:  # pragma: no cover - malformed overrides file
                log.warning("Ignoring unreadable overrides file %s: %s", overrides_path, e)

        llm_raw = raw.get("llm", {})
        params = llm_raw.pop("parameters", {})
        api_key = llm_raw.get("api_key", "")
        if api_key.startswith("env:"):
            api_key = os.environ.get(api_key[4:], "")

        config = cls(
            llm=LLMConfig(
                provider=llm_raw.get("provider", "openai"),
                model=llm_raw.get("model", "gpt-4o"),
                api_key=api_key,
                base_url=llm_raw.get("base_url", ""),
                temperature=params.get("temperature", 0.0),
                max_tokens=params.get("max_tokens", 8192),
                context_window=params.get("context_window", 128000),
                frequency_penalty=params.get("frequency_penalty", 0.0),
                presence_penalty=params.get("presence_penalty", 0.0),
                vision=llm_raw.get("vision", False),
                timeout=llm_raw.get("timeout", 360),
            ),
            server=ServerConfig(
                host=raw.get("server", {}).get("host", "127.0.0.1"),
                port=raw.get("server", {}).get("port", 8090),
                workspace=raw.get("server", {}).get("workspace", "."),
                password=raw.get("server", {}).get("password", ""),
            ),
            agent=AgentConfig(
                max_iterations=raw.get("agent", {}).get("max_iterations", 30),
                name=raw.get("agent", {}).get("name", "CodeAssist"),
                default_agent=raw.get("agent", {}).get("default_agent", "default"),
                subagent_depth=raw.get("agent", {}).get("subagent_depth", 1),
            ),
            tools=ToolConfig(
                shell_timeout=raw.get("tools", {}).get("shell_timeout", 120),
                max_output_chars=raw.get("tools", {}).get("max_output_chars", 20000),
                webfetch_max_chars=raw.get("tools", {}).get("webfetch_max_chars", 30000),
                websearch_max_chars=raw.get("tools", {}).get("websearch_max_chars", 30000),
                websearch_engine=raw.get("tools", {}).get("websearch_engine", "duckduckgo"),
                tool_output_max_tokens=raw.get("tools", {}).get("tool_output_max_tokens", 4000),
            ),
            mcp=MCPConfig(
                enabled=raw.get("mcp", {}).get("enabled", False),
                servers=raw.get("mcp", {}).get("servers", {}),
            ),
            skills=SkillsConfig(
                enabled=raw.get("skills", {}).get("enabled", True),
                directories=raw.get("skills", {}).get(
                    "directories", ["codeassist/skills", "runtime/skills"]
                ),
            ),
            plugins=PluginConfig(
                enabled=raw.get("plugins", {}).get("enabled", False),
                directories=raw.get("plugins", {}).get("directories", ["codeassist/plugins"]),
            ),
            lsp=LSPConfig(
                enabled=raw.get("lsp", {}).get("enabled", False),
                servers=raw.get("lsp", {}).get("servers", {}),
            ),
            git=GitConfig(
                enabled=raw.get("git", {}).get("enabled", True),
                auto_detect=raw.get("git", {}).get("auto_detect", True),
            ),
            tool_output=ToolOutputConfig(
                max_lines=raw.get("tool_output", {}).get("max_lines", 2000),
                max_bytes=raw.get("tool_output", {}).get("max_bytes", 51200),
                retention_days=raw.get("tool_output", {}).get("retention_days", 7),
            ),
            instructions=InstructionsConfig(
                paths=raw.get("instructions", {}).get("paths", []),
                disable_project_config=raw.get("instructions", {}).get("disable_project_config", False),
            ),
            compaction=CompactionConfig(
                enabled=raw.get("compaction", {}).get("enabled", True),
                mode=raw.get("compaction", {}).get("mode", "llm"),
                threshold_pct=raw.get("compaction", {}).get("threshold_pct", 75),
                keep_recent=raw.get("compaction", {}).get("keep_recent", 20),
                tail_turns=raw.get("compaction", {}).get("tail_turns", 2),
                preserve_recent_tokens=raw.get("compaction", {}).get("preserve_recent_tokens", 4000),
                model=raw.get("compaction", {}).get("model", ""),
                tool_result_max_tokens=raw.get("compaction", {}).get("tool_result_max_tokens", 4000),
            ),
            permissions=PermissionConfig(
                trust_all=raw.get("permissions", {}).get("trust_all", "ask"),
            ),
        )
        config.workspace = Path(
            os.environ.get("CODEASSIST_WORKSPACE", config.server.workspace)
        ).resolve()
        # Allow env overrides for Docker / containerized deployments
        if os.environ.get("CODEASSIST_HOST"):
            config.server.host = os.environ["CODEASSIST_HOST"]
        if os.environ.get("CODEASSIST_PORT"):
            config.server.port = int(os.environ["CODEASSIST_PORT"])
        config._source = Path(path).resolve()
        return config


def load_config(path: str | Path = "config.toml") -> Config:
    """Convenience function to load config from file."""
    return Config.load(path)
