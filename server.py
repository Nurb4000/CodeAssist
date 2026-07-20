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


# ── Knowledge Base Endpoints ──────────────────────────────────────────────

@app.get("/api/knowledge")
async def list_knowledge(
    entry_type: str = None,
    scope: str = None,
    scope_identifier: str = None,
    min_confidence: float = 0.0,
    limit: int = 50,
):
    """List knowledge entries with optional filters."""
    from knowledge import KnowledgeBase
    return await KnowledgeBase.search_knowledge(
        entry_type=entry_type,
        scope=scope,
        scope_identifier=scope_identifier,
        min_confidence=min_confidence,
        limit=limit,
    )


@app.get("/api/knowledge/search")
async def search_knowledge(q: str, entry_type: str = None, limit: int = 20):
    """Full-text search across knowledge entries."""
    from knowledge import KnowledgeBase
    try:
        results = await KnowledgeBase.fulltext_search_knowledge(q, entry_type=entry_type, limit=limit)
        if results:
            return results
    except Exception:
        pass
    # Fallback to LIKE search
    return await KnowledgeBase.search_knowledge(
        entry_type=entry_type,
        min_confidence=0.0,
        limit=limit,
    )


@app.post("/api/knowledge")
async def create_knowledge(body: dict):
    """Create a knowledge entry."""
    from knowledge import KnowledgeBase
    entry_type = body.get("entry_type")
    scope = body.get("scope")
    content = body.get("content")
    
    if not all([entry_type, scope, content]):
        raise HTTPException(status_code=400, detail="entry_type, scope, and content are required")
    
    entry_id = await KnowledgeBase.create_knowledge_entry(
        entry_type=entry_type,
        scope=scope,
        content=content,
        scope_identifier=body.get("scope_identifier"),
        source_session_id=body.get("source_session_id"),
        confidence=body.get("confidence", 1.0),
        tags=body.get("tags"),
        metadata=body.get("metadata"),
    )
    return {"id": entry_id}


@app.get("/api/knowledge/{entry_id}")
async def get_knowledge(entry_id: str):
    """Get a specific knowledge entry."""
    from knowledge import KnowledgeBase
    entry = await KnowledgeBase.get_knowledge_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return entry


@app.put("/api/knowledge/{entry_id}")
async def update_knowledge(entry_id: str, body: dict):
    """Update a knowledge entry."""
    from knowledge import KnowledgeBase
    updated = await KnowledgeBase.update_knowledge_entry(entry_id, **body)
    if not updated:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return {"ok": True}


