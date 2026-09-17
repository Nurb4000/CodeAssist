"""Tool management API routes."""
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.post("/reload")
async def reload_tools_endpoint():
    """Hot-reload all tools (built-in + custom) without restarting the server."""
    from ..server import reload_all_tools
    try:
        await reload_all_tools()
        return {"ok": True, "message": "Tools reloaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reload tools: {e}")


@router.get("/list")
async def list_tools():
    """List all available tool names."""
    from ..server import tools
    if tools is None:
        return {"tools": []}
    return {"tools": [name for name in tools.list_names()]}


# ── Tool Management GUI ────────────────────────────────────────

@router.get("/manage/list")
async def list_all_tools():
    """List all tools (built-in + custom) with usage stats and trust status."""
    from ..server import get_config, get_trust_registry
    from tools import get_tools
    from codeassist.custom_tools_loader import get_custom_tool_registry
    from codeassist.knowledge import KnowledgeBase

    config = get_config()
    workspace = Path(config.server.workspace)
    trust_registry = get_trust_registry()

    builtin_tools = get_tools(config)

    custom_registry = get_custom_tool_registry(workspace, trust_registry=trust_registry)
    custom_registry.discover()

    tool_stats = await KnowledgeBase.get_tool_stats()

    tools_list = []

    for name, tool in builtin_tools.items():
        stats = tool_stats.get(name, {})
        tools_list.append({
            "name": name,
            "type": "builtin",
            "description": getattr(tool, 'description', ''),
            "enabled": True,
            "trusted": True,
            "usage_count": stats.get("total_calls", 0),
            "success_rate": round(stats.get("successful", 0) / max(stats.get("total_calls", 1), 1) * 100, 1),
        })

    for tool in custom_registry.list_tools():
        name = tool["name"]
        stats = tool_stats.get(name, {})
        tools_list.append({
            "name": name,
            "type": "custom",
            "description": tool.get("description", ""),
            "enabled": True,
            "trusted": False,
            "source": tool.get("source", ""),
            "usage_count": stats.get("total_calls", 0),
            "success_rate": round(stats.get("successful", 0) / max(stats.get("total_calls", 1), 1) * 100, 1),
        })

    return {"tools": tools_list, "count": len(tools_list)}


@router.get("/manage/usage")
async def get_tool_usage_stats(period_days: int = 30):
    """Get tool usage statistics for the specified period (default: 30 days)."""
    from codeassist.knowledge import KnowledgeBase

    stats = await KnowledgeBase.get_tool_stats(period_days=period_days)

    sorted_stats = sorted(stats.items(), key=lambda x: x[1].get("total_calls", 0), reverse=True)

    return {
        "period_days": period_days,
        "tools": {name: data for name, data in sorted_stats},
        "total_calls": sum(data.get("total_calls", 0) for _, data in sorted_stats),
    }


@router.get("/manage/{tool_name}")
async def get_tool_details(tool_name: str):
    """Get detailed information about a specific tool including its schema and source code."""
    from ..server import get_config, get_trust_registry
    from tools import get_tools
    from codeassist.custom_tools_loader import get_custom_tool_registry

    config = get_config()
    workspace = Path(config.server.workspace)
    trust_registry = get_trust_registry()

    builtin_tools = get_tools(config)

    if tool_name in builtin_tools:
        tool = builtin_tools[tool_name]
        return {
            "name": tool_name,
            "type": "builtin",
            "description": getattr(tool, 'description', ''),
            "schema": getattr(tool, 'schema', lambda: {})(),
            "trusted": True,
        }

    custom_registry = get_custom_tool_registry(workspace, trust_registry=trust_registry)
    custom_registry.discover()

    custom_tool = custom_registry.get_tool(tool_name)
    if custom_tool:
        source_path = Path(custom_tool.source_path)
        source_code = ""
        if source_path.exists():
            source_code = source_path.read_text(encoding="utf-8")

        return {
            "name": tool_name,
            "type": "custom",
            "description": custom_tool.description,
            "schema": custom_tool.schema(),
            "source_code": source_code,
            "source_path": str(source_path),
            "trusted": custom_tool.trusted,
        }

    return JSONResponse(status_code=404, content={"error": f"Tool '{tool_name}' not found"})


@router.put("/manage/{tool_name}/trust")
async def set_tool_trust(tool_name: str, body: dict):
    """Set the trust level for a custom tool. Body must contain 'trusted' (boolean)."""
    from ..server import get_config, get_trust_registry
    from codeassist.custom_tools_loader import get_custom_tool_registry

    config = get_config()
    workspace = Path(config.server.workspace)
    trust_registry = get_trust_registry()

    custom_registry = get_custom_tool_registry(workspace, trust_registry=trust_registry)
    custom_registry.discover()

    custom_tool = custom_registry.get_tool(tool_name)
    if not custom_tool:
        return JSONResponse(status_code=404, content={"error": "Custom tool not found"})

    trusted = body.get("trusted", False)
    custom_tool.trusted = trusted

    return {"message": f"Tool '{tool_name}' trust level set to {trusted}", "trusted": trusted}


