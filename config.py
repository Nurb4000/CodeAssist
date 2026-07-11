import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.0
    max_tokens: int = 8192
    context_window: int = 128000


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    workspace: str = "."


@dataclass
class AgentConfig:
    max_iterations: int = 30
    name: str = "CodeAssist"


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
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
            ),
            server=ServerConfig(
                host=raw.get("server", {}).get("host", "0.0.0.0"),
                port=raw.get("server", {}).get("port", 8000),
                workspace=raw.get("server", {}).get("workspace", "."),
            ),
            agent=AgentConfig(
                max_iterations=raw.get("agent", {}).get("max_iterations", 30),
                name=raw.get("agent", {}).get("name", "CodeAssist"),
            ),
        )
        config.workspace = Path(config.server.workspace).resolve()
        return config
