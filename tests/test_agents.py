"""Tests for multi-agent system."""
import pytest

from codeassist.agents import AgentConfig, AgentManager, AgentPermissions, Permission


class TestPermission:
    """Test permission class."""

    def test_permission_creation(self):
        """Test creating a permission."""
        perm = Permission("read", "allow")
        assert perm.tool_name == "read"
        assert perm.action == "allow"
        assert perm.scope is None

    def test_permission_with_scope(self):
        """Test creating a permission with scope."""
        perm = Permission("write", "confirm", scope="*.py")
        assert perm.scope == "*.py"

    def test_permission_to_dict(self):
        """Test converting permission to dict."""
        perm = Permission("shell", "deny")
        d = perm.to_dict()
        
        assert d["tool_name"] == "shell"
        assert d["action"] == "deny"

    def test_permission_from_dict(self):
        """Test creating permission from dict."""
        d = {"tool_name": "git", "action": "confirm", "scope": None}
        perm = Permission.from_dict(d)
        
        assert perm.tool_name == "git"
        assert perm.action == "confirm"


class TestAgentPermissions:
    """Test agent permissions management."""

    def test_empty_permissions(self):
        """Test empty permissions."""
        perms = AgentPermissions()
        
        # Default should be confirm for unknown tools
        assert perms.check_permission("read") == "confirm"

    def test_allow_permission(self):
        """Test allow permission."""
        perms = AgentPermissions([Permission("read", "allow")])
        
        assert perms.check_permission("read") == "allow"
        assert perms.has_permission("read") is True

    def test_deny_permission(self):
        """Test deny permission."""
        perms = AgentPermissions([Permission("shell", "deny")])
        
        assert perms.check_permission("shell") == "deny"
        assert perms.has_permission("shell") is False

    def test_confirm_permission(self):
        """Test confirm permission."""
        perms = AgentPermissions([Permission("write", "confirm")])
        
        assert perms.check_permission("write") == "confirm"

    def test_get_allowed_tools(self):
        """Test getting allowed tools."""
        perms = AgentPermissions([
            Permission("read", "allow"),
            Permission("glob", "allow"),
            Permission("shell", "deny"),
        ])
        
        allowed = perms.get_allowed_tools()
        assert "read" in allowed
        assert "glob" in allowed
        assert "shell" not in allowed

    def test_get_denied_tools(self):
        """Test getting denied tools."""
        perms = AgentPermissions([
            Permission("shell", "deny"),
            Permission("write", "confirm"),
        ])
        
        denied = perms.get_denied_tools()
        assert "shell" in denied

    def test_to_dict(self):
        """Test converting permissions to dict."""
        perms = AgentPermissions([
            Permission("read", "allow"),
            Permission("shell", "deny"),
        ])
        
        d = perms.to_dict()
        assert "read" in d
        assert "shell" in d

    def test_from_dict(self):
        """Test creating permissions from dict."""
        d = {
            "read": {"tool_name": "read", "action": "allow"},
            "shell": {"tool_name": "shell", "action": "deny"},
        }
        
        perms = AgentPermissions.from_dict(d)
        assert perms.has_permission("read") is True
        assert perms.check_permission("shell") == "deny"


class TestAgentConfig:
    """Test agent configuration."""

    def test_basic_agent_config(self):
        """Test basic agent configuration."""
        config = AgentConfig(name="test-agent")
        
        assert config.name == "test-agent"
        assert config.description == ""
        assert config.instructions == ""

    def test_agent_with_description(self):
        """Test agent with description."""
        config = AgentConfig(
            name="plan-agent",
            description="Read-only analysis agent"
        )
        
        assert "Read-only" in config.description

    def test_agent_system_prompt(self):
        """Test generating system prompt."""
        config = AgentConfig(
            name="test-agent",
            description="A test agent",
            instructions="Be helpful.",
            permissions={"read": ["allow"], "shell": ["deny"]}
        )
        
        prompt = config.get_system_prompt()
        
        assert "test-agent" in prompt
        assert "A test agent" in prompt
        assert "Be helpful" in prompt
        assert "read" in prompt.lower()

    def test_agent_with_model(self):
        """Test agent with custom model."""
        config = AgentConfig(
            name="fast-agent",
            model="gpt-4o-mini"
        )
        
        assert config.model == "gpt-4o-mini"

    def test_agent_steps_defaults_to_none(self):
        """Without a step budget the agent falls back to the global cap."""
        config = AgentConfig(name="stepless-agent")
        assert config.steps is None

    def test_agent_steps_serialized_in_to_dict(self):
        """The per-agent step budget is exposed via to_dict so it can be edited
        and persisted through the API."""
        config = AgentConfig(name="bounded-agent", steps=12)
        assert config.steps == 12
        assert config.to_dict()["steps"] == 12


class TestAgentManager:
    """Test agent manager."""

    @pytest.mark.asyncio
    async def test_initialize_with_defaults(self):
        """Test initialization creates default agent."""
        manager = AgentManager()
        await manager.initialize()
        
        agents = manager.list_agents()
        assert len(agents) >= 1
        
        default = manager.get_default_agent()
        assert default is not None

    @pytest.mark.asyncio
    async def test_create_agent(self):
        """Test creating a new agent."""
        manager = AgentManager()
        await manager.initialize()
        
        agent = await manager.create_agent(
            name="custom-agent",
            description="Custom agent",
            permissions={"read": ["allow"]}
        )
        
        assert agent.name == "custom-agent"
        
        # Verify it's in the list
        agents = manager.list_agents()
        names = [a["name"] for a in agents]
        assert "custom-agent" in names

    @pytest.mark.asyncio
    async def test_builtin_agents_have_default_step_budgets(self):
        """Built-in agents seed with a per-agent step budget so each mode wraps
        up at a sensible point (read-only modes earlier, build later)."""
        manager = AgentManager()
        await manager.initialize()

        by_key = {key: cfg.steps for key, cfg in manager._agents.items()}
        # The six primary modes each seed with an integer step budget.
        assert set(by_key) >= {"default", "research", "review", "build", "general", "explore"}
        for key in {"default", "research", "review", "build", "general", "explore"}:
            assert isinstance(by_key[key], int), f"{key} missing a step budget"
        # Read-only modes get a smaller budget than the long-running build agent.
        assert by_key["explore"] < by_key["build"]
        assert by_key["research"] < by_key["build"]
        # The budget surfaces through list_agents() too.
        listed = {a["id"]: a["steps"] for a in manager.list_agents()}
        assert listed["build"] == by_key["build"]

    @pytest.mark.asyncio
    async def test_get_agent(self):
        """Test getting an agent by name."""
        manager = AgentManager()
        await manager.initialize()
        
        await manager.create_agent(
            name="test-get",
            description="Test get"
        )
        
        agent = manager.get_agent("test-get")
        assert agent is not None
        assert agent.name == "test-get"

    @pytest.mark.asyncio
    async def test_delete_agent(self):
        """Test deleting an agent."""
        manager = AgentManager()
        await manager.initialize()
        
        await manager.create_agent(name="to-delete")
        
        await manager.delete_agent("to-delete")
        
        agent = manager.get_agent("to-delete")
        assert agent is None

    @pytest.mark.asyncio
    async def test_list_agents(self):
        """Test listing all agents."""
        manager = AgentManager()
        await manager.initialize()
        
        await manager.create_agent(name="agent-1")
        await manager.create_agent(name="agent-2")
        
        agents = manager.list_agents()
        assert len(agents) >= 3  # default + 2 new
