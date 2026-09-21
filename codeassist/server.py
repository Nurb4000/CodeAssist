import asyncio
import base64
import hmac
import json
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
from .instruction_discovery import get_instruction_discoverer
from .tools import ToolRegistry, create_registry
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
lsp_client: "LSPClient | None" = None

# Track active WebSocket connections for broadcasting
_active_websockets: set[WebSocket] = set()


async def _merged_lsp_specs(cfg: Config) -> dict:
    """LSP server specs (name -> {command, args, languages}) from config.toml
    plus admin-managed DB servers (enabled=1). config.toml wins on a name
    collision; a malformed DB row is skipped with a warning rather than
    failing startup."""
    specs: dict = {}
    toml_servers = getattr(cfg.lsp, "servers", None) or {}
    for name, scfg in toml_servers.items():
        specs[name] = {
            "command": scfg.get("command", ""),
            "args": scfg.get("args", []),
            "languages": scfg.get("languages", []),
        }

    from codeassist.session import LSPServer

    try:
        db_rows = await LSPServer.list_all()
    except Exception as e:  # pragma: no cover - DB unavailable at boot
        log.warning("Could not read LSP servers from DB: %s", e)
        db_rows = []

    for row in db_rows:
        name = row["name"]
        if name in specs:
            continue
        try:
            args = json.loads(row["args"])
            languages = json.loads(row["languages"])
        except Exception as e:
            log.warning("Skipping LSP server '%s': invalid args/languages JSON (%s)", name, e)
            continue
        specs[name] = {"command": row["command"], "args": args, "languages": languages}
    return specs


async def _start_lsp_servers(cfg: Config, lsp_client) -> None:
    """Start config.toml LSP servers plus admin-managed DB servers (enabled=1).

    Each start is guarded so a single bad DB row logs an error instead of
    aborting boot. No-op when LSP is disabled.
    """
    if not cfg.lsp.enabled:
        return
    for name, spec in (await _merged_lsp_specs(cfg)).items():
        try:
            await lsp_client.start_server(
                name=name,
                command=spec["command"],
                args=spec["args"],
                languages=spec["languages"],
                workspace=cfg.workspace,
            )
        except Exception as e:
            log.error("Failed to start LSP server '%s': %s", name, e)


async def reload_mcp_servers() -> list[str]:
    """Reconnect MCP servers after an admin edit, using the merged DB+config set.

    No-op when MCP is disabled (no live client). Returns names (re)connected.
    """
    if mcp_client is None:
        return []
    cfg = get_config()
    servers = await _merged_mcp_servers(cfg)
    return await mcp_client.reload(servers)


async def reload_lsp_servers() -> None:
    """Restart LSP servers after an admin edit, using the merged DB+config set."""
    cfg = get_config()
    if lsp_client is None or not cfg.lsp.enabled:
        return
    specs = await _merged_lsp_specs(cfg)
    await lsp_client.reload(specs, cfg.workspace)


# Background reconcile tasks, retained until they finish so the event loop
# doesn't garbage-collect them mid-flight (which would raise "coroutine never
# awaited"). Tasks remove themselves via the done callback.
_reload_tasks: "set[asyncio.Task]" = set()


def spawn_reload(coro):
    """Schedule a reconcile coroutine to run off the request path.

    The admin create/update/delete routes use this so editing many servers at
    once doesn't hold the HTTP response open while connections reconnect. Any
    error is logged inside the wrapper; callers get no return value.
    """

    async def _run():
        try:
            await coro
        except Exception:  # pragma: no cover - reload_* helpers log internally
            log.exception("Background MCP/LSP reload failed")

    task = asyncio.create_task(_run())
    _reload_tasks.add(task)
    task.add_done_callback(_reload_tasks.discard)
    return task


