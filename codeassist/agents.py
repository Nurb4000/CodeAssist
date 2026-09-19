import json
import logging
from typing import Any

from .session import Agent as AgentRecord

log = logging.getLogger(__name__)

# Agents reseeded by initialize(); they're always present and cannot be deleted.
BUILTIN_AGENT_KEYS = {"default", "research", "review", "build", "general", "explore", "compaction"}


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

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description or "",
            "instructions": self.instructions or "",
            "model": self.model,
            "max_iterations": self.max_iterations,
        }

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

        # Add research agent (read-only, web search enabled)
        if "research" not in self._agents:
            self._agents["research"] = AgentConfig(
                name="Research",
                description="Read-only research agent. Gathers information without modifying code. "
                           "Use for answering questions, finding documentation, and troubleshooting.",
                instructions=(
                    "You are a research assistant. You can read files, search code, and browse the web, "
                    "but you MUST NOT modify any files. When you need to share findings, present them "
                    "clearly with file references and line numbers."
                ),
                permissions={
                    "read": ["allow"],
                    "write": ["deny"],
                    "edit": ["deny"],
                    "shell": ["deny"],
                    "glob": ["allow"],
                    "grep": ["allow"],
                    "webfetch": ["allow"],
                    "websearch": ["allow"],
                    "todo": ["allow"],
                    "symbol_search": ["allow"],
                    "diff_preview": ["deny"],
                },
            )

        # Add review agent (read-only, code review focused)
        if "review" not in self._agents:
            self._agents["review"] = AgentConfig(
                name="Review",
                description="Code review agent. Analyzes code for issues, suggests improvements, "
                           "and produces structured reviews. Read-only — never modifies files.",
                instructions=(
                    "You are a code review assistant. When reviewing code:\n"
                    "1. Read the target files and understand the changes\n"
                    "2. Check for bugs, security issues, and style violations\n"
                    "3. Suggest improvements with specific line references\n"
                    "4. Produce a structured review with severity levels\n"
                    "Use 'git diff' to see changes, 'read' to examine files, and 'grep' to check patterns. "
                    "NEVER modify files — your role is to analyze and report."
                ),
                permissions={
                    "read": ["allow"],
                    "write": ["deny"],
                    "edit": ["deny"],
                    "shell": ["confirm"],
                    "glob": ["allow"],
                    "grep": ["allow"],
                    "webfetch": ["allow"],
                    "todo": ["allow"],
                    "symbol_search": ["allow"],
                    "diff_preview": ["allow"],
                    "test_runner": ["allow"],
                },
            )

        # Add build agent (primary agent with full tool access)
        if "build" not in self._agents:
            self._agents["build"] = AgentConfig(
                name="Build",
                description="Primary build agent with full tool access. Executes plans, writes code, runs tests.",
                instructions=(
                    "You are the build agent. You have full access to all tools. "
                    "Your job is to execute tasks, write code, and implement features. "
                    "When you receive a plan, follow it carefully. Use todo tool to track progress."
                ),
                permissions={
                    "read": ["allow"],
                    "write": ["allow"],
                    "edit": ["allow"],
                    "shell": ["allow"],
                    "glob": ["allow"],
                    "grep": ["allow"],
                    "webfetch": ["allow"],
                    "todo": ["allow"],
                    "git": ["allow"],
                    "task": ["allow"],
                },
            )

        # Add general agent (multi-step task execution subagent)
        if "general" not in self._agents:
            self._agents["general"] = AgentConfig(
                name="General",
                description="General-purpose subagent for multi-step task execution. Has full tool access but cannot spawn subagents.",
                instructions=(
                    "You are a general-purpose subagent. Execute the given task thoroughly. "
                    "You have access to most tools but CANNOT use 'task' or 'todowrite' — "
                    "those are reserved for the parent agent. Report your findings clearly."
                ),
                permissions={
                    "read": ["allow"],
                    "write": ["allow"],
                    "edit": ["allow"],
                    "shell": ["allow"],
                    "glob": ["allow"],
                    "grep": ["allow"],
                    "webfetch": ["allow"],
                    "todo": ["allow"],
                    "git": ["allow"],
                    "task": ["deny"],
                },
            )

        # Add explore agent (fast codebase exploration, read-only)
        if "explore" not in self._agents:
            self._agents["explore"] = AgentConfig(
                name="Explore",
                description="Fast read-only subagent for codebase exploration. Use for parallel discovery tasks.",
                instructions=(
                    "You are an explore subagent. Your job is to quickly gather information about the codebase. "
                    "You can ONLY use read-only tools: read, glob, grep, directory, symbol_search, lsp. "
                    "DO NOT modify any files. Report your findings concisely with file paths and line numbers."
                ),
                permissions={
                    "read": ["allow"],
                    "write": ["deny"],
                    "edit": ["deny"],
                    "shell": ["deny"],
                    "glob": ["allow"],
                    "grep": ["allow"],
                    "webfetch": ["allow"],
                    "todo": ["deny"],
                    "git": ["deny"],
                    "task": ["deny"],
                    "symbol_search": ["allow"],
                    "directory": ["allow"],
                    "lsp": ["allow"],
                },
            )

        # Add compaction agent (hidden, for LLM-based context summarization)
        if "compaction" not in self._agents:
            self._agents["compaction"] = AgentConfig(
                name="Compaction",
                description="Hidden agent for summarizing conversation history during context window compaction.",
                instructions=(
                    "You are a compaction agent. Your ONLY job is to summarize conversation history. "
                    "You do NOT have access to tools. You receive a conversation transcript and must produce "
                    "a concise summary preserving key decisions, code changes, errors, and their resolutions."
                ),
                permissions={},
            )

    def get_agent(self, name: str) -> AgentConfig | None:
        """Get an agent by name."""
        return self._agents.get(name)

    def list_agents(self) -> list[dict]:
        """List all available agents.

        Built-in agents use a stable registry key ("default", "research", ...) that
        differs from their display name ("CodeAssist", "Research", ...). Clients
        select by ``id`` (the key) and display ``name``.
        """
        return [
            {
                "id": key,
                "name": config.name,
                "description": config.description,
                "instructions": config.instructions,
                "model": config.model,
                "max_iterations": config.max_iterations,
                "builtin": key in BUILTIN_AGENT_KEYS,
            }
            for key, config in self._agents.items()
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
        """Delete an agent. Built-in agents are reseeded at startup and cannot be removed."""
        if name in BUILTIN_AGENT_KEYS:
            raise ValueError(f"Cannot delete built-in agent '{name}'")
        if name in self._agents:
            del self._agents[name]

        # Remove from database
        try:
            agent_record = await AgentRecord.get_by_name(name)
            if agent_record:
                await agent_record.delete()
        except Exception as e:
            log.error("Failed to delete agent from database: %s", e)

    async def update_agent(self, name: str, **kwargs):
        """Update a custom agent's editable fields (description/model/instructions/max_iterations)."""
        if name not in self._agents:
            raise ValueError(f"Agent '{name}' not found")
        config = self._agents[name]
        allowed = {"description", "instructions", "model", "max_iterations"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        for key, value in updates.items():
            setattr(config, key, value)

        # Persist to database.
        try:
            record = await AgentRecord.get_by_name(name)
            if record:
                await record.update(**updates)
        except Exception as e:
            log.error("Failed to persist agent changes for '%s': %s", name, e)
        return config


# Singleton instance
agent_manager = AgentManager()
