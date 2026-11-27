import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cost_tracker import BudgetConfig
from .transcribe import TranscriptionConfig

log = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    # Provider is a required enum; "openai" is the neutral default and works for
    # both OpenAI and OpenAI-compatible backends (llama.cpp, vLLM) via base_url.
    provider: str = "openai"
    # Name of the DB-backed provider profile currently hydrating this config.
    # Blank means the ordinary provider/base_url/key/model fields are in use.
    active_profile: str = ""
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
    # Audio input capability override. Mirrors `vision`: a manual override always
    # wins; otherwise it is auto-detected by probing the backend's /v1/models.
    audio: bool = False
    # Seconds without LLM output before the request/stream is declared timed
    # out. Generous for slower local hardware (900s = 7.5x the historical 120s).
    timeout: int = 900


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8090
    workspace: str = "."
    password: str = ""


@dataclass
class AgentConfig:
    # Default step budget for agents that do not define their own per-agent
    # 'steps' (built-ins all define one, so they bind instead). It also feeds
    # subagents that fall back to it. The per-agent step budget is the graceful
    # wrap-up point; this value is only consulted when no per-agent budget is set.
    max_iterations: int = 200
    name: str = "CodeAssist"
    default_agent: str = "default"
    subagent_depth: int = 1  # Max nested subagent depth (0 = no subagents)
    # Auto-creation settings
    auto_create_skills: bool = False  # Disabled by default (low-quality auto-skills); opt in explicitly
    auto_create_tools: bool = False  # Disabled by default (security)
    max_auto_creations: int = 3  # Per session limit
    min_confidence: float = 0.7  # Threshold for auto-creation


@dataclass
class CodeModeConfig:
    """Confined JS execute-tool runtime (D1)."""

    enabled: bool = True
    timeout_ms: int = 120000
    max_tool_calls: int = 64
    max_output_chars: int = 20000


@dataclass
class ToolConfig:
    shell_timeout: int = 120
    max_output_chars: int = 20000
    webfetch_max_chars: int = 30000
    websearch_max_chars: int = 30000
    websearch_engine: str = "duckduckgo"
    # Tokens of a single tool result kept in the conversation. Kept lean on
    # purpose: oversized tool outputs (grep/read/shell dumps) fill context fast
    # and make the model lose track mid-run. opencode caps this at ~2k chars;
    # 2000 tokens is a middle ground that stays focused while preserving detail.
    tool_output_max_tokens: int = 2000
    codemode: CodeModeConfig = field(default_factory=CodeModeConfig)


@dataclass
class RemoteTestVMConfig:
    """SSH target used by ``test_runner`` for tests needing a full VM.

    ``enabled`` is true by default but an empty ``host`` makes the target
    unavailable, so an unconfigured install simply falls back to local tests.
    All values can be overridden at launch with ``CODEASSIST_REMOTE_TEST_VM_*``
    environment variables; empty env values are ignored so TOML stays the
    source of truth unless an override is explicitly supplied.
    """

    enabled: bool = True
    host: str = ""
    port: int = 22
    user: str = ""
    # A leading ~ is expanded against the *remote* user's $HOME by test_runner.
    # /tmp is deliberately not used: it is shared scratch, size-limited on many
    # systems and subject to cleanup.
    remote_dir: str = "~/codeassist-tests"
    # Empty => use the SSH client's default identity/known_hosts locations.
    key_path: str = ""
    known_hosts: str = ""
    # Prefer key auth. If set (plain text or "env:VAR"), sshpass -e is used.
    password: str = ""
    # "rsync" syncs the current workspace before running; "none" uses the
    # checkout already present at remote_dir.
    sync: str = "rsync"
    rsync_excludes: list[str] = field(default_factory=lambda: [
        ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
        "*.egg-info", "runtime/tool-output",
    ])
    connect_timeout: int = 5
    test_timeout: int = 300
    # Only VM-unavailable errors trigger this; a failing remote suite is a result.
    fallback_to_local: bool = True
    # false => StrictHostKeyChecking=no with UserKnownHostsFile=/dev/null.
    strict_host_key_checking: bool = True
    # Trusted shell preamble run in remote_dir before the test command, e.g.
    # "source /opt/venv/bin/activate". Never populated from model input.
    remote_env: str = ""