async def _init_subsystems(cfg: Config):
    """Initialize all subsystems with the given config."""
    global mcp_client, skill_registry, plugin_registry, tools, trust_registry, lsp_client

    # Initialize trust registry for secure tool/plugin loading
    trust_registry = TrustRegistry()
    
    mcp_client = MCPClient() if cfg.mcp.enabled else None
    skill_registry = SkillRegistry(cfg.workspace, cfg.skills) if cfg.skills.enabled else None
    plugin_registry = PluginRegistry(cfg.workspace, cfg.plugins, trust_registry=trust_registry) if cfg.plugins.enabled else None

    # Initialize LSP client
    from .lsp_client import LSPClient
    lsp_client = LSPClient()
    await _start_lsp_servers(cfg, lsp_client)

    tools = create_registry(cfg.workspace, cfg.tools, mcp_client, skill_registry, plugin_registry, lsp_client)


async def init_agents():
    await agent_manager.initialize()

async def _merged_mcp_servers(cfg: Config) -> dict:
    """MCP server registry passed to the client at boot.

    Combines ``[mcp].servers`` from config.toml with admin-managed servers
    stored in the DB (enabled=1). config.toml takes precedence on a name
    collision; a malformed DB config JSON is skipped with a warning rather
    than failing startup.
    """
    merged: dict = {}
    toml_servers = getattr(cfg.mcp, "servers", None) or {}
    for name, srv in toml_servers.items():
        merged[name] = srv

    from codeassist.session import MCPServer

    try:
        db_rows = await MCPServer.list_all()
    except Exception as e:  # pragma: no cover - DB unavailable at boot
        log.warning("Could not read MCP servers from DB: %s", e)
        db_rows = []

    for row in db_rows:
        name = row["name"]
        if name in merged:
            continue
        try:
            merged[name] = json.loads(row["config"])
        except Exception as e:
            log.warning("Skipping MCP server '%s': invalid config JSON (%s)", name, e)
    return merged


async def init_mcp():
    if mcp_client is None or _config is None:
        return
    servers = await _merged_mcp_servers(_config)
    await mcp_client.initialize(servers)

async def init_skills():
    if skill_registry:
        skill_registry.discover()

async def init_plugins():
    if plugin_registry:
        plugin_registry.discover()


async def reload_all_tools():
    """Reload all tools from the tools directory."""
    global tools
    cfg = get_config()
    from codeassist.dynamic_tools import DynamicToolLoader

    if tools is None:
        await _init_subsystems(cfg)
        return

    loader = DynamicToolLoader(cfg.workspace, trust_registry=trust_registry)
    loader.reload_registry(tools)
    log.info("Tools reloaded successfully")


async def reload_configured_subsystems() -> None:
    """Re-read config from disk and re-initialize config-driven subsystems.

    Lets admin edits to restart-required *feature* toggles (MCP, skills,
    plugins, LSP, git) take effect without a full process restart. Server bind
    changes (host/port/password) still require a container/process restart, so
    the caller reports those separately.
    """
    global mcp_client, skill_registry, plugin_registry, tools, trust_registry, lsp_client
    cfg = get_config()
    from .settings import apply_settings_overrides

    await apply_settings_overrides(cfg)

    # Release existing handles so ports/sockets are freed before re-binding.
    try:
        if lsp_client is not None:
            await lsp_client.shutdown()
    except Exception:  # pragma: no cover - defensive
        log.exception("Error shutting down LSP client during reload")
    try:
        if mcp_client is not None:
            await mcp_client.close()
    except Exception:  # pragma: no cover - defensive
        log.exception("Error closing MCP client during reload")

    await _init_subsystems(cfg)
    await init_skills()
    await init_plugins()
    await init_mcp()
    log.info("Configured subsystems reloaded from updated config")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    await init_db()
    # Apply UI-managed settings overrides (settings table) on top of config.toml
    # BEFORE subsystems boot so feature toggles and runtime knobs take effect.
    from .settings import apply_settings_overrides
    await apply_settings_overrides(cfg)
    await _init_subsystems(cfg)
    await init_agents()
    await init_mcp()
    await init_skills()
    await init_plugins()
    log.info("CodeAssist starting | model=%s workspace=%s", cfg.llm.model, cfg.workspace)

    yield
    if mcp_client:
        await mcp_client.close()


app = FastAPI(title="CodeAssist", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)


