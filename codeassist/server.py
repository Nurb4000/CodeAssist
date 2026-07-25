import asyncio
import base64
import hmac
import logging
import logging.config
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Config
from .session import init_db
from .mcp_client import MCPClient
from .skills import SkillRegistry
from .plugins import PluginRegistry
from .trust_registry import TrustRegistry
from tools import ToolRegistry, create_registry
from .agents import agent_manager

log = logging.getLogger(__name__)

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

_logging_configured = False


def _configure_logging():
    global _logging_configured
    if not _logging_configured:
        logging.config.dictConfig(LOGGING_CONFIG)
        _logging_configured = True


_configure_logging()

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


def get_trust_registry() -> TrustRegistry | None:
    """Get the current trust registry instance."""
    return trust_registry


async def broadcast_message(message: dict):
    """Broadcast a message to all connected WebSocket clients."""
    if not _active_websockets:
        return
    
    disconnected = set()
    for ws in _active_websockets:
        try:
            await ws.send_json(message)
        except Exception as e:
            log.warning("Failed to broadcast to websocket: %s", e)
            disconnected.add(ws)
    
    # Remove disconnected clients
    _active_websockets -= disconnected


def register_websocket(ws: WebSocket):
    """Register a WebSocket connection."""
    _active_websockets.add(ws)


def unregister_websocket(ws: WebSocket):
    """Unregister a WebSocket connection."""
    _active_websockets.discard(ws)


# Subsystems are initialized lazily in the lifespan using the resolved config
mcp_client: MCPClient | None = None
skill_registry: SkillRegistry | None = None
plugin_registry: PluginRegistry | None = None
tools: ToolRegistry | None = None
trust_registry: TrustRegistry | None = None

# Track active WebSocket connections for broadcasting
_active_websockets: set[WebSocket] = set()


def _init_subsystems(cfg: Config):
    """Initialize all subsystems with the given config."""
    global mcp_client, skill_registry, plugin_registry, tools, trust_registry

    # Initialize trust registry for secure tool/plugin loading
    trust_registry = TrustRegistry()
    
    mcp_client = MCPClient() if cfg.mcp.enabled else None
    skill_registry = SkillRegistry(cfg.workspace, cfg.skills) if cfg.skills.enabled else None
    plugin_registry = PluginRegistry(cfg.workspace, cfg.plugins, trust_registry=trust_registry) if cfg.plugins.enabled else None

    tools = create_registry(cfg.workspace, cfg.tools, mcp_client, skill_registry, plugin_registry, trust_registry=trust_registry)


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

    loader = DynamicToolLoader(cfg.workspace, trust_registry=trust_registry)
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
    if request.url.path in ("/health", "/favicon.ico") or \
       request.url.path.startswith("/static/") or \
       request.url.path.startswith("/ws/"):
        return await call_next(request)

    cfg = get_config()
    password = cfg.server.password
    if not password:
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
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


# ── WebSocket endpoint ──────────────────────────────────────────

MAX_SESSION_NAME_LEN = 200
MAX_MESSAGE_LEN = 100_000


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    from session import Session, Agent as AgentRecord
    from agent import Agent
    from agents import agent_manager
    from session_hook import get_session_hook
    import asyncio
    import hmac

    cfg = get_config()

    await websocket.accept()
    register_websocket(websocket)
    
    # Set up trust registry callback to broadcast approval requests
    if trust_registry:
        async def on_trust_request(request, approved):
            if not approved:
                await broadcast_message({
                    "type": "trust_approval_required",
                    **request.to_dict(),
                })
        
        trust_registry.set_approval_callback(on_trust_request)

    # Authenticate WebSocket connections via header only (never query params for security)
    password = cfg.server.password
    if password:
        authenticated = False
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

            elif data.get("type") == "approve_tool":
                file_path = data.get("file_path")
                if file_path and trust_registry:
                    from pathlib import Path as PPath
                    trust_registry.approve(PPath(file_path))
                    await websocket.send_json({
                        "type": "tool_approved",
                        "file_path": file_path,
                    })
                    # Reload tools to pick up the newly trusted tool
                    if tools is not None:
                        from dynamic_tools import DynamicToolLoader
                        loader = DynamicToolLoader(cfg.workspace, trust_registry=trust_registry)
                        loader.reload_registry(tools)

            elif data.get("type") == "reject_tool":
                file_path = data.get("file_path")
                if file_path and trust_registry:
                    from pathlib import Path as PPath
                    trust_registry.reject(PPath(file_path))
                    await websocket.send_json({
                        "type": "tool_rejected",
                        "file_path": file_path,
                    })

    except WebSocketDisconnect:
        log.info("Client disconnected from session %s", session_id)
        unregister_websocket(websocket)
        if agent_task and not agent_task.done():
            agent.cancel()
        try:
            hook = get_session_hook()
            await hook.on_session_end(session, agent)
        except Exception as e:
            log.warning("Failed to generate session summary: %s", e)
    except Exception as e:
        log.exception("WebSocket error")
        unregister_websocket(websocket)
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
        "codeassist.server:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=True,
    )


if __name__ == "__main__":
    main()
