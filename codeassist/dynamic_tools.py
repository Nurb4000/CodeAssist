"""Dynamic tool loading system for CodeAssist.

Allows the agent to create new tools at runtime without server restart.
Tools are loaded from the tools/ directory and can be reloaded on demand.
"""

import importlib
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from .tools import Tool, ToolRegistry
from .trust_registry import TrustRegistry, TrustStatus

log = logging.getLogger(__name__)


class DynamicToolLoader:
    """Loads and manages tools dynamically from the tools directory."""

    def __init__(self, workspace: Path, trust_registry: TrustRegistry | None = None):
        self.workspace = workspace
        self.tools_dir = workspace / "tools"
        self.trust_registry = trust_registry
        self._loaded_modules: dict[str, Any] = {}
        self._tool_classes: dict[str, type[Tool]] = {}

    def discover_tools(self) -> list[type[Tool]]:
        """Discover all Tool subclasses in the tools directory."""
        discovered = []

        if not self.tools_dir.exists():
            log.warning("Tools directory not found: %s", self.tools_dir)
            return discovered

        # Scan for Python files in tools/
        for item in self.tools_dir.iterdir():
            if item.suffix == '.py' and item.name != '__init__.py' and not item.name.startswith('_'):
                module_name = item.stem
                
                # Check trust before loading
                if self.trust_registry:
                    trust_status = self.trust_registry.check_trust(item, file_type="dynamic_tool")
                    if trust_status == TrustStatus.UNTRUSTED:
                        log.warning("Dynamic tool '%s' is not trusted, skipping", item.name)
                        continue
                    elif trust_status == TrustStatus.PENDING_APPROVAL:
                        log.info("Dynamic tool '%s' pending approval, skipping load", item.name)
                        continue
                
                try:
                    # Import the module. User tool files may still do
                    # `from tools import Tool`, so expose the shipped package
                    # under the legacy top-level name for the duration of exec.
                    _prev_tools = sys.modules.get("tools")
                    if _prev_tools is None:
                        sys.modules["tools"] = importlib.import_module(
                            "codeassist.tools"
                        )
                    spec = importlib.util.spec_from_file_location(
                        f"codeassist.tools.userdynamic.{module_name}",
                        str(item)
                    )
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[spec.name] = module
                        try:
                            spec.loader.exec_module(module)
                        finally:
                            sys.modules.pop(spec.name, None)
                            if _prev_tools is None:
                                sys.modules.pop("tools", None)
                        
                        # Find all Tool subclasses in the module
                        for name, obj in inspect.getmembers(module, inspect.isclass):
                            if (issubclass(obj, Tool) and 
                                obj is not Tool and 
                                obj.__module__ == module.__name__):
                                
                                self._tool_classes[name] = obj
                                discovered.append(obj)
                                log.info("Discovered tool class: %s.%s", module_name, name)
                
                except Exception as e:  # noqa: BLE001
                    log.error("Failed to load tool from %s: %s", item, e)

        return discovered

    def create_tool_instance(self, tool_class: type[Tool], workspace: Path) -> Tool:
        """Create an instance of a tool class."""
        try:
            tool = tool_class()
            if hasattr(tool, 'workspace'):
                tool.workspace = workspace
            return tool
        except Exception as e:  # noqa: BLE001
            log.error("Failed to create tool instance %s: %s", tool_class.__name__, e)
            return None

    def reload_registry(self, registry: ToolRegistry) -> int:
        """Reload all tools into the registry. Returns count of loaded tools."""
        # Clear existing tools
        registry.clear()
        
        # Discover and load all tools
        tool_classes = self.discover_tools()
        loaded_count = 0
        
        for tool_cls in tool_classes:
            tool = self.create_tool_instance(tool_cls, self.workspace)
            if tool:
                registry.register(tool)
                loaded_count += 1
        
        log.info("Reloaded %d tools into registry", loaded_count)
        return loaded_count

    def get_available_tools(self) -> list[dict]:
        """Get list of available tool classes."""
        return [
            {
                "name": name,
                "class": cls.__name__,
                "module": cls.__module__,
                "description": cls.description if hasattr(cls, 'description') else "",
            }
            for name, cls in self._tool_classes.items()
        ]


def create_dynamic_registry(workspace: Path, trust_registry: TrustRegistry | None = None) -> tuple[ToolRegistry, DynamicToolLoader]:
    """Create a tool registry with dynamic loading capability."""
    loader = DynamicToolLoader(workspace, trust_registry=trust_registry)
    registry = ToolRegistry(workspace)
    
    # Load all discovered tools
    loader.reload_registry(registry)
    
    return registry, loader
