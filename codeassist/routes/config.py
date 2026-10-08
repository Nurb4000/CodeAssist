"""Config and todo API routes."""
from fastapi import APIRouter

from ..capabilities import is_external_backend

router = APIRouter(tags=["config"])


def _human_size(num: int) -> str:
    """Format a byte count as a human-readable string (B/KB/MB/GB)."""
    value = float(num)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or u == "TB":
            return f"{value:.0f} {u}" if u == "B" else f"{value:.1f} {u}"
        value /= 1024
    return f"{num} B"


@router.get("/api/config")
async def api_config():
    """Get current server configuration (model, workspace, enabled features)."""
    from ..capabilities import (
        effective_context_window,
        get_backend_info,
        model_vision_capable,
    )
    from ..server import get_config
    cfg = get_config()
    vision = await model_vision_capable(cfg)
    backend = await get_backend_info(cfg)
    # Local vs external provider rule: auto-detect the in-use model for local /
    # self-hosted backends; hosted providers keep showing the admin-configured
    # model. Classification lives in capabilities.is_external_backend so this
    # route and the context-window budget can't drift apart.
    external = is_external_backend(cfg.llm.base_url)
    detected = backend.get("model")
    effective = detected if (not external and detected and backend.get("source") == "backend") else cfg.llm.model
    window = await effective_context_window(cfg)
    return {
        "model": cfg.llm.model,
        "detected_model": detected,
        "effective_model": effective,
        "provider": cfg.llm.provider,
        "workspace": str(cfg.workspace),
        # Report the agent *id* (registry key), not the display name: clients
        # select agents by id (see agents.list_agents) and map it to a label.
        # Returning the display name here made the chat UI revert to "CodeAssist"
        # on reload instead of the configured default agent's short label.
        "agent_name": cfg.agent.default_agent,
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


@router.get("/api/status")
async def api_status():
    """Operational status for the admin Health panel: DB path/size and whether
    any applied settings override requires a restart."""
    from ..session import DB_PATH
    from ..settings import BY_KEY, settings_store

    size_bytes = 0
    if DB_PATH.exists():
        try:
            size_bytes = DB_PATH.stat().st_size
        except OSError:  # pragma: no cover - path vanished between check and stat
            size_bytes = 0

    restart_keys = []
    if not settings_store.is_loaded():
        await settings_store.load()
    for key in settings_store.all():
        spec = BY_KEY.get(key)
        if spec and spec.get("restart_required"):
            restart_keys.append(spec["label"])

    return {
        "db_path": str(DB_PATH),
        "db_size_bytes": size_bytes,
        "db_size_human": _human_size(size_bytes),
        "restart_needed": len(restart_keys) > 0,
        "restart_count": len(restart_keys),
    }


# Todo endpoints. Both are session-scoped: the plan list belongs to one
# conversation, and the tool instance is shared across the whole process, so the
# caller has to say which session it means.
@router.get("/api/todos")
async def get_todos(session_id: str | None = None):
    """Get the task list for a session."""
    from ..server import tools
    if tools is None:
        return {"tasks": []}
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "load_session"):
        # Hydrate from the database first: after a restart the in-memory list is
        # empty even though the session still has a plan on disk.
        if session_id:
            await todo_tool.load_session(session_id)
        return {"tasks": todo_tool.get_tasks(session_id)}
    return {"tasks": []}


@router.post("/api/todos/clear")
async def clear_todos(session_id: str | None = None):
    """Clear the task list for one session.

    Without a session id this clears the currently-bound session, so it can no
    longer wipe another conversation's plan.
    """
    from ..server import tools
    if tools is None:
        return {"ok": True}
    todo_tool = tools.get("todo")
    if todo_tool and hasattr(todo_tool, "clear_tasks"):
        await todo_tool.clear_tasks(session_id)
    return {"ok": True}


@router.get("/api/model/status")
async def model_status():
    """Check LLM model availability and return status information.

    Sends a lightweight test request to the configured LLM backend and returns
    the response time, model name, and any rate limit headers present.
    """
    from ..config import get_config
    from ..llm import LLMClient

    cfg = get_config()
    llm = LLMClient(cfg.llm)

    result = {
        "model": cfg.llm.model,
        "provider": cfg.llm.provider,
        "base_url": cfg.llm.base_url or "api.openai.com",
        "context_window": cfg.llm.context_window,
        "available": False,
        "response_time_ms": None,
        "rate_limit_remaining": None,
        "error": None,
    }

    import time
    start = time.monotonic()
    try:
        events = []
        async for event in llm.stream([{"role": "user", "content": "."}]):
            events.append(event)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        result["available"] = True
        result["response_time_ms"] = elapsed_ms

        # Extract rate limit info from any finish event
        for ev in events:
            if hasattr(ev, "rate_limit_remaining") and ev.rate_limit_remaining is not None:
                result["rate_limit_remaining"] = ev.rate_limit_remaining
                break

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    return result