@dataclass
class MCPConfig:
    enabled: bool = False
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class AutomationsConfig:
    """Unattended jobs. Disabled by default: a scheduled shell command runs with
    the server's privileges, so enabling this is an explicit admin decision."""

    enabled: bool = False
    allow_shell: bool = True
    tick_seconds: float = 1.0
    timeout_seconds: int = 300


@dataclass
class SkillsConfig:
    enabled: bool = True
    directories: list[str] = field(
        default_factory=lambda: ["codeassist/skills", "runtime/skills"]
    )
    # The skills shipped inside the package are always discovered, so they stay
    # available no matter what `server.workspace` points at. Set to false to run
    # with workspace-provided skills only.
    include_packaged: bool = True


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
    # Scope of the AGENTS.md/CLAUDE.md walk up from the workspace. "workspace"
    # only inspects the workspace dir; "workspace+parent" also checks one parent
    # (e.g. a monorepo root). The old default walked to filesystem root, so an
    # AGENTS.md in the home dir or "/" became authoritative model instructions.
    scope: str = "workspace+parent"
    # Label remote (fetched) instruction sources as untrusted in the injected
    # block so the model knows they may be authored by a third party rather than
    # being framed as authoritative "should be followed".
    label_remote: bool = True


@dataclass
class PermissionConfig:
    # Trust-all mode for tool confirmations:
    #   "ask"     — prompt the user for every tool call (default)
    #   "session" — auto-allow everything until the server restarts (ephemeral;
    #               set from the admin Settings UI, never persisted so it reverts
    #               on a restart)
    #   "always"  — auto-allow everything, persisted across restarts
    trust_all: str = "ask"


# Valid values for PermissionConfig.trust_all. Anything else in TOML is a typo
# and must fail loudly at load rather than silently falling back to "ask"
# (masking an admin's intent on a security-relevant, auto-approve setting).
_TRUST_ALL_OPTIONS = ("ask", "session", "always")


def _validate_trust_all(permissions_raw: dict) -> str:
    """Return the trusted-all mode from a raw ``[permissions]`` table.

    Raises ``ValueError`` on an unknown value so a malformed config.toml fails
    at startup instead of silently defaulting to "ask" (§13 #14). The admin
    Settings UI rejects the same values via validate_setting; this mirrors that
    guard for the TOML load path.
    """
    value = permissions_raw.get("trust_all", "ask")
    if value not in _TRUST_ALL_OPTIONS:
        raise ValueError(
            f"Invalid permissions.trust_all={value!r} in config.toml; "
            f"must be one of {_TRUST_ALL_OPTIONS}"
        )
    return value


@dataclass
class CompactionConfig:
    enabled: bool = True
    mode: str = "llm"  # "llm" or "text"
    threshold_pct: int = 85
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


def _parse_env_bool(value: str) -> bool | None:
    """Parse a boolean env override, returning None when it is not recognisable."""
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _remote_test_vm_config(raw: dict) -> RemoteTestVMConfig:
    """Build ``RemoteTestVMConfig`` from a raw ``[remote_test_vm]`` TOML table."""

    def _positive_int(name: str, default: int) -> int:
        try:
            value = int(raw.get(name, default))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    def _string_list(name: str, default: list[str]) -> list[str]:
        value = raw.get(name, default)
        if not isinstance(value, list):
            return list(default)
        return [str(item).strip() for item in value if str(item).strip()]

    sync = str(raw.get("sync", "rsync") or "rsync").strip().lower()
    if sync not in {"rsync", "none"}:
        raise ValueError(
            f"Invalid remote_test_vm.sync={raw.get('sync')!r} in config.toml; "
            "must be 'rsync' or 'none'"
        )

    password = str(raw.get("password", "") or "")
    if password.startswith("env:"):
        password = os.environ.get(password[4:], "")

    return RemoteTestVMConfig(
        enabled=bool(raw.get("enabled", True)),
        host=str(raw.get("host", "") or "").strip(),
        port=_positive_int("port", 22),
        user=str(raw.get("user", "") or "").strip(),
        remote_dir=str(raw.get("remote_dir", RemoteTestVMConfig().remote_dir) or "").strip(),
        key_path=str(raw.get("key_path", "") or "").strip(),
        known_hosts=str(raw.get("known_hosts", "") or "").strip(),
        password=password,
        sync=sync,
        rsync_excludes=_string_list("rsync_excludes", RemoteTestVMConfig().rsync_excludes),
        connect_timeout=_positive_int("connect_timeout", 5),
        test_timeout=_positive_int("test_timeout", 300),
        fallback_to_local=bool(raw.get("fallback_to_local", True)),
        strict_host_key_checking=bool(raw.get("strict_host_key_checking", True)),
        remote_env=str(raw.get("remote_env", "") or ""),
    )


