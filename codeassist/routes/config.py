"""Config and todo API routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["config"])


@router.get("/api/config")
async def api_config():
    from ..server import get_config
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
@router.get("/api/todos")
async def get_todos():
    from ..server import tools
    if tools is None:
        return {"tasks": []}
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "get_tasks"):
        return {"tasks": todo_tool.get_tasks()}
    return {"tasks": []}


@router.post("/api/todos/clear")
async def clear_todos():
    from ..server import tools
    if tools is None:
        return {"ok": True}
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "clear_tasks"):
        todo_tool.clear_tasks()
    return {"ok": True}