@app.delete("/api/knowledge/{entry_id}")
async def delete_knowledge(entry_id: str):
    """Delete a knowledge entry."""
    from knowledge import KnowledgeBase
    deleted = await KnowledgeBase.delete_knowledge_entry(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return {"ok": True}


@app.get("/api/knowledge/semantic")
async def semantic_search(q: str, entry_type: str = None, limit: int = 10):
    """Semantic search using embeddings (falls back to text search if embeddings unavailable)."""
    from embeddings import get_embedding_manager
    manager = get_embedding_manager()
    return await manager.search_by_embedding(q, limit=limit, entry_type=entry_type)


@app.get("/api/knowledge/{entry_id}/similar")
async def find_similar_knowledge(entry_id: str, limit: int = 5):
    """Find knowledge entries similar to a given entry."""
    from embeddings import get_embedding_manager
    manager = get_embedding_manager()
    return await manager.search_by_entry_embedding(entry_id, limit=limit)


@app.post("/api/knowledge/embeddings/generate")
async def generate_embeddings(batch_size: int = 10):
    """Generate embeddings for knowledge entries that don't have them."""
    from embeddings import get_embedding_manager
    manager = get_embedding_manager()
    count = await manager.generate_embeddings_for_all(batch_size=batch_size)
    return {"generated": count}


# ── Analytics Endpoints ───────────────────────────────────────────────────

@app.get("/api/analytics/tools")
async def tool_stats(
    session_id: str = None,
    tool_name: str = None,
    period_days: int = None,
):
    """Get tool usage statistics."""
    from knowledge import KnowledgeBase
    return await KnowledgeBase.get_tool_stats(
        session_id=session_id,
        tool_name=tool_name,
        period_days=period_days,
    )


@app.get("/api/analytics/llm")
async def llm_stats(
    session_id: str = None,
    model: str = None,
    period_days: int = None,
):
    """Get LLM usage statistics."""
    from knowledge import KnowledgeBase
    return await KnowledgeBase.get_llm_stats(
        session_id=session_id,
        model=model,
        period_days=period_days,
    )


# ── Session Tags Endpoints ────────────────────────────────────────────────

@app.get("/api/sessions/search/tags")
async def search_sessions_by_tags(tags: str, match_all: bool = False, limit: int = 50):
    """Search sessions by tags (comma-separated)."""
    from knowledge import KnowledgeBase
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        raise HTTPException(status_code=400, detail="tags parameter is required")
    return await KnowledgeBase.search_sessions_by_tags(tag_list, match_all=match_all, limit=limit)


@app.get("/api/sessions/{session_id}/tags")
async def get_session_tags(session_id: str):
    """Get tags for a session."""
    from knowledge import KnowledgeBase
    return await KnowledgeBase.get_session_tags(session_id)


@app.post("/api/sessions/{session_id}/tags")
async def add_session_tag(session_id: str, body: dict):
    """Add a tag to a session."""
    from knowledge import KnowledgeBase
    tag = body.get("tag")
    if not tag:
        raise HTTPException(status_code=400, detail="tag is required")
    
    tag_id = await KnowledgeBase.add_session_tag(
        session_id=session_id,
        tag=tag,
        source=body.get("source", "user"),
    )
    return {"id": tag_id}


# ── File History Endpoints ────────────────────────────────────────────────

@app.get("/api/files/history")
async def file_history(file_path: str, limit: int = 50):
    """Get modification history for a file."""
    from knowledge import KnowledgeBase
    return await KnowledgeBase.get_file_history(file_path, limit=limit)


# ── Custom Tools Management ───────────────────────────────────────────

@app.get("/api/custom-tools")
async def list_custom_tools():
    """List all custom tools."""
    from custom_tools_loader import get_custom_tool_registry
    from config import load_config
    
    config = load_config()
    workspace = Path(config.server.workspace)
    registry = get_custom_tool_registry(workspace)
    registry.discover()
    
    return {"tools": registry.list_tools()}


@app.post("/api/custom-tools/reload")
async def reload_custom_tools():
    """Reload all custom tools from disk."""
    from custom_tools_loader import get_custom_tool_registry
    from config import load_config
    
    config = load_config()
    workspace = Path(config.server.workspace)
    registry = get_custom_tool_registry(workspace)
    registry.reload()
    
    return {"message": "Custom tools reloaded", "count": len(registry._tools)}


@app.get("/api/skills/list")
async def list_all_skills():
    """List all available skills (built-in + custom)."""
    from skills import SkillRegistry
    from config import load_config
    
    config = load_config()
    workspace = Path(config.server.workspace)
    registry = SkillRegistry(workspace, config.skills)
    skills = registry.discover()
    
    return {"skills": [s.to_dict() for s in skills]}


@app.post("/api/skills/reload")
async def reload_skills():
    """Reload all skills from disk."""
    from skills import SkillRegistry
    from config import load_config
    
    config = load_config()
    workspace = Path(config.server.workspace)
    registry = SkillRegistry(workspace, config.skills)
    registry.reload()
    
    return {"message": "Skills reloaded", "count": len(registry._skills)}


@app.get("/api/auto-creation/status")
async def auto_creation_status():
    """Get auto-creation status and stats."""
    from config import load_config
    from knowledge import KnowledgeBase
    
    config = load_config()
    
    # Get recent auto-created skills
    recent_skills = await KnowledgeBase.search_knowledge(
        entry_type="skill_created",
        scope="project",
        min_confidence=0.5,
        limit=10,
    )
    
    # Get recent auto-created tools
    recent_tools = await KnowledgeBase.search_knowledge(
        entry_type="tool_created",
        scope="project",
        min_confidence=0.5,
        limit=10,
    )
    
    # Get detected patterns
    patterns = await KnowledgeBase.search_knowledge(
        entry_type="pattern",
        tags=["repetitive_workflow"],
        min_confidence=0.5,
        limit=10,
    )
    
    return {
        "enabled": {
            "skills": config.agent.auto_create_skills,
            "tools": config.agent.auto_create_tools,
        },
        "limits": {
            "max_per_session": config.agent.max_auto_creations,
            "min_confidence": config.agent.min_confidence,
        },
        "recent_skills": recent_skills,
        "recent_tools": recent_tools,
        "detected_patterns": patterns,
    }


# ── Knowledge Base GUI API ────────────────────────────────────────────

@app.get("/api/kb/stats")
async def kb_stats():
    """Get KB statistics for dashboard."""
    from knowledge import KnowledgeBase
    
    # Get entry counts by type
    entry_types = ["pattern", "convention", "decision", "bug_fix", "optimization", 
                   "skill_created", "tool_created", "effectiveness"]
    
    stats = {}
    for entry_type in entry_types:
        entries = await KnowledgeBase.search_knowledge(entry_type=entry_type, limit=1000)
        stats[entry_type] = len(entries)
    
    # Get total entries
    all_entries = await KnowledgeBase.search_knowledge(limit=10000)
    stats["total"] = len(all_entries)
    
    # Get recent activity (last 7 days)
    from session import get_db
    async with get_db() as db:
        # Sessions count
        cursor = await db.execute("SELECT COUNT(*) FROM sessions")
        stats["total_sessions"] = (await cursor.fetchone())[0]
        
        # Tool executions
        cursor = await db.execute("SELECT COUNT(*) FROM tool_executions")
        stats["total_tool_calls"] = (await cursor.fetchone())[0]
        
        # LLM usage
        cursor = await db.execute("SELECT COUNT(*) FROM llm_usage")
        stats["total_llm_calls"] = (await cursor.fetchone())[0]
        
        # Recent entries (last 7 days)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM knowledge_entries WHERE created_at >= datetime('now', '-7 days')"
        )
        stats["recent_entries_7d"] = (await cursor.fetchone())[0]
        
        # Average quality score
        cursor = await db.execute(
            "SELECT AVG(quality_score) FROM session_summaries WHERE quality_score IS NOT NULL"
        )
        avg_quality = (await cursor.fetchone())[0]
        stats["avg_quality_score"] = round(avg_quality, 2) if avg_quality else 0
    
    return stats