@dataclass
class MusicGenConfig:
    # Host/port of the external musicgen (ACE-Step) synthesis server. Defaults
    # target a local instance; set host to the remote device's LAN IP in
    # practice (see findings/musicgen-codeassist-integration-Findings.md).
    # Runtime tool args (host/port) override these values.
    host: str = "localhost"
    port: int = 3000
    timeout_seconds: int = 900


@dataclass
class BrowserConfig:
    """Policy and lifecycle settings for the Playwright-backed ``browser`` tool.

    The browser is a stronger SSRF primitive than webfetch, so its URL policy is
    intentionally separate from ``security.validate_url`` (see
    ``tools/browser.py:validate_browser_url``). Loopback dev servers are the
    primary target; LAN/private and public hosts require explicit opt-in.
    """

    enabled: bool = True
    headless: bool = True
    # Playwright engine. Chromium is the default because it has the widest
    # headless support and a system Chrome fallback; Firefox/WebKit must be
    # installed with `playwright install <engine>`.
    engine: str = "chromium"
    # Permit loopback (127.0.0.0/8, ::1, localhost) on any port.
    allow_local: bool = True
    # Permit arbitrary public hosts. Off by default: local development only.
    allow_public: bool = False
    # Exact origins that are always permitted, e.g. "http://host.docker.internal:5173".
    allowed_origins: list[str] = field(default_factory=list)
    # Hostnames (not ranges) permitted even when they are private/LAN, e.g.
    # "host.docker.internal" or "devbox.lan".
    allowed_private_hosts: list[str] = field(default_factory=list)
    navigation_timeout_seconds: int = 45
    action_timeout_seconds: int = 15
    idle_timeout_seconds: int = 900
    max_pages: int = 8
    viewport_width: int = 1280
    viewport_height: int = 800
    # Optional system Chrome/Chromium path. Empty => Playwright-managed browser,
    # falling back to a discovered system Chrome/Chromium.
    executable_path: str = ""
    # None => add --no-sandbox only when running as root (Docker). Set false to
    # force the sandbox on, true to force it off.
    no_sandbox: bool | None = None


_BROWSER_ENGINES = ("chromium", "firefox", "webkit")


