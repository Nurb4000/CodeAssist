"""Tests for configuration system."""
import os
import tempfile
from pathlib import Path

import pytest

from config import Config


class TestConfig:
    """Test configuration loading and validation."""

    def test_load_default_config(self):
        """Test loading default config when no file exists."""
        config = Config.load("/nonexistent/config.toml")
        
        assert config.llm.provider == "openai"
        assert config.llm.model == "gpt-4o"
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 8000
        assert config.agent.max_iterations == 30
        assert config.tools.shell_timeout == 120

    def test_load_config_from_file(self, tmp_path):
        """Test loading config from a TOML file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[server]
host = "0.0.0.0"
port = 9000

[agent]
max_iterations = 50
name = "TestAgent"

[tools]
shell_timeout = 60
""")
        
        config = Config.load(config_file)
        
        assert config.llm.provider == "openai"
        assert config.llm.model == "gpt-4o-mini"
        assert config.llm.api_key == "test-key"
        assert config.server.host == "0.0.0.0"
        assert config.server.port == 9000
        assert config.agent.max_iterations == 50
        assert config.agent.name == "TestAgent"
        assert config.tools.shell_timeout == 60

    def test_env_variable_substitution(self, tmp_path, monkeypatch):
        """Test environment variable substitution for API keys."""
        monkeypatch.setenv("TEST_API_KEY", "my-secret-key")
        
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[llm]
provider = "openai"
model = "gpt-4o"
api_key = "env:TEST_API_KEY"
""")
        
        config = Config.load(config_file)
        assert config.llm.api_key == "my-secret-key"

    def test_llm_parameters(self, tmp_path):
        """Test LLM parameters loading."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[llm]
provider = "openai"
model = "gpt-4o"

[llm.parameters]
temperature = 0.7
max_tokens = 4096
context_window = 64000
""")
        
        config = Config.load(config_file)
        
        assert config.llm.temperature == 0.7
        assert config.llm.max_tokens == 4096
        assert config.llm.context_window == 64000

    def test_mcp_config(self, tmp_path):
        """Test MCP configuration loading."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[mcp]
enabled = true

[mcp.servers.my_server]
url = "http://localhost:3001/sse"
""")
        
        config = Config.load(config_file)
        
        assert config.mcp.enabled is True
        assert "my_server" in config.mcp.servers
        assert config.mcp.servers["my_server"]["url"] == "http://localhost:3001/sse"

    def test_skills_config(self, tmp_path):
        """Test skills configuration loading."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[skills]
enabled = true
directories = [".codeassist/skills", "custom/skills"]
""")
        
        config = Config.load(config_file)
        
        assert config.skills.enabled is True
        assert len(config.skills.directories) == 2

    def test_plugins_config(self, tmp_path):
        """Test plugins configuration loading."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[plugins]
enabled = false
""")
        
        config = Config.load(config_file)
        assert config.plugins.enabled is False

    def test_lsp_config(self, tmp_path):
        """Test LSP configuration loading."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[lsp]
enabled = true

[lsp.servers.typescript]
command = "typescript-language-server"
args = ["--stdio"]
languages = ["typescript", "javascript"]
""")
        
        config = Config.load(config_file)
        
        assert config.lsp.enabled is True
        assert "typescript" in config.lsp.servers

    def test_git_config(self, tmp_path):
        """Test Git configuration loading."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[git]
enabled = true
auto_detect = false
""")
        
        config = Config.load(config_file)
        
        assert config.git.enabled is True
        assert config.git.auto_detect is False

    def test_compaction_config(self, tmp_path):
        """Test compaction configuration loading."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[compaction]
enabled = true
threshold_pct = 80
keep_recent = 15
tool_result_max_tokens = 2000
""")
        
        config = Config.load(config_file)
        
        assert config.compaction.enabled is True
        assert config.compaction.threshold_pct == 80
        assert config.compaction.keep_recent == 15
        assert config.compaction.tool_result_max_tokens == 2000

    def test_workspace_resolution(self, tmp_path):
        """Test workspace path resolution."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(f"""
[server]
workspace = "{tmp_path}"
""")
        
        config = Config.load(config_file)
        assert config.workspace == tmp_path.resolve()

    def test_server_password(self, tmp_path):
        """Test server password configuration."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[server]
password = "secret123"
""")
        
        config = Config.load(config_file)
        assert config.server.password == "secret123"
