"""
Custom Tools Loader - Dynamic loading and management of custom tools.
"""

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class CustomTool:
    """Represents a dynamically loaded custom tool."""
    
    def __init__(self, name: str, description: str, parameters: dict, 
                 execute_func: Callable, source_path: str, trusted: bool = False):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.execute_func = execute_func
        self.source_path = source_path
        self.trusted = trusted
        self.usage_count = 0
    
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "source": "custom",
            "trusted": self.trusted,
        }


class CustomToolRegistry:
    """Manages dynamic loading of custom tools."""
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.tools_dir = workspace / ".codeassist" / "custom_tools"
        self._tools: dict[str, CustomTool] = {}
        self._modules: dict[str, Any] = {}
    
    def discover(self) -> list[CustomTool]:
        """Discover and load all custom tools."""
        if not self.tools_dir.exists():
            return []
        
        for tool_file in self.tools_dir.glob("*.py"):
            if tool_file.name.startswith("_"):
                continue
            try:
                self._load_tool(tool_file)
            except Exception as e:
                log.error("Failed to load custom tool %s: %s", tool_file, e)
        
        return list(self._tools.values())
    
    def _load_tool(self, tool_path: Path):
        """Load a single custom tool module."""
        module_name = f"custom_tool_{tool_path.stem}"
        
        spec = importlib.util.spec_from_file_location(module_name, tool_path)
        if not spec or not spec.loader:
            return
        
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        
        # Extract TOOLS dict
        if not hasattr(module, "TOOLS"):
            log.warning("Custom tool %s missing TOOLS dict", tool_path)
            return
        
        tools_dict = module.TOOLS
        if not isinstance(tools_dict, dict):
            log.warning("Custom tool %s TOOLS is not a dict", tool_path)
            return
        
        for tool_name, tool_def in tools_dict.items():
            if not isinstance(tool_def, dict):
                continue
            
            # Get execute function
            execute_func = getattr(module, "execute", None)
            if not execute_func:
                log.warning("Custom tool %s missing execute function", tool_path)
                continue
            
            custom_tool = CustomTool(
                name=tool_name,
                description=tool_def.get("description", ""),
                parameters=tool_def.get("parameters", {}),
                execute_func=execute_func,
                source_path=str(tool_path),
            )
            
            self._tools[tool_name] = custom_tool
            self._modules[tool_name] = module
            
            log.debug("Loaded custom tool: %s from %s", tool_name, tool_path)
    
    def get_tool(self, name: str) -> CustomTool | None:
        return self._tools.get(name)
    
    def list_tools(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]
    
    def reload(self):
        """Reload all custom tools."""
        self._tools.clear()
        self._modules.clear()
        self.discover()
    
    async def execute_tool(self, name: str, **kwargs) -> str:
        """Execute a custom tool with confirmation if not trusted."""
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Custom tool '{name}' not found"
        
        # For untrusted tools, we would normally ask for confirmation
        # The confirmation is handled by the server/agent layer
        
        try:
            result = await tool.execute_func(**kwargs)
            tool.usage_count += 1
            return result
        except Exception as e:
            log.exception("Custom tool %s execution failed", name)
            return f"Error executing custom tool: {e}"


# Singleton instance
_custom_tool_registry: CustomToolRegistry | None = None


def get_custom_tool_registry(workspace: Path) -> CustomToolRegistry:
    """Get or create the custom tool registry singleton."""
    global _custom_tool_registry
    if _custom_tool_registry is None:
        _custom_tool_registry = CustomToolRegistry(workspace)
    return _custom_tool_registry