def _browser_config(raw: dict) -> BrowserConfig:
    """Build ``BrowserConfig`` from a raw ``[browser]`` TOML table.

    Unknown keys are ignored (TOML loaders are lenient elsewhere too), but list
    and timeout values are normalized so a typo cannot silently disable the URL
    policy or produce a zero timeout. ``engine`` is validated strictly: an
    unknown value raises ``ValueError`` at load instead of silently selecting
    Chromium.
    """

    def _positive_int(name: str, default: int) -> int:
        try:
            value = int(raw.get(name, default))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    def _string_list(name: str) -> list[str]:
        value = raw.get(name, [])
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    no_sandbox = raw.get("no_sandbox")
    if no_sandbox is not None:
        no_sandbox = bool(no_sandbox)

    engine = str(raw.get("engine", "chromium") or "chromium").strip().lower()
    if engine not in _BROWSER_ENGINES:
        raise ValueError(
            f"Invalid browser.engine={raw.get('engine')!r} in config.toml; "
            f"must be one of {_BROWSER_ENGINES}"
        )

    return BrowserConfig(
        enabled=bool(raw.get("enabled", True)),
        headless=bool(raw.get("headless", True)),
        engine=engine,
        allow_local=bool(raw.get("allow_local", True)),
        allow_public=bool(raw.get("allow_public", False)),
        allowed_origins=_string_list("allowed_origins"),
        allowed_private_hosts=_string_list("allowed_private_hosts"),
        navigation_timeout_seconds=_positive_int("navigation_timeout_seconds", 45),
        action_timeout_seconds=_positive_int("action_timeout_seconds", 15),
        idle_timeout_seconds=_positive_int("idle_timeout_seconds", 900),
        max_pages=_positive_int("max_pages", 8),
        viewport_width=_positive_int("viewport_width", 1280),
        viewport_height=_positive_int("viewport_height", 800),
        executable_path=str(raw.get("executable_path", "") or ""),
        no_sandbox=no_sandbox,
    )


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    remote_test_vm: RemoteTestVMConfig = field(default_factory=RemoteTestVMConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    automations: AutomationsConfig = field(default_factory=AutomationsConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    plugins: PluginConfig = field(default_factory=PluginConfig)
    lsp: LSPConfig = field(default_factory=LSPConfig)
    git: GitConfig = field(default_factory=GitConfig)
    tool_output: ToolOutputConfig = field(default_factory=ToolOutputConfig)
    instructions: InstructionsConfig = field(default_factory=InstructionsConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    # Per-session cost/token budget. Reuses cost_tracker.BudgetConfig so the
    # agent loop enforces exactly what Config.load reads from [cost]; previously
    # this table existed but was never wired here, so CostTracker() was always
    # bare (enabled=False) and enforcement was dead (§7).
    cost: BudgetConfig = field(default_factory=BudgetConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    musicgen: MusicGenConfig = field(default_factory=MusicGenConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    workspace: Path = field(default_factory=lambda: Path.cwd())

    def refresh_workspace(self) -> None:
        """Re-derive the effective ``workspace`` from the current raw setting.

        ``server.workspace`` is the raw string a user can change (config file,
        ``CODEASSIST_WORKSPACE``, or the settings UI); ``workspace`` is the
        resolved absolute path that every tool and route actually reads. They are
        computed at different times, so anything that mutates the raw value must
        call this or the two silently diverge and tools keep operating on the
        previous project.
        """
        raw = getattr(self.server, "workspace", ".")
        self.workspace = Path(os.environ.get("CODEASSIST_WORKSPACE", raw)).resolve()

    def _apply_remote_vm_env_overrides(self) -> None:
        """Apply ``CODEASSIST_REMOTE_TEST_VM_*`` launch overrides.

        Empty values are deliberately ignored: compose templates pass every
        variable through with an empty default, and an empty host would otherwise
        wipe a perfectly good TOML endpoint. This keeps config.toml authoritative
        unless the operator actually supplies an override.
        """
        rvm = self.remote_test_vm
        original_sync = rvm.sync
        string_overrides = {
            "CODEASSIST_REMOTE_TEST_VM_HOST": "host",
            "CODEASSIST_REMOTE_TEST_VM_USER": "user",
            "CODEASSIST_REMOTE_TEST_VM_REMOTE_DIR": "remote_dir",
            "CODEASSIST_REMOTE_TEST_VM_KEY_PATH": "key_path",
            "CODEASSIST_REMOTE_TEST_VM_KNOWN_HOSTS": "known_hosts",
            "CODEASSIST_REMOTE_TEST_VM_PASSWORD": "password",
            "CODEASSIST_REMOTE_TEST_VM_SYNC": "sync",
        }
        for env_name, attr in string_overrides.items():
            value = os.environ.get(env_name)
            if value:
                setattr(rvm, attr, value.strip() if attr != "password" else value)

        int_overrides = {
            "CODEASSIST_REMOTE_TEST_VM_PORT": "port",
            "CODEASSIST_REMOTE_TEST_VM_CONNECT_TIMEOUT": "connect_timeout",
            "CODEASSIST_REMOTE_TEST_VM_TEST_TIMEOUT": "test_timeout",
        }
        for env_name, attr in int_overrides.items():
            value = os.environ.get(env_name)
            if not value:
                continue
            try:
                parsed = int(value)
            except ValueError:
                log.warning("%s='%s' is not an integer; keeping config value %s", env_name, value, getattr(rvm, attr))
                continue
            if parsed > 0:
                setattr(rvm, attr, parsed)
            else:
                log.warning("%s='%s' must be positive; keeping config value %s", env_name, value, getattr(rvm, attr))

        bool_overrides = {
            "CODEASSIST_REMOTE_TEST_VM_ENABLED": "enabled",
            "CODEASSIST_REMOTE_TEST_VM_FALLBACK_TO_LOCAL": "fallback_to_local",
            "CODEASSIST_REMOTE_TEST_VM_STRICT_HOST_KEY_CHECKING": "strict_host_key_checking",
        }
        for env_name, attr in bool_overrides.items():
            value = os.environ.get(env_name)
            if not value:
                continue
            parsed = _parse_env_bool(value)
            if parsed is None:
                log.warning("%s='%s' is not a boolean; keeping config value %s", env_name, value, getattr(rvm, attr))
                continue
            setattr(rvm, attr, parsed)

        # A bad sync value from env would break the tool at call time; validate
        # it here with the same rule as TOML so config failures stay at boot.
        if rvm.sync not in {"rsync", "none"}:
            log.warning(
                "CODEASSIST_REMOTE_TEST_VM_SYNC='%s' is invalid (use rsync|none); "
                "keeping TOML value",
                rvm.sync,
            )
            rvm.sync = original_sync

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
            except Exception as e:  # pragma: no cover - malformed overrides file  # noqa: BLE001
                log.warning("Ignoring unreadable overrides file %s: %s", overrides_path, e)

        llm_raw = raw.get("llm", {})
        params = llm_raw.pop("parameters", {})
        api_key = llm_raw.get("api_key", "")
        if api_key.startswith("env:"):
            api_key = os.environ.get(api_key[4:], "")

        config = cls(
            llm=LLMConfig(
                provider=llm_raw.get("provider", "openai"),
                active_profile=str(llm_raw.get("active_profile", "") or ""),
                # No baked-in default: match LLMConfig.model and the documented
                # "blank until configured in the UI" policy at config.py:16-19.
                model=llm_raw.get("model", ""),
                api_key=api_key,
                base_url=llm_raw.get("base_url", ""),
                temperature=params.get("temperature", 0.0),
                max_tokens=params.get("max_tokens", 8192),
                context_window=params.get("context_window", 128000),
                frequency_penalty=params.get("frequency_penalty", 0.0),
                presence_penalty=params.get("presence_penalty", 0.0),
                # Documented in two places (config.example.toml puts it under
                # [llm.parameters], README.md under [llm]); accept either.
                embedding_model=llm_raw.get("embedding_model") or params.get("embedding_model", ""),
                vision=llm_raw.get("vision", False),
                audio=llm_raw.get("audio", False),
                timeout=llm_raw.get("timeout", 900),
            ),
            server=ServerConfig(
                host=raw.get("server", {}).get("host", "127.0.0.1"),
                port=raw.get("server", {}).get("port", 8090),
                workspace=raw.get("server", {}).get("workspace", "."),
                password=raw.get("server", {}).get("password", ""),
            ),
            agent=AgentConfig(
                max_iterations=raw.get("agent", {}).get("max_iterations", 200),
                name=raw.get("agent", {}).get("name", "CodeAssist"),
                default_agent=raw.get("agent", {}).get("default_agent", "default"),
                subagent_depth=raw.get("agent", {}).get("subagent_depth", 1),
                auto_create_skills=raw.get("agent", {}).get("auto_create_skills", False),
                auto_create_tools=raw.get("agent", {}).get("auto_create_tools", False),
                max_auto_creations=raw.get("agent", {}).get("max_auto_creations", 3),
                min_confidence=raw.get("agent", {}).get("min_confidence", 0.7),
            ),
            tools=(lambda tools_raw, cm_raw: ToolConfig(
                shell_timeout=tools_raw.get("shell_timeout", 120),
                max_output_chars=tools_raw.get("max_output_chars", 20000),
                webfetch_max_chars=tools_raw.get("webfetch_max_chars", 30000),
                websearch_max_chars=tools_raw.get("websearch_max_chars", 30000),
                websearch_engine=tools_raw.get("websearch_engine", "duckduckgo"),
                tool_output_max_tokens=tools_raw.get("tool_output_max_tokens", 2000),
                codemode=CodeModeConfig(
                    enabled=bool(cm_raw.get("enabled", True)),
                    timeout_ms=max(1, int(cm_raw.get("timeout_ms", 120000))),
                    max_tool_calls=max(1, int(cm_raw.get("max_tool_calls", 64))),
                    max_output_chars=max(1000, int(cm_raw.get("max_output_chars", 20000))),
                ),
            ))(raw.get("tools", {}), raw.get("tools", {}).get("codemode", {}) if isinstance(raw.get("tools", {}).get("codemode", {}), dict) else {}),
            remote_test_vm=_remote_test_vm_config(raw.get("remote_test_vm", {})),
            mcp=MCPConfig(
                enabled=raw.get("mcp", {}).get("enabled", False),
                servers=raw.get("mcp", {}).get("servers", {}),
            ),
            automations=AutomationsConfig(
                enabled=raw.get("automations", {}).get("enabled", False),
                allow_shell=raw.get("automations", {}).get("allow_shell", True),
                tick_seconds=float(raw.get("automations", {}).get("tick_seconds", 1.0)),
                timeout_seconds=int(raw.get("automations", {}).get("timeout_seconds", 300)),
            ),
            skills=SkillsConfig(
                enabled=raw.get("skills", {}).get("enabled", True),
                directories=raw.get("skills", {}).get(
                    "directories", ["codeassist/skills", "runtime/skills"]
                ),
                include_packaged=raw.get("skills", {}).get("include_packaged", True),
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
            transcription=TranscriptionConfig(
                engine=raw.get("transcription", {}).get("engine", "local"),
                model=raw.get("transcription", {}).get("model", "base"),
                api_key=raw.get("transcription", {}).get("api_key", ""),
                base_url=raw.get("transcription", {}).get("base_url", ""),
            ),
            tool_output=ToolOutputConfig(
                max_lines=raw.get("tool_output", {}).get("max_lines", 2000),
                max_bytes=raw.get("tool_output", {}).get("max_bytes", 51200),
                retention_days=raw.get("tool_output", {}).get("retention_days", 7),
            ),
            instructions=InstructionsConfig(
                paths=raw.get("instructions", {}).get("paths", []),
                disable_project_config=raw.get("instructions", {}).get("disable_project_config", False),
                scope=raw.get("instructions", {}).get("scope", "workspace+parent"),
                label_remote=raw.get("instructions", {}).get("label_remote", True),
            ),
            compaction=CompactionConfig(
                enabled=raw.get("compaction", {}).get("enabled", True),
                mode=raw.get("compaction", {}).get("mode", "llm"),
                threshold_pct=raw.get("compaction", {}).get("threshold_pct", 85),
                keep_recent=raw.get("compaction", {}).get("keep_recent", 20),
                tail_turns=raw.get("compaction", {}).get("tail_turns", 2),
                preserve_recent_tokens=raw.get("compaction", {}).get("preserve_recent_tokens", 4000),
                model=raw.get("compaction", {}).get("model", ""),
                tool_result_max_tokens=raw.get("compaction", {}).get("tool_result_max_tokens", 4000),
            ),
            permissions=PermissionConfig(
                trust_all=_validate_trust_all(raw.get("permissions", {})),
            ),
            musicgen=MusicGenConfig(
                host=raw.get("musicgen", {}).get("host", "localhost"),
                port=raw.get("musicgen", {}).get("port", 3000),
                timeout_seconds=raw.get("musicgen", {}).get("timeout_seconds", 900),
            ),
            browser=_browser_config(raw.get("browser", {})),
            cost=BudgetConfig(
                enabled=raw.get("cost", {}).get("enabled", False),
                max_tokens_per_session=raw.get("cost", {}).get("max_tokens_per_session", 0),
                max_tokens_per_message=raw.get("cost", {}).get("max_tokens_per_message", 0),
                max_cost_per_session=raw.get("cost", {}).get("max_cost_per_session", 0.0),
                warning_threshold_pct=raw.get("cost", {}).get("warning_threshold_pct", 80.0),
                hard_limit=raw.get("cost", {}).get("hard_limit", True),
            ),
        )
        config.refresh_workspace()
        config._apply_remote_vm_env_overrides()
        # Allow env overrides for Docker / containerized deployments
        if os.environ.get("CODEASSIST_HOST"):
            config.server.host = os.environ["CODEASSIST_HOST"]
        port_str = os.environ.get("CODEASSIST_PORT")
        if port_str:
            try:
                config.server.port = int(port_str)
            except ValueError:
                import logging
                logging.getLogger(__name__).warning(
                    "CODEASSIST_PORT='%s' is not a valid integer, keeping config value %d",
                    port_str, config.server.port,
                )
        config._source = Path(path).resolve()
        return config


def load_config(path: str | Path = "config.toml") -> Config:
    """Convenience function to load config from file."""
    return Config.load(path)