@router.delete("/manage/{tool_name}")
async def delete_custom_tool(tool_name: str):
    """Delete a custom tool file and remove it from the registry."""
    from ..server import get_config, get_trust_registry
    from codeassist.custom_tools_loader import get_custom_tool_registry

    config = get_config()
    workspace = Path(config.server.workspace)
    trust_registry = get_trust_registry()

    custom_registry = get_custom_tool_registry(workspace, trust_registry=trust_registry)
    custom_registry.discover()

    custom_tool = custom_registry.get_tool(tool_name)
    if not custom_tool:
        return JSONResponse(status_code=404, content={"error": "Custom tool not found"})

    source_path = Path(custom_tool.source_path)
    if source_path.exists():
        source_path.unlink()

    custom_registry.reload()

    return {"message": f"Tool '{tool_name}' deleted", "tool_name": tool_name}


@router.post("/manage/scan")
async def scan_custom_tools():
    """Scan all custom tools for potentially dangerous patterns (network, file system, subprocess)."""
    from ..server import get_config, get_trust_registry
    from codeassist.custom_tools_loader import get_custom_tool_registry

    config = get_config()
    workspace = Path(config.server.workspace)
    trust_registry = get_trust_registry()

    custom_registry = get_custom_tool_registry(workspace, trust_registry=trust_registry)
    custom_registry.discover()

    dangerous_patterns = {
        "network_access": r'(requests|urllib|httpx|aiohttp)\.(get|post|put|delete|patch)',
        "file_system": r'(open|os\.remove|os\.unlink|shutil\.rmtree|pathlib.*unlink)',
        "subprocess": r'(subprocess|os\.system|os\.popen|exec|eval)',
        "imports": r'(import\s+os|from\s+os|import\s+subprocess|from\s+subprocess)',
        "env_access": r'(os\.environ|os\.getenv|process\.env)',
    }

    results = []

    for tool in custom_registry.list_tools():
        tool_path = Path(workspace) / ".codeassist" / "custom_tools" / f"{tool['name']}.py"
        if not tool_path.exists():
            continue

        source = tool_path.read_text(encoding="utf-8")
        findings = []

        for pattern_name, pattern in dangerous_patterns.items():
            matches = re.findall(pattern, source)
            if matches:
                findings.append({
                    "pattern": pattern_name,
                    "matches": matches[:5],
                })

        results.append({
            "tool_name": tool["name"],
            "source_path": str(tool_path),
            "findings": findings,
            "risk_level": "high" if len(findings) >= 3 else "medium" if findings else "low",
        })

    risk_order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda x: risk_order.get(x["risk_level"], 3))

    return {"results": results, "total_scanned": len(results)}


# ── Analytics ───────────────────────────────────────────────────

@router.get("/analytics/tools")
async def tool_stats(
    session_id: str = None,
    tool_name: str = None,
    period_days: int = None,
):
    """Get tool usage analytics, optionally filtered by session, tool, or time period."""
    from codeassist.knowledge import KnowledgeBase
    return await KnowledgeBase.get_tool_stats(
        session_id=session_id,
        tool_name=tool_name,
        period_days=period_days,
    )


@router.get("/analytics/llm")
async def llm_stats(
    session_id: str = None,
    model: str = None,
    period_days: int = None,
):
    """Get LLM usage analytics (token counts, costs), optionally filtered."""
    from codeassist.knowledge import KnowledgeBase
    return await KnowledgeBase.get_llm_stats(
        session_id=session_id,
        model=model,
        period_days=period_days,
    )


# ── Trust Management ────────────────────────────────────────────

@router.get("/trust/pending")
async def get_pending_trust_requests():
    """Get all pending trust approval requests for custom tools/plugins."""
    from ..server import get_trust_registry
    
    registry = get_trust_registry()
    if not registry:
        return {"pending": []}
    
    pending = registry.get_pending_requests()
    return {
        "pending": [req.to_dict() for req in pending],
        "count": len(pending),
    }


@router.get("/trust/trusted")
async def get_trusted_tools():
    """Get all tools/plugins that have been explicitly trusted."""
    from ..server import get_trust_registry
    
    registry = get_trust_registry()
    if not registry:
        return {"trusted": []}
    
    trusted = registry.list_trusted()
    return {
        "trusted": trusted,
        "count": len(trusted),
    }
