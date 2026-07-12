import json
import logging
from typing import Any

from session import Agent as AgentRecord

log = logging.getLogger(__name__)


class Permission:
    """Represents a permission for an agent."""

    def __init__(self, tool_name: str, action: str, scope: str | None = None):
        self.tool_name = tool_name
        self.action = action  # "allow", "deny", "confirm"
        self.scope = scope  # Optional scope (e.g., file pattern)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "action": self.action,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Permission":
        return cls(
            tool_name=data["tool_name"],
            action=data.get("action", "allow"),
            scope=data.get("scope"),
        )


class AgentPermissions:
    """Manages permissions for an agent."""

    def __init__(self, permissions: list[Permission] | None = None):
        self._permissions: dict[str, Permission] = {}
        if permissions:
            for perm in permissions:
                self._permissions[perm.tool_name] = perm

    def check_permission(self, tool_name: str) -> str:
        """Check if a tool is allowed. Returns 'allow', 'deny', or 'confirm'."""
        perm = self._permissions.get(tool_name)
        if not perm:
            return "confirm"  # Default to confirmation if not explicitly set

        return perm.action

    def has_permission(self, tool_name: str) -> bool:
        """Check if a tool is explicitly allowed."""
        perm = self._permissions.get(tool_name)
        if not perm:
            return False
        return perm.action == "allow"

    def get_allowed_tools(self) -> list[str]:
        """Get list of explicitly allowed tools."""
        return [name for name, perm in self._permissions.items() if perm.action == "allow"]

    def get_denied_tools(self) -> list[str]:
        """Get list of explicitly denied tools."""
        return [name for name, perm in self._permissions.items() if perm.action == "deny"]

    def to_dict(self) -> dict:
        return {name: perm.to_dict() for name, perm in self._permissions.items()}

    @classmethod
    def from_dict(cls, data: dict) -> "AgentPermissions":
        permissions = [Permission.from_dict(v) for v in data.values()]
        return cls(permissions)


class AgentConfig:
    """Configuration for an agent."""

    def __init__(
        self,
        name: str,
        description: str | None = None,
        instructions: str | None = None,
        model: str | None = None,
        max_iterations: int | None = None,
        permissions: dict[str, list[str]] | None = None,
    ):
        self.name = name
        self.description = description or ""
        self.instructions = instructions or ""
        self.model = model
        self.max_iterations = max_iterations
        self.permissions = self._parse_permissions(permissions or {})

    def _parse_permissions(self, perm_config: dict) -> AgentPermissions:
        """Parse permission configuration."""
        permissions = []
        for tool_name, actions in perm_config.items():
            if isinstance(actions, list):
                for action in actions:
                    permissions.append(Permission(tool_name, action))
            elif isinstance(actions, str):
                permissions.append(Permission(tool_name, actions))

        return AgentPermissions(permissions)

    def get_system_prompt(self) -> str:
        """Get the system prompt for this agent."""
        prompt = f"You are {self.name}.\n\n"

        if self.description:
            prompt += f"{self.description}\n\n"

        if self.instructions:
            prompt += f"Instructions:\n{self.instructions}\n\n"

        # Add permission information
        allowed = self.permissions.get_allowed_tools()
        denied = self.permissions.get_denied_tools()

        if allowed:
            prompt += f"You are explicitly allowed to use these tools: {', '.join(allowed)}\n"

        if denied:
            prompt += f"You are NOT allowed to use these tools: {', '.join(denied)}\n"

        return prompt


class AgentManager:
    """Manages multiple agents."""

    def __init__(self):
        self._agents: dict[str, AgentConfig] = {}
        self._default_agent_name = "default"

    async def initialize(self):
        """Initialize agents from database and defaults."""
        # Load agents from database
        try:
            db_agents = await AgentRecord.list_all()
            for db_agent in db_agents:
                config = AgentConfig(
                    name=db_agent["name"],
                    description=db_agent.get("description"),
                    instructions=db_agent.get("instructions"),
                    model=db_agent.get("model"),
                    max_iterations=db_agent.get("max_iterations"),
                    permissions=json.loads(db_agent.get("permissions", "{}")) if db_agent.get("permissions") else {},
                )
                self._agents[db_agent["name"]] = config
        except Exception as e:
            log.error("Failed to load agents from database: %s", e)

        # Add default agent if not exists
        if "default" not in self._agents:
            self._agents["default"] = AgentConfig(
                name="CodeAssist",
                description="Default development agent with full tool access.",
                permissions={
                    "read": ["allow"],
                    "write": ["confirm"],
                    "edit": ["confirm"],
                    "shell": ["confirm"],
                    "glob": ["allow"],
                    "grep": ["allow"],
                    "webfetch": ["allow"],
                    "todo": ["allow"],
                    "git": ["confirm"],
                },
            )

    def get_agent(self, name: str) -> AgentConfig | None:
        """Get an agent by name."""
        return self._agents.get(name)

    def list_agents(self) -> list[dict]:
        """List all available agents."""
        return [
            {
                "name": config.name,
                "description": config.description,
                "model": config.model,
            }
            for config in self._agents.values()
        ]

    def get_default_agent(self) -> AgentConfig:
        """Get the default agent."""
        return self._agents.get(self._default_agent_name, list(self._agents.values())[0])

    async def create_agent(self, name: str, **kwargs) -> AgentConfig:
        """Create a new agent."""
        config = AgentConfig(name=name, **kwargs)
        self._agents[name] = config

        # Save to database
        try:
            await AgentRecord.create(
                name=name,
                description=kwargs.get("description"),
                instructions=kwargs.get("instructions"),
                model=kwargs.get("model"),
                max_iterations=kwargs.get("max_iterations"),
                permissions=kwargs.get("permissions", {}),
            )
        except Exception as e:
            log.error("Failed to save agent to database: %s", e)

        return config

    async def delete_agent(self, name: str):
        """Delete an agent."""
        if name in self._agents:
            del self._agents[name]

        # Remove from database
        try:
            agent_record = await AgentRecord.get_by_name(name)
            if agent_record:
                await agent_record.delete()
        except Exception as e:
            log.error("Failed to delete agent from database: %s", e)


# Singleton instance
agent_manager = AgentManager()
