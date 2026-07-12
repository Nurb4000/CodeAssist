import asyncio
import hmac
import logging
import logging.config
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import Config
from session import Session, Agent as AgentRecord, MCPServer, Skill, Plugin, LSPServer, GitRepo, init_db
from agent import Agent
from tools import ToolRegistry, create_registry
from mcp_client import MCPClient
from skills import SkillRegistry
from plugins import PluginRegistry
from agents import agent_manager

log = logging.getLogger(__name__)

MAX_SESSION_NAME_LEN = 200
MAX_MESSAGE_LEN = 100_000

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "agent": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "llm": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "tools": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "mcp": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "skills": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "plugins": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
    },
}

_config: Config | None = None

def get_config() -> Config:
    """Get the current configuration, loading from default if not set."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def set_config(config: Config):
    """Set the configuration (called by CLI before server startup)."""
    global _config
    _config = config


logging.config.dictConfig(LOGGING_CONFIG)

# Subsystems are initialized lazily in the lifespan using the resolved config
mcp_client: MCPClient | None = None
skill_registry: SkillRegistry | None = None
plugin_registry: PluginRegistry | None = None
tools: ToolRegistry | None = None


def _init_subsystems(cfg: Config):
    """Initialize all subsystems with the given config."""
    global mcp_client, skill_registry, plugin_registry, tools

    mcp_client = MCPClient() if cfg.mcp.enabled else None
    skill_registry = SkillRegistry(cfg.workspace, cfg.skills) if cfg.skills.enabled else None
    plugin_registry = PluginRegistry(cfg.workspace, cfg.plugins) if cfg.plugins.enabled else None

    tools = create_registry(cfg.workspace, cfg.tools, mcp_client, skill_registry, plugin_registry)


# Initialize agent manager
async def init_agents():
    await agent_manager.initialize()

async def init_mcp():
    if mcp_client and _config and _config.mcp.servers:
        await mcp_client.initialize(_config.mcp.servers)

async def init_skills():
    if skill_registry:
        skill_registry.discover()

async def init_plugins():
    if plugin_registry:
        plugin_registry.discover()


def reload_all_tools():
    """Reload all tools from the tools directory."""
    global tools
    cfg = get_config()
    from dynamic_tools import DynamicToolLoader
    
    if tools is None:
        _init_subsystems(cfg)
        return

    loader = DynamicToolLoader(cfg.workspace)
    loader.reload_registry(tools)
    log.info("Tools reloaded successfully")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    _init_subsystems(cfg)
    await init_db()
    await init_agents()
    await init_mcp()
    await init_skills()
    await init_plugins()
    log.info("CodeAssist starting | model=%s workspace=%s", cfg.llm.model, cfg.workspace)
    yield
    if mcp_client:
        await mcp_client.close()


app = FastAPI(title="CodeAssist", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.middleware("http")
async def auth_middleware(request, call_next):
    # Skip auth for health check, static files, favicon, and WebSocket
    # (WebSocket has its own auth in the endpoint handler)
    if request.url.path in ("/health", "/favicon.ico") or \
       request.url.path.startswith("/static/") or \
       request.url.path.startswith("/ws/"):
        return await call_next(request)

    cfg = get_config()
    password = cfg.server.password
    if not password:
        return await call_next(request)

    # Check for Basic Auth header
    from fastapi.requests import Request
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        import base64
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            username, _, pw = decoded.partition(":")
            if hmac.compare_digest(pw, password):
                return await call_next(request)
        except Exception:
            pass

    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required"},
        headers={"WWW-Authenticate": 'Basic realm="CodeAssist"'},
    )


@app.get("/health")
async def health_check():
    cfg = get_config()
    return {
        "status": "ok",
        "model": cfg.llm.model,
        "workspace": str(cfg.workspace),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


# Session endpoints
@app.get("/api/sessions")
async def list_sessions():
    return await Session.list_all()


@app.post("/api/sessions")
async def create_session():
    session = await Session.create()
    return {"id": session.id}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    session = Session(session_id)
    await session.delete()
    return {"ok": True}


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, body: dict):
    name = body.get("name", "Untitled")
    if not isinstance(name, str) or len(name) > MAX_SESSION_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"Session name must be <= {MAX_SESSION_NAME_LEN} characters")
    session = Session(session_id)
    await session.rename(name)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    session = Session(session_id)
    return await session.get_messages()


@app.post("/api/sessions/{session_id}/fork")
async def fork_session(session_id: str, body: dict):
    name = body.get("name")
    new_session = await Session.fork_session(session_id, name)
    return {"id": new_session.id}


@app.get("/api/sessions/{session_id}/summary")
async def session_summary(session_id: str):
    from session_manager import SessionManager
    summary = await SessionManager.get_session_summary(session_id)
    return summary


@app.post("/api/sessions/export")
async def export_session(body: dict):
    from session_manager import SessionManager
    session_id = body.get("session_id")
    redact = body.get("redact", False)
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    data = await SessionManager.export_session(session_id, redact)
    return data


@app.post("/api/sessions/import")
async def import_session(body: dict):
    from session_manager import SessionManager
    data = body.get("data")
    name = body.get("name")
    if not data:
        raise HTTPException(status_code=400, detail="data is required")
    import json
    try:
        export_data = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON data")
    new_session = await SessionManager.import_session(export_data, name)
    return {"id": new_session.id}


# Config endpoints
@app.get("/api/config")
async def api_config():
    cfg = get_config()
    return {
        "model": cfg.llm.model,
        "provider": cfg.llm.provider,
        "workspace": str(cfg.workspace),
        "agent_name": cfg.agent.name,
        "features": {
            "mcp_enabled": cfg.mcp.enabled,
            "skills_enabled": cfg.skills.enabled,
            "plugins_enabled": cfg.plugins.enabled,
            "lsp_enabled": cfg.lsp.enabled,
            "git_enabled": cfg.git.enabled,
        },
    }


# Todo endpoints
@app.get("/api/todos")
async def get_todos():
    if tools is None:
        return {"tasks": []}
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "get_tasks"):
        return {"tasks": todo_tool.get_tasks()}
    return {"tasks": []}


@app.post("/api/todos/clear")
async def clear_todos():
    if tools is None:
        return {"ok": True}
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "clear_tasks"):
        todo_tool.clear_tasks()
    return {"ok": True}


# Agent endpoints
@app.get("/api/agents")
async def list_agents():
    return agent_manager.list_agents()


@app.post("/api/agents")
async def create_agent(body: dict):
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    agent = await agent_manager.create_agent(
        name=name,
        description=body.get("description"),
        instructions=body.get("instructions"),
        model=body.get("model"),
        max_iterations=body.get("max_iterations"),
        permissions=body.get("permissions", {}),
    )
    return agent.to_dict() if hasattr(agent, 'to_dict') else {"name": name}


@app.delete("/api/agents/{agent_name}")
async def delete_agent(agent_name: str):
    await agent_manager.delete_agent(agent_name)
    return {"ok": True}


# MCP endpoints
@app.get("/api/mcp/servers")
async def list_mcp_servers():
    cfg = get_config()
    if not cfg.mcp.enabled:
        return {"servers": []}
    return await MCPServer.list_all()


@app.post("/api/mcp/servers")
async def create_mcp_server(body: dict):
    cfg = get_config()
    if not cfg.mcp.enabled:
        raise HTTPException(status_code=400, detail="MCP is not enabled")
    name = body.get("name")
    server_config = body.get("config", {})
    server = await MCPServer.create(name, server_config)
    return {"id": server.id}


@app.delete("/api/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str):
    server = MCPServer(server_id)
    await server.delete()
    return {"ok": True}


# Skill endpoints
@app.get("/api/skills")
async def list_skills():
    cfg = get_config()
    if not cfg.skills.enabled or not skill_registry:
        return {"skills": []}
    return {"skills": skill_registry.list_skills()}


@app.post("/api/skills")
async def create_skill(body: dict):
    cfg = get_config()
    if not cfg.skills.enabled or not skill_registry:
        raise HTTPException(status_code=400, detail="Skills are not enabled")
    skill = await Skill.create(
        name=body.get("name"),
        description=body.get("description", ""),
        content=body.get("content", ""),
        slash_command=body.get("slash_command"),
    )
    return {"id": skill.id}


@app.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: str):
    skill = Skill(skill_id)
    await skill.delete()
    return {"ok": True}


# Plugin endpoints
@app.get("/api/plugins")
async def list_plugins():
    cfg = get_config()
    if not cfg.plugins.enabled or not plugin_registry:
        return {"plugins": []}
    return {"plugins": plugin_registry.list_plugins()}


# LSP endpoints
@app.get("/api/lsp/servers")
async def list_lsp_servers():
    return await LSPServer.list_all()


@app.post("/api/lsp/servers")
async def create_lsp_server(body: dict):
    server = await LSPServer.create(
        name=body.get("name"),
        command=body.get("command"),
        args=body.get("args", []),
        languages=body.get("languages", []),
    )
    return {"id": server.id}


@app.delete("/api/lsp/servers/{server_id}")
async def delete_lsp_server(server_id: str):
    server = LSPServer(server_id)
    await server.delete()
    return {"ok": True}


# Git endpoints
@app.get("/api/git/repos")
async def list_git_repos():
    cfg = get_config()
    if not cfg.git.enabled:
        return {"repos": []}
    return await GitRepo.list_all()


# Tool management endpoints
@app.post("/api/tools/reload")
async def reload_tools_endpoint():
    """Reload all tools from the tools directory."""
    try:
        reload_all_tools()
        return {"ok": True, "message": "Tools reloaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reload tools: {e}")


@app.get("/api/tools/list")
async def list_tools():
    """List all available tools."""
    if tools is None:
        return {"tools": []}
    return {"tools": [name for name in tools.list_names()]}


# WebSocket endpoint
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    cfg = get_config()

    await websocket.accept()

    # Authenticate WebSocket connections via query param or header
    password = cfg.server.password
    if password:
        authenticated = False
        # Check query parameter ?password=...
        query_params = parse_qs(websocket.url.query or "")
        qp_password = query_params.get("password", [None])[0]
        if qp_password and hmac.compare_digest(qp_password, password):
            authenticated = True
        # Check Sec-WebSocket-Protocol header (browser custom header workaround)
        if not authenticated:
            ws_protocol = websocket.headers.get("sec-websocket-protocol", "")
            if hmac.compare_digest(ws_protocol, password):
                authenticated = True
        if not authenticated:
            await websocket.close(code=4001, reason="Authentication required")
            return

    session = Session(session_id)

    # Get current agent
    current_agent_name = cfg.agent.default_agent
    agent_config_obj = agent_manager.get_agent(current_agent_name)
    system_prompt = agent_config_obj.get_system_prompt() if agent_config_obj else "You are a helpful coding assistant."

    # Use the global tools registry (supports dynamic reloading)
    if tools is None:
        await websocket.close(code=1011, reason="Server not initialized")
        return

    # Update SessionTool with the current session ID
    session_tool = tools.get("session")
    if session_tool and hasattr(session_tool, "current_session_id"):
        session_tool.current_session_id = session_id

    agent = Agent(cfg, session, tools, system_prompt)
    agent.reset_trust()
    agent_task: asyncio.Task | None = None

    async def run_agent_task(message: str):
        nonlocal agent_task
        try:
            async for event in agent.run(message):
                await websocket.send_json({
                    "type": event.type,
                    **event.data,
                })
        except Exception as e:
            log.exception("Agent error")
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass
        finally:
            agent_task = None

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "user_message":
                content = data.get("content", "")
                if not content.strip():
                    continue
                if len(content) > MAX_MESSAGE_LEN:
                    await websocket.send_json({"type": "error", "message": f"Message too long. Maximum is {MAX_MESSAGE_LEN} characters."})
                    continue
                if agent_task and not agent_task.done():
                    await websocket.send_json({"type": "error", "message": "Agent is busy, please wait"})
                    continue

                agent_task = asyncio.create_task(run_agent_task(content))

            elif data.get("type") == "cancel":
                if agent_task and not agent_task.done():
                    agent.cancel()

            elif data.get("type") == "confirm_response":
                confirm_id = data.get("id")
                approved = data.get("approved", False)
                trust_workspace = data.get("trust_workspace", False)
                trust_shell = data.get("trust_shell", False)
                if confirm_id:
                    agent.resolve_confirm(confirm_id, approved, trust_workspace, trust_shell)

            elif data.get("type") == "switch_agent":
                agent_name = data.get("agent_name")
                if agent_name:
                    new_config = agent_manager.get_agent(agent_name)
                    if new_config:
                        agent.system_prompt = new_config.get_system_prompt()
                        await websocket.send_json({
                            "type": "agent_switched",
                            "agent": agent_name,
                        })

    except WebSocketDisconnect:
        log.info("Client disconnected from session %s", session_id)
        if agent_task and not agent_task.done():
            agent.cancel()
    except Exception as e:
        log.exception("WebSocket error")
        if agent_task and not agent_task.done():
            agent.cancel()
        try:
            await websocket.close()
        except Exception:
            pass


def main():
    import uvicorn
    cfg = get_config()
    uvicorn.run(
        "server:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=True,
    )


if __name__ == "__main__":
    main()