@app.get("/api/kb/entries")
async def kb_list_entries(
    entry_type: str | None = None,
    scope: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List KB entries with filters."""
    from knowledge import KnowledgeBase
    
    if search:
        entries = await KnowledgeBase.fulltext_search_knowledge(search, entry_type=entry_type, limit=limit)
    else:
        tags = [tag] if tag else None
        entries = await KnowledgeBase.search_knowledge(
            entry_type=entry_type,
            scope=scope,
            tags=tags,
            limit=limit + offset,
        )
    
    # Apply offset
    entries = entries[offset:offset + limit]
    
    return {"entries": entries, "count": len(entries)}


@app.get("/api/kb/entries/{entry_id}")
async def kb_get_entry(entry_id: str):
    """Get a specific KB entry."""
    from knowledge import KnowledgeBase
    entry = await KnowledgeBase.get_knowledge_entry(entry_id)
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Entry not found"})
    return entry


@app.put("/api/kb/entries/{entry_id}")
async def kb_update_entry(entry_id: str, body: dict):
    """Update a KB entry."""
    from knowledge import KnowledgeBase
    
    # Extract allowed fields
    updates = {}
    for field in ["content", "confidence", "tags", "metadata"]:
        if field in body:
            updates[field] = body[field]
    
    if not updates:
        return JSONResponse(status_code=400, content={"error": "No valid fields to update"})
    
    success = await KnowledgeBase.update_knowledge_entry(entry_id, **updates)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Entry not found or no changes"})
    
    return {"message": "Entry updated", "entry_id": entry_id}


@app.delete("/api/kb/entries/{entry_id}")
async def kb_delete_entry(entry_id: str):
    """Delete a KB entry."""
    from knowledge import KnowledgeBase
    success = await KnowledgeBase.delete_knowledge_entry(entry_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Entry not found"})
    return {"message": "Entry deleted", "entry_id": entry_id}


@app.post("/api/kb/entries/bulk-delete")
async def kb_bulk_delete(body: dict):
    """Bulk delete KB entries."""
    from knowledge import KnowledgeBase
    
    entry_ids = body.get("entry_ids", [])
    if not entry_ids:
        return JSONResponse(status_code=400, content={"error": "No entry IDs provided"})
    
    deleted = 0
    for entry_id in entry_ids:
        if await KnowledgeBase.delete_knowledge_entry(entry_id):
            deleted += 1
    
    return {"message": f"Deleted {deleted} entries", "deleted": deleted}


@app.post("/api/kb/entries")
async def kb_create_entry(body: dict):
    """Create a new KB entry manually."""
    from knowledge import KnowledgeBase
    
    required = ["entry_type", "scope", "content"]
    for field in required:
        if field not in body:
            return JSONResponse(status_code=400, content={"error": f"Missing required field: {field}"})
    
    entry_id = await KnowledgeBase.create_knowledge_entry(
        entry_type=body["entry_type"],
        scope=body["scope"],
        content=body["content"],
        scope_identifier=body.get("scope_identifier"),
        confidence=body.get("confidence", 1.0),
        tags=body.get("tags"),
        metadata=body.get("metadata"),
    )
    
    return {"message": "Entry created", "entry_id": entry_id}


@app.get("/api/kb/search")
async def kb_search(
    q: str,
    entry_type: str | None = None,
    semantic: bool = False,
    limit: int = 20,
):
    """Search KB entries."""
    from knowledge import KnowledgeBase
    
    if semantic:
        # Try semantic search
        try:
            from embeddings import get_embedding_manager
            manager = get_embedding_manager()
            results = await manager.search_by_embedding(q, limit=limit)
            return {"results": results, "type": "semantic"}
        except Exception as e:
            # Fallback to text search
            pass
    
    # Text search
    results = await KnowledgeBase.fulltext_search_knowledge(q, entry_type=entry_type, limit=limit)
    return {"results": results, "type": "text"}


@app.get("/api/kb/sessions")
async def kb_list_sessions(limit: int = 50, offset: int = 0):
    """List sessions with summaries."""
    from session import get_db
    
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT s.*, ss.summary, ss.quality_score, ss.key_topics, ss.tools_used
               FROM sessions s
               LEFT JOIN session_summaries ss ON s.id = ss.session_id
               ORDER BY s.updated_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        sessions = [dict(row) for row in await cursor.fetchall()]
    
    return {"sessions": sessions, "count": len(sessions)}


@app.get("/api/kb/sessions/{session_id}")
async def kb_get_session(session_id: str):
    """Get session details with summary."""
    from session import get_db, Session
    
    session = Session(session_id)
    messages = await session.get_messages()
    
    from knowledge import KnowledgeBase
    summary = await KnowledgeBase.get_session_summary(session_id)
    tags = await KnowledgeBase.get_session_tags(session_id)
    
    # Get tool executions
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM tool_executions WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        tool_calls = [dict(row) for row in await cursor.fetchall()]
    
    return {
        "session_id": session_id,
        "messages": messages[:100],  # Limit to 100 messages
        "summary": summary,
        "tags": tags,
        "tool_calls": tool_calls,
    }


@app.get("/api/kb/analytics/tools")
async def kb_analytics_tools(period_days: int = 30):
    """Get tool usage analytics."""
    from knowledge import KnowledgeBase
    return await KnowledgeBase.get_tool_stats(period_days=period_days)


@app.get("/api/kb/analytics/llm")
async def kb_analytics_llm(period_days: int = 30):
    """Get LLM usage analytics."""
    from knowledge import KnowledgeBase
    return await KnowledgeBase.get_llm_stats(period_days=period_days)


@app.get("/api/kb/pii/scan")
async def kb_pii_scan():
    """Scan KB for potential PII."""
    from knowledge import KnowledgeBase
    import re
    
    # PII patterns
    pii_patterns = {
        "email": r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        "ip_address": r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
        "phone": r'\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b',
        "ssn": r'\b\d{3}[-]?\d{2}[-]?\d{4}\b',
        "credit_card": r'\b(?:\d{4}[-]?){3}\d{4}\b',
        "api_key": r'(?i)(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?key)[=:]\s*["\']?[A-Za-z0-9]{20,}',
    }
    
    entries = await KnowledgeBase.search_knowledge(limit=10000)
    flagged = []
    
    for entry in entries:
        content = entry.get("content", "") or ""
        entry_id = entry.get("id", "")
        
        for pii_type, pattern in pii_patterns.items():
            matches = re.findall(pattern, content)
            if matches:
                flagged.append({
                    "entry_id": entry_id,
                    "entry_type": entry.get("entry_type"),
                    "pii_type": pii_type,
                    "matches": matches[:5],  # Limit matches
                    "content_preview": content[:200],
                })
    
    return {"flagged": flagged, "total_scanned": len(entries)}


@app.post("/api/kb/pii/redact")
async def kb_pii_redact(body: dict):
    """Redact PII from an entry."""
    from knowledge import KnowledgeBase
    import re
    
    entry_id = body.get("entry_id")
    pii_type = body.get("pii_type")
    
    if not entry_id:
        return JSONResponse(status_code=400, content={"error": "entry_id required"})
    
    entry = await KnowledgeBase.get_knowledge_entry(entry_id)
    if not entry:
        return JSONResponse(status_code=404, content={"error": "Entry not found"})
    
    content = entry.get("content", "")
    
    # Redaction patterns
    redaction_map = {
        "email": r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        "ip_address": r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
        "phone": r'\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b',
        "ssn": r'\b\d{3}[-]?\d{2}[-]?\d{4}\b',
        "credit_card": r'\b(?:\d{4}[-]?){3}\d{4}\b',
        "api_key": r'(?i)(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?key)[=:]\s*["\']?[A-Za-z0-9]{20,}',
    }
    
    if pii_type and pii_type in redaction_map:
        redacted_content = re.sub(redaction_map[pii_type], f'[REDACTED {pii_type.upper()}]', content)
        await KnowledgeBase.update_knowledge_entry(entry_id, content=redacted_content)
        return {"message": f"Redacted {pii_type}", "entry_id": entry_id}
    else:
        # Redact all PII types
        redacted_content = content
        for ptype, pattern in redaction_map.items():
            redacted_content = re.sub(pattern, f'[REDACTED {ptype.upper()}]', redacted_content)
        
        await KnowledgeBase.update_knowledge_entry(entry_id, content=redacted_content)
        return {"message": "All PII redacted", "entry_id": entry_id}


@app.get("/api/kb/settings")
async def kb_get_settings():
    """Get KB/auto-creation settings."""
    from config import load_config
    config = load_config()
    
    return {
        "auto_create_skills": config.agent.auto_create_skills,
        "auto_create_tools": config.agent.auto_create_tools,
        "max_auto_creations": config.agent.max_auto_creations,
        "min_confidence": config.agent.min_confidence,
    }


@app.put("/api/kb/settings")
async def kb_update_settings(body: dict):
    """Update KB settings (runtime only, not persisted to config file)."""
    from config import load_config
    config = load_config()
    
    # Update runtime config (will reset on restart)
    if "auto_create_skills" in body:
        config.agent.auto_create_skills = body["auto_create_skills"]
    if "auto_create_tools" in body:
        config.agent.auto_create_tools = body["auto_create_tools"]
    if "max_auto_creations" in body:
        config.agent.max_auto_creations = body["max_auto_creations"]
    if "min_confidence" in body:
        config.agent.min_confidence = body["min_confidence"]
    
    return {"message": "Settings updated (runtime only)", "settings": await kb_get_settings()}


@app.post("/api/kb/export")
async def kb_export(body: dict | None = None):
    """Export KB entries to JSON."""
    from knowledge import KnowledgeBase
    import json
    from datetime import datetime
    
    format_type = body.get("format", "json") if body else "json"
    
    entries = await KnowledgeBase.search_knowledge(limit=100000)
    
    if format_type == "csv":
        # CSV export
        import csv
        import io
        
        output = io.StringIO()
        if entries:
            writer = csv.DictWriter(output, fieldnames=entries[0].keys())
            writer.writeheader()
            writer.writerows(entries)
        
        return {
            "format": "csv",
            "data": output.getvalue(),
            "count": len(entries),
            "filename": f"codeassist_kb_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        }
    else:
        # JSON export
        return {
            "format": "json",
            "data": json.dumps(entries, indent=2, default=str),
            "count": len(entries),
            "filename": f"codeassist_kb_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        }


@app.post("/api/kb/import")
async def kb_import(body: dict):
    """Import KB entries from JSON."""
    from knowledge import KnowledgeBase
    
    data = body.get("data", [])
    if not data:
        return JSONResponse(status_code=400, content={"error": "No data provided"})
    
    imported = 0
    for entry in data:
        try:
            await KnowledgeBase.create_knowledge_entry(
                entry_type=entry.get("entry_type", "pattern"),
                scope=entry.get("scope", "project"),
                content=entry.get("content", ""),
                scope_identifier=entry.get("scope_identifier"),
                confidence=entry.get("confidence", 1.0),
                tags=entry.get("tags"),
                metadata=entry.get("metadata"),
            )
            imported += 1
        except Exception as e:
            log.warning("Failed to import entry: %s", e)
    
    return {"message": f"Imported {imported} entries", "imported": imported}


@app.delete("/api/kb/clear")
async def kb_clear(body: dict):
    """Clear all KB entries (dangerous!)."""
    from session import get_db
    
    confirm = body.get("confirm", False)
    if not confirm:
        return JSONResponse(status_code=400, content={"error": "Set confirm=true to clear all KB entries"})
    
    async with get_db() as db:
        # Delete in order (foreign keys)
        await db.execute("DELETE FROM session_tags")
        await db.execute("DELETE FROM file_snapshots")
        await db.execute("DELETE FROM qa_pairs")
        await db.execute("DELETE FROM tool_executions")
        await db.execute("DELETE FROM llm_usage")
        await db.execute("DELETE FROM knowledge_entries")
        await db.execute("DELETE FROM session_summaries")
        await db.commit()
    
    return {"message": "All KB entries cleared"}


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
        # Generate session summary and extract knowledge
        try:
            from session_hook import get_session_hook
            hook = get_session_hook()
            await hook.on_session_end(session, agent)
        except Exception as e:
            log.warning("Failed to generate session summary: %s", e)
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