@app.middleware("http")
async def static_cache_headers(request, call_next):
    """Force browsers to revalidate static assets on every deploy.

    Without explicit cache headers, browsers fall back to heuristic caching and
    can serve a stale app.js/style.css after a container rebuild — which makes
    fresh UI changes (icons, layout) appear half-applied. Setting no-store on
    static assets guarantees the browser re-fetches them every load.
    """
    if request.url.path.startswith("/static/"):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    return await call_next(request)

# Register REST API routes (sessions, config, knowledge base, tools, agents, etc.)
from .routes import register_routes  # noqa: E402

register_routes(app)


@app.middleware("http")
async def auth_middleware(request, call_next):
    # Paths exempt from HTTP Basic Auth (WebSocket auth is handled separately
    # in the WS endpoint — do NOT add new /ws/ prefixes here blindly)
    _EXEMPT_PATHS = {"/health", "/favicon.ico"}
    _EXEMPT_PREFIXES = ("/static/",)

    path = request.url.path
    if path in _EXEMPT_PATHS or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
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
    """Health check endpoint. Returns server status, model, and workspace path."""
    cfg = get_config()
    return {
        "status": "ok",
        "model": cfg.llm.model,
        "workspace": str(cfg.workspace),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main chat UI (index.html)."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


# ── WebSocket endpoint ──────────────────────────────────────────

MAX_SESSION_NAME_LEN = 200
MAX_MESSAGE_LEN = 100_000
MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_MIME = ("image/png", "image/jpeg", "image/webp", "image/gif")

MAX_FILES_PER_MESSAGE = 5
MAX_FILE_BYTES = 256 * 1024


def _parse_text_files(files) -> tuple[list[dict], str | None]:
    """Validate incoming text-file attachments.

    Returns (attachments, error_message). Each attachment is
    {attachment_type, mime_type, file_name, data} where data is the file text.
    """
    if not files:
        return [], None
    if len(files) > MAX_FILES_PER_MESSAGE:
        return [], f"Too many files. Maximum is {MAX_FILES_PER_MESSAGE} per message."

    attachments = []
    for i, file in enumerate(files):
        if not isinstance(file, dict):
            return [], "Invalid file attachment."
        name = str(file.get("name") or f"file-{i + 1}").strip()[:255]
        content = file.get("content")
        if content is None:
            return [], "Invalid file attachment."
        content = str(content)
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            size_kb = len(content.encode("utf-8")) / 1024
            return [], (
                f"File '{name}' is too large ({size_kb:.0f}KB). "
                f"Maximum is {MAX_FILE_BYTES // 1024}KB."
            )
        attachments.append({
            "attachment_type": "text",
            "mime_type": "text/plain",
            "file_name": name,
            "data": content,
        })
    return attachments, None


def _parse_image_attachments(images: list) -> tuple[list[dict], str | None]:
    """Validate incoming base64 image data URLs.

    Returns (attachments, error_message). On success error_message is None and
    each attachment is {attachment_type, mime_type, file_name, data} where data
    is the original data URL.
    """
    if not images:
        return [], None
    if len(images) > MAX_IMAGES_PER_MESSAGE:
        return [], f"Too many images. Maximum is {MAX_IMAGES_PER_MESSAGE} per message."

    attachments = []
    for img in images:
        if not isinstance(img, str) or not img.startswith("data:image/"):
            return [], "Invalid image attachment."
        try:
            header, _, b64 = img.partition(",")
            mime = header[len("data:"):].split(";")[0].lower()
            if mime not in ALLOWED_IMAGE_MIME:
                return [], f"Unsupported image type '{mime}'. Supported: {', '.join(ALLOWED_IMAGE_MIME)}"
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            return [], "Invalid image data URL."
        if not raw:
            return [], "Invalid image data URL."
        if len(raw) > MAX_IMAGE_BYTES:
            size_mb = len(raw) / (1024 * 1024)
            return [], f"Image too large ({size_mb:.1f}MB). Maximum is {MAX_IMAGE_BYTES // (1024 * 1024)}MB."
        attachments.append({
            "attachment_type": "image",
            "mime_type": mime,
            "file_name": f"image-{len(attachments) + 1}.img",
            "data": img,
        })
    return attachments, None


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    from codeassist.session import Session, Agent as AgentRecord
    from codeassist.agent import Agent
    from codeassist.agents import agent_manager
    from codeassist.session_hook import get_session_hook
    import asyncio
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

    session = await Session.get_or_create(session_id)

    # Get current agent: per-session choice (agent switcher), defaulting to config.
    current_agent_name = await session.get_agent_name() or cfg.agent.default_agent
    agent_config_obj = agent_manager.get_agent(current_agent_name)
    if agent_config_obj is None:
        # Stored agent no longer exists (deleted since); fall back to default.
        current_agent_name = cfg.agent.default_agent
        agent_config_obj = agent_manager.get_agent(current_agent_name)

    # Discover and load project instructions (AGENTS.md, CLAUDE.md, etc.)
    discoverer = get_instruction_discoverer()
    instruction_sources = await discoverer.discover(
        workspace=cfg.workspace,
        config_paths=cfg.instructions.paths,
        disable_project_config=cfg.instructions.disable_project_config,
    )
    instructions_text = discoverer.get_combined_content(instruction_sources)

    # Build system prompt with instructions injected
    from codeassist.prompts import build_system_prompt
    base_prompt = agent_config_obj.get_system_prompt() if agent_config_obj else "You are a helpful coding assistant."
    system_prompt = build_system_prompt(
        workspace=cfg.workspace,
        model_id=cfg.llm.model,
        features={
            "mcp_enabled": cfg.mcp.enabled,
            "skills_enabled": cfg.skills.enabled,
            "plugins_enabled": cfg.plugins.enabled,
            "lsp_enabled": cfg.lsp.enabled,
            "git_enabled": cfg.git.enabled,
        },
        instructions=instructions_text if instructions_text else None,
    )
    # Prepend agent-specific description/instructions
    if agent_config_obj:
        agent_header = agent_config_obj.get_system_prompt()
        if agent_header and agent_header != base_prompt:
            system_prompt = agent_header + "\n\n" + system_prompt

    # Inject skill guidance into system prompt (if skills are enabled)
    if skill_registry and cfg.skills.enabled:
        skill_guidance = skill_registry.get_instructions()
        if skill_guidance:
            system_prompt += "\n\n" + skill_guidance

    # Use the global tools registry (supports dynamic reloading)
    if tools is None:
        await websocket.close(code=1011, reason="Server not initialized")
        return

    # Update SessionTool with the current session ID
    session_tool = tools.get("session")
    if session_tool and hasattr(session_tool, "current_session_id"):
        session_tool.current_session_id = session_id

    # Configure TaskTool with session context for subagent spawning
    task_tool = tools.get("task")
    if task_tool and hasattr(task_tool, "configure"):
        task_tool.configure(session_id, cfg, tools)

    agent = Agent(cfg, session, tools, system_prompt)
    agent_task: asyncio.Task | None = None

    # Tell the client which agent is active for this session (agent switcher).
    if agent_config_obj:
        _agent_info = agent_config_obj.to_dict()
        _agent_info["id"] = current_agent_name
        await websocket.send_json({
            "type": "active_agent",
            "agent": _agent_info,
        })

    async def run_agent_task(message: str, attachments: list | None = None):
        nonlocal agent_task
        try:
            async for event in agent.run(message, attachments or None):
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
                from codeassist.capabilities import model_vision_capable
                content = data.get("content", "")
                images, img_error = _parse_image_attachments(data.get("images") or [])
                files, file_error = _parse_text_files(data.get("files") or [])
                if img_error:
                    await websocket.send_json({"type": "error", "message": img_error})
                    continue
                if file_error:
                    await websocket.send_json({"type": "error", "message": file_error})
                    continue
                if not (content.strip() or images or files):
                    continue
                if len(content) > MAX_MESSAGE_LEN:
                    await websocket.send_json({"type": "error", "message": f"Message too long. Maximum is {MAX_MESSAGE_LEN} characters."})
                    continue
                if agent_task and not agent_task.done():
                    await websocket.send_json({"type": "error", "message": "Agent is busy, please wait"})
                    continue

                if images and not await model_vision_capable(cfg):
                    await websocket.send_json({
                        "type": "error",
                        "message": "This model/server does not support images. You can still attach text files.",
                    })
                    continue

                attachments = images + files
                agent_task = asyncio.create_task(run_agent_task(content, attachments))

            elif data.get("type") == "cancel":
                if agent_task and not agent_task.done():
                    agent.cancel()

            elif data.get("type") == "undo":
                deleted = await session.undo_last_turn()
                await websocket.send_json({
                    "type": "undo_done",
                    "deleted": deleted,
                })

            elif data.get("type") == "rollback":
                message_id = data.get("message_id")
                if message_id:
                    deleted = await session.delete_messages_after(message_id)
                    await websocket.send_json({
                        "type": "rollback_done",
                        "deleted": deleted,
                    })

            elif data.get("type") == "confirm_response":
                confirm_id = data.get("id")
                approved = data.get("approved", False)
                trust_workspace = data.get("trust_workspace", False)
                trust_shell = data.get("trust_shell", False)
                trust_tool = data.get("trust_tool", False)
                remember = data.get("remember", False)
                if confirm_id:
                    agent.resolve_confirm(confirm_id, approved, trust_workspace, trust_shell, trust_tool, remember)
                    # Persist a remembered permission from server-bound context,
                    # never from client-echoed tool/file_path values.
                    if remember and approved:
                        ctx = agent.get_confirm_context(confirm_id)
                        if ctx and ctx.get("tool"):
                            await agent.save_permission(ctx["tool"], ctx.get("file_path") or "", "allow")

            elif data.get("type") == "question_response":
                question_id = data.get("id")
                answer = data.get("answer", "")
                if question_id:
                    agent.resolve_confirm(question_id, True)
                    # Also resolve the question tool itself
                    question_tool = tools.get("question")
                    if question_tool and hasattr(question_tool, "set_answer"):
                        question_tool.set_answer(question_id, json.dumps(answer) if isinstance(answer, list) else answer)

            elif data.get("type") == "question_rejected":
                question_id = data.get("id")
                if question_id:
                    agent.resolve_confirm(question_id, False)
                    question_tool = tools.get("question")
                    if question_tool and hasattr(question_tool, "reject_question"):
                        question_tool.reject_question(question_id)

            elif data.get("type") == "switch_agent":
                agent_name = data.get("agent_name")
                if agent_name and agent_name != current_agent_name:
                    new_config = agent_manager.get_agent(agent_name)
                    if not new_config:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Agent '{agent_name}' not found",
                        })
                        continue
                    if agent_task and not agent_task.done():
                        await websocket.send_json({
                            "type": "error",
                            "message": "Agent is busy, switch when the current turn finishes",
                        })
                        continue
                    await session.set_agent_name(agent_name)
                    current_agent_name = agent_name

                    # Rebuild the system prompt for the new agent (mirrors connect path).
                    new_base_prompt = new_config.get_system_prompt() if new_config else "You are a helpful coding assistant."
                    new_header = new_config.get_system_prompt() if new_config else None
                    new_system_prompt = build_system_prompt(
                        workspace=cfg.workspace,
                        model_id=cfg.llm.model,
                        features={
                            "mcp_enabled": cfg.mcp.enabled,
                            "skills_enabled": cfg.skills.enabled,
                            "plugins_enabled": cfg.plugins.enabled,
                            "lsp_enabled": cfg.lsp.enabled,
                            "git_enabled": cfg.git.enabled,
                        },
                        instructions=instructions_text if instructions_text else None,
                    )
                    if new_header and new_header != new_base_prompt:
                        new_system_prompt = new_header + "\n\n" + new_system_prompt
                    agent.system_prompt = new_system_prompt

                    await websocket.send_json({
                        "type": "agent_switched",
                        "agent": {**new_config.to_dict(), "id": agent_name},
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
                        from codeassist.dynamic_tools import DynamicToolLoader
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
