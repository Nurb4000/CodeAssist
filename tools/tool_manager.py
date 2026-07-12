import logging
from typing import Any

from tools import Tool, ToolResult

log = logging.getLogger(__name__)


class ToolManagerTool(Tool):
    name = "tool_manager"
    description = (
        "Manage tools: list available tools or reload tools from disk. "
        "Use 'list' to see all available tools, or 'reload' to reload tools "
        "after creating new ones. This tool allows the agent to discover and "
        "manage its own capabilities."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "reload"],
                "description": "Tool management action to perform",
            },
        },
        "required": ["action"],
    }

    async def execute(self, action: str) -> ToolResult:
        if action == "list":
            return await self._list_tools()
        elif action == "reload":
            return await self._reload_tools()
        else:
            return ToolResult(output=f"Error: unknown action '{action}'", error=True)

    async def _list_tools(self) -> ToolResult:
        """List all available tools."""
        import server as server_mod

        if server_mod.tools is None:
            cfg = server_mod.get_config()
            server_mod._init_subsystems(cfg)

        if server_mod.tools is None:
            return ToolResult(output="Tools not initialized. Server may still be starting up.")

        tool_list = server_mod.tools.list_names()

        if not tool_list:
            return ToolResult(output="No tools available")
        
        lines = ["**Available Tools:**\n"]
        for name in sorted(tool_list):
            tool = server_mod.tools.get(name)
            if tool and hasattr(tool, 'description'):
                desc = tool.description[:80] + "..." if len(tool.description) > 80 else tool.description
                lines.append(f"- `{name}`: {desc}")
        
        return ToolResult(output="\n".join(lines))

    async def _reload_tools(self) -> ToolResult:
        """Reload tools from disk."""
        try:
            from server import reload_all_tools
            
            reload_all_tools()
            return ToolResult(output="Tools reloaded successfully. New tools are now available.")
        except Exception as e:
            log.exception("Failed to reload tools")
            return ToolResult(output=f"Error reloading tools: {e}", error=True)
