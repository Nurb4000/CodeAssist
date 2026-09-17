"""API route registration."""
from fastapi import FastAPI


def register_routes(app: FastAPI):
    """Register all API route routers with the FastAPI app."""
    from .sessions import router as sessions_router
    from .config import router as config_router
    from .agents import router as agents_router
    from .mcp import router as mcp_router
    from .skills import router as skills_router
    from .plugins import router as plugins_router
    from .lsp import router as lsp_router
    from .git import router as git_router
    from .tools import router as tools_router
    from .knowledge import router as knowledge_router
    from .custom_tools import router as custom_tools_router
    from .kb_gui import router as kb_router

    app.include_router(sessions_router)
    app.include_router(config_router)
    app.include_router(agents_router)
    app.include_router(mcp_router)
    app.include_router(skills_router)
    app.include_router(plugins_router)
    app.include_router(lsp_router)
    app.include_router(git_router)
    app.include_router(tools_router)
    app.include_router(knowledge_router)
    app.include_router(custom_tools_router)
    app.include_router(kb_router)
