import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.0
    max_tokens: int = 8192
    context_window: int = 128000
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    workspace: str = "."
    password: str = ""


@dataclass
class AgentConfig:
    max_iterations: int = 30
    name: str = "CodeAssist"
    default_agent: str = "default"


@dataclass
class ToolConfig:
    shell_timeout: int = 120
    max_output_chars: int = 20000
    webfetch_max_chars: int = 30000
    websearch_max_chars: int = 30000
    tool_output_max_tokens: int = 4000


@dataclass
class MCPConfig:
    enabled: bool = False
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class SkillsConfig:
    enabled: bool = True
    directories: list[str] = field(default_factory=lambda: [".codeassist/skills"])


@dataclass
class PluginConfig:
    enabled: bool = False
    directories: list[str] = field(default_factory=lambda: [".codeassist/plugins"])


@dataclass
class LSPConfig:
    enabled: bool = False
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class GitConfig:
    enabled: bool = True
    auto_detect: bool = True


@dataclass
class CompactionConfig:
    enabled: bool = True
    threshold_pct: int = 75
    keep_recent: int = 20
    tool_result_max_tokens: int = 4000


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
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    workspace: Path = field(default_factory=lambda: Path.cwd())

    @classmethod
    def load(cls, path: str | Path = "config.toml") -> "Config":
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path, "rb") as f:
            raw = tomllib.load(f)

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
            ),
            server=ServerConfig(
                host=raw.get("server", {}).get("host", "127.0.0.1"),
                port=raw.get("server", {}).get("port", 8000),
                workspace=raw.get("server", {}).get("workspace", "."),
                password=raw.get("server", {}).get("password", ""),
            ),
            agent=AgentConfig(
                max_iterations=raw.get("agent", {}).get("max_iterations", 30),
                name=raw.get("agent", {}).get("name", "CodeAssist"),
                default_agent=raw.get("agent", {}).get("default_agent", "default"),
            ),
            tools=ToolConfig(
                shell_timeout=raw.get("tools", {}).get("shell_timeout", 120),
                max_output_chars=raw.get("tools", {}).get("max_output_chars", 20000),
                webfetch_max_chars=raw.get("tools", {}).get("webfetch_max_chars", 30000),
                websearch_max_chars=raw.get("tools", {}).get("websearch_max_chars", 30000),
                tool_output_max_tokens=raw.get("tools", {}).get("tool_output_max_tokens", 4000),
            ),
            mcp=MCPConfig(
                enabled=raw.get("mcp", {}).get("enabled", False),
                servers=raw.get("mcp", {}).get("servers", {}),
            ),
            skills=SkillsConfig(
                enabled=raw.get("skills", {}).get("enabled", True),
                directories=raw.get("skills", {}).get("directories", [".codeassist/skills"]),
            ),
            plugins=PluginConfig(
                enabled=raw.get("plugins", {}).get("enabled", False),
                directories=raw.get("plugins", {}).get("directories", [".codeassist/plugins"]),
            ),
            lsp=LSPConfig(
                enabled=raw.get("lsp", {}).get("enabled", False),
                servers=raw.get("lsp", {}).get("servers", {}),
            ),
            git=GitConfig(
                enabled=raw.get("git", {}).get("enabled", True),
                auto_detect=raw.get("git", {}).get("auto_detect", True),
            ),
            compaction=CompactionConfig(
                enabled=raw.get("compaction", {}).get("enabled", True),
                threshold_pct=raw.get("compaction", {}).get("threshold_pct", 75),
                keep_recent=raw.get("compaction", {}).get("keep_recent", 20),
                tool_result_max_tokens=raw.get("compaction", {}).get("tool_result_max_tokens", 4000),
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
        return config
