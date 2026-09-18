"""Config and todo API routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["config"])


@router.get("/api/config")
async def api_config():
    """Get current server configuration (model, workspace, enabled features)."""
    from ..server import get_config
    from ..capabilities import get_backend_info, model_vision_capable
    cfg = get_config()
    vision = await model_vision_capable(cfg)
    backend = await get_backend_info(cfg)
    # Local vs external provider rule: auto-detect the in-use model for local /
    # self-hosted backends; external providers (api.openai.com, blank base_url)
    # keep showing the admin-configured model as before.
    base = (cfg.llm.base_url or "").strip().lower()
    external = (not base) or "api.openai.com" in base
    detected = backend.get("model")
    effective = detected if (not external and detected and backend.get("source") == "backend") else cfg.llm.model
    window = backend.get("context_window") if (not external and backend.get("context_window")) else cfg.llm.context_window
    return {
        "model": cfg.llm.model,
        "detected_model": detected,
        "effective_model": effective,
        "provider": cfg.llm.provider,
        "workspace": str(cfg.workspace),
        "agent_name": cfg.agent.name,
        "vision": cfg.llm.vision,
        "vision_capable": vision,
        "context_window": cfg.llm.context_window,
        "effective_context_window": window,
        "detected_context_window": backend.get("context_window"),
        # Only claim auto-detection for local backends; external providers keep
        # showing the configured model with no "auto" badge.
        "backend_source": ("backend" if (backend.get("source") and not external) else None),
        "backend_external": external,
        "features": {
            "mcp_enabled": cfg.mcp.enabled,
            "skills_enabled": cfg.skills.enabled,
            "plugins_enabled": cfg.plugins.enabled,
            "lsp_enabled": cfg.lsp.enabled,
            "git_enabled": cfg.git.enabled,
        },
    }


# Todo endpoints
@router.get("/api/todos")
async def get_todos():
    """Get the current todo list from the active agent."""
    from ..server import tools
    if tools is None:
        return {"tasks": []}
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "get_tasks"):
        return {"tasks": todo_tool.get_tasks()}
    return {"tasks": []}


@router.post("/api/todos/clear")
async def clear_todos():
    """Clear all tasks from the active agent's todo list."""
    from ..server import tools
    if tools is None:
        return {"ok": True}
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "clear_tasks"):
        todo_tool.clear_tasks()
    return {"ok": True}
