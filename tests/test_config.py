"""Tests for configuration system."""

import pytest

from codeassist.config import Config


class TestConfig:
    """Test configuration loading and validation."""

    def test_load_default_config(self):
        """Test loading default config when no file exists."""
        config = Config.load("/nonexistent/config.toml")
        
        assert config.llm.provider == "openai"
        assert config.llm.model == ""  # no baked-in model default; UI-configured
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 8090
        assert config.agent.max_iterations == 200
        assert config.agent.auto_create_skills is False
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

    def test_agent_auto_creation_fields_loaded_from_toml(self, tmp_path):
        """The four auto-creation fields (auto_create_skills, auto_create_tools,
        max_auto_creations, min_confidence) must be read from [agent] in the
        TOML. They were silently dropped by AgentConfig(), so session_hook's
        auto-creation guards always saw the defaults regardless of config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[agent]
auto_create_skills = true
auto_create_tools = true
max_auto_creations = 7
min_confidence = 0.9
""")
        config = Config.load(config_file)
        assert config.agent.auto_create_skills is True
        assert config.agent.auto_create_tools is True
        assert config.agent.max_auto_creations == 7
        assert config.agent.min_confidence == 0.9

    def test_agent_auto_creation_fields_default_when_omitted(self, tmp_path):
        """When [agent] omits the auto-creation fields, the dataclass defaults
        still apply (nothing regresses to a non-default)."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[agent]
max_iterations = 10
""")
        config = Config.load(config_file)
        assert config.agent.auto_create_skills is False
        assert config.agent.auto_create_tools is False
        assert config.agent.max_auto_creations == 3
        assert config.agent.min_confidence == 0.7

    def test_tool_output_max_tokens_default_matches_dataclass(self, tmp_path):
        """Config.load() and the ToolConfig dataclass must agree on defaults.

        They drifted: the dataclass defaulted tool_output_max_tokens to 2000
        (the value test_settings.py relies on) while Config.load defaulted it to
        4000, so whichever path built the object won. Asserting both sides guard
        against the two defaults diverging again (§7 FRAGILE / §13 #14)."""
        from codeassist.config import ToolConfig

        config_file = tmp_path / "config.toml"
        config_file.write_text("[server]\nworkspace = '.'\n")
        loaded = Config.load(config_file).tools.tool_output_max_tokens
        assert loaded == ToolConfig().tool_output_max_tokens == 2000

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

    def test_llm_timeout_default_and_override(self, tmp_path):
        """LLM stream timeout defaults to 900s and is overridable from [llm]."""
        assert Config.load("/nonexistent/config.toml").llm.timeout == 900

        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[llm]
provider = "openai"
timeout = 720
""")

        config = Config.load(config_file)
        assert config.llm.timeout == 720

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
directories = ["codeassist/skills", "custom/skills"]
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

    def test_permissions_config(self, tmp_path):
        """Test permissions (trust-all) configuration loading."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[permissions]
trust_all = "always"
""")
        
        config = Config.load(config_file)
        assert config.permissions.trust_all == "always"

    def test_permissions_trust_all_invalid_raises(self, tmp_path):
        """A malformed permissions.trust_all in TOML must fail loudly at load,
        not silently fall back to 'ask' (§13 #14). trust_all auto-approves every
        tool call, so a typo must never be swallowed into an unintended mode."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[permissions]
trust_all = "sometimes"
""")
        with pytest.raises(ValueError, match="Invalid permissions.trust_all"):
            Config.load(config_file)

    def test_permissions_trust_all_session_accepted(self, tmp_path):
        """'session' is a valid (ephemeral) mode and must load without raising."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[permissions]
trust_all = "session"
""")
        config = Config.load(config_file)
        assert config.permissions.trust_all == "session"


class TestPermissionConfigDefaults:
    def test_default_ask(self):
        """Default permissions mode prompts for confirmation (safest default)."""
        config = Config()
        assert config.permissions.trust_all == "ask"

    def test_model_omitted_stays_blank(self, tmp_path):
        """A [llm] block without `model` must not fall back to a hardcoded
        default. Blank means "not configured yet" and the UI prompts for it —
        silently substituting a model would hide a misconfiguration."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[llm]
provider = "openai"
api_key = "test-key"
""")

        assert Config.load(config_file).llm.model == ""

    def test_embedding_model_from_llm_section(self, tmp_path):
        """README.md documents embedding_model under [llm]."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[llm]
provider = "openai"
embedding_model = "text-embedding-3-small"
""")

        assert Config.load(config_file).llm.embedding_model == "text-embedding-3-small"

    def test_embedding_model_from_parameters_section(self, tmp_path):
        """config.example.toml documents it under [llm.parameters] instead —
        both documented placements must actually work."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[llm]
provider = "openai"

[llm.parameters]
embedding_model = "text-embedding-3-large"
""")

        assert Config.load(config_file).llm.embedding_model == "text-embedding-3-large"

    def test_embedding_model_defaults_blank(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[llm]
provider = "openai"
model = "some-model"
""")

        assert Config.load(config_file).llm.embedding_model == ""
