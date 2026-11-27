"""Shared bootstrap for non-web CodeAssist entrypoints (run, ACP).

These helpers reuse the server module's subsystem globals so headless modes
exercise the same config, tool, MCP, LSP, skill, plugin, and permission paths
as the FastAPI app, without starting uvicorn or the browser UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import server
from .config import Config

log = logging.getLogger(__name__)


@dataclass
class HeadlessRuntime:
    config: Config

    async def close(self) -> None:
        """Release process-global resources started by bootstrap."""
        try:
            if server.lsp_client is not None:
                await server.lsp_client.shutdown()
        except Exception:  # pragma: no cover - defensive cleanup
            log.exception("Error shutting down LSP client")
        try:
            if server.mcp_client is not None:
                await server.mcp_client.close()
        except Exception:  # pragma: no cover - defensive cleanup
            log.exception("Error closing MCP client")
        try:
            from .tools.browser import shutdown_browser

            await shutdown_browser()
        except Exception:  # pragma: no cover - defensive cleanup
            log.exception("Error shutting down browser")


async def bootstrap(config: Config) -> HeadlessRuntime:
    """Initialize DB/config/subsystems like the server lifespan, without HTTP."""
    server.set_config(config)

    await server.init_db()
    from .settings import apply_settings_overrides

    await apply_settings_overrides(config)
    await server.init_permissions()
    await server._init_subsystems(config)
    await server.init_agents()
    await server.init_mcp()
    await server.init_skills()
    await server.init_plugins()

    log.info(
        "CodeAssist headless bootstrap complete | model=%s workspace=%s",
        config.llm.model,
        config.workspace,
    )
    return HeadlessRuntime(config=config)


def bind_session_tools(session_id: str) -> None:
    """Bind shared tool instances to the session, mirroring websocket setup."""
    tools = server.tools
    if tools is None:
        return
    session_tool = tools.get("session")
    if session_tool and hasattr(session_tool, "current_session_id"):
        session_tool.current_session_id = session_id
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "bind"):
        todo_tool.bind(session_id)
    task_tool = tools.get("task")
    cfg = server.get_config()
    if task_tool and hasattr(task_tool, "configure"):
        task_tool.configure(session_id, cfg, tools)


async def load_session_todos(session_id: str) -> None:
    tools = server.tools
    todo_tool = tools.get("todo") if tools else None
    if todo_tool and hasattr(todo_tool, "load_session"):
        await todo_tool.load_session(session_id)


def resolve_agent(agent_name: str | None = None):
    """Resolve agent name to (config_obj, resolved_name)."""
    cfg = server.get_config()
    name = agent_name or cfg.agent.default_agent or "default"
    config_obj = server.agent_manager.get_agent(name)
    if config_obj is None and name != "default":
        config_obj = server.agent_manager.get_agent("default")
        if config_obj is not None:
            name = "default"
    if config_obj is None:
        config_obj = server.agent_manager.get_default_agent()
        if config_obj is not None:
            # fall back to any registered agent
            for a in server.agent_manager.list_agents():
                if a.get("id"):
                    name = a["id"]
                    config_obj = server.agent_manager.get_agent(name)
                    break
    if config_obj is None:
        raise RuntimeError("No agents available")
    return config_obj, name


def build_agent(session, agent_name: str | None = None):
    """Build a fresh Agent for a session using the server's current globals."""
    from .agent import Agent
    from .prompts import build_system_prompt

    cfg = server.get_config()
    tools = server.tools
    if tools is None:
        raise RuntimeError("Tool registry not initialized")

    config_obj, name = resolve_agent(agent_name)

    features = {
        "mcp": bool(getattr(cfg, "mcp", None) and cfg.mcp.enabled and server.mcp_client is not None),
        "skills": bool(getattr(cfg, "skills", None) and cfg.skills.enabled and server.skill_registry is not None),
        "plugins": bool(getattr(cfg, "plugins", None) and cfg.plugins.enabled and server.plugin_registry is not None),
        "lsp": bool(getattr(cfg, "lsp", None) and cfg.lsp.enabled and server.lsp_client is not None),
        "codebase_index": bool(getattr(getattr(cfg, "codebase_index", None), "enabled", False)),
        "websearch": bool(getattr(getattr(cfg, "tools", None), "websearch_engine", "duckduckgo")),
        "vision": bool(getattr(getattr(cfg, "llm", None), "vision", False)),
    }
    instruction_discoverer = server.get_instruction_discoverer()
    instructions = instruction_discoverer.discover_all()
    instructions_text = "\n\n".join(instructions) if instructions else None

    prompt = build_system_prompt(
        workspace=cfg.workspace,
        model_id=cfg.llm.model,
        features=features,
        instructions=instructions_text,
    )
    agent_header = config_obj.get_system_prompt()
    if agent_header:
        prompt = agent_header + "\n\n" + prompt
    if server.skill_registry and cfg.skills.enabled:
        prompt += "\n\n" + server.skill_registry.get_instructions()

    return Agent(
        cfg,
        session,
        tools,
        prompt,
        agent_ruleset=config_obj.permissions.to_ruleset(),
        max_steps=config_obj.steps,
    ), config_obj, name
