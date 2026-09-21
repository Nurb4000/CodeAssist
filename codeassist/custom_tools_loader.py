"""
Custom Tools Loader - Dynamic loading and management of custom tools.
"""

import ast
import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from .trust_registry import TrustRegistry, TrustStatus

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
    
    def __init__(self, workspace: Path, trust_registry: Optional[TrustRegistry] = None):
        self.workspace = workspace
        self.tools_dir = workspace / "runtime" / "custom_tools"
        self.trust_registry = trust_registry
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
        # Check trust before loading
        if self.trust_registry:
            trust_status = self.trust_registry.check_trust(tool_path, file_type="custom_tool")
            if trust_status == TrustStatus.UNTRUSTED:
                log.warning("Custom tool '%s' is not trusted, skipping", tool_path.name)
                return
            elif trust_status == TrustStatus.PENDING_APPROVAL:
                log.info("Custom tool '%s' pending approval, skipping load", tool_path.name)
                return
        
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

    def _find_tool_file(self, name: str) -> "Path | None":
        """Return the source file defining a custom tool by name, without
        executing it.

        Scans each ``*.py`` file with :mod:`ast` and matches ``name`` against the
        keys of its top-level ``TOOLS`` dict. This lets admins remove tool files
        even while they are still pending trust approval (i.e. not yet loaded).
        """
        if not self.tools_dir.exists():
            return None
        for tool_file in sorted(self.tools_dir.glob("*.py")):
            if tool_file.name.startswith("_"):
                continue
            try:
                tree = ast.parse(tool_file.read_text(encoding="utf-8"),
                                 filename=str(tool_file))
            except SyntaxError:
                continue
            for key in self._tool_names_from_tree(tree):
                if key == name:
                    return tool_file
        return None

    @staticmethod
    def _tool_names_from_tree(tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "TOOLS":
                        value = node.value
                        if isinstance(value, ast.Dict):
                            for k in value.keys:
                                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                    names.add(k.value)
        return names

    def remove_tool(self, name: str) -> "Path | None":
        """Delete a custom tool's source file from the custom_tools directory.

        Returns the removed file path, or None if the tool is unknown or its
        source escapes the managed custom tools directory (path-traversal guard).
        Trust-independent: locates the file via an AST scan so pending/untrusted
        tools can still be removed.
        """
        candidate = self._find_tool_file(name)
        if candidate is None:
            return None
        resolved = candidate.resolve()
        root = self.tools_dir.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        if not resolved.is_file():
            return None
        resolved.unlink()
        # Drop from live in-memory registry too.
        self._tools.pop(name, None)
        self._modules.pop(f"custom_tool_{resolved.stem}", None)
        return resolved
        root = self.tools_dir.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        candidate.unlink()
        # Drop from live in-memory registry too.
        self._tools.pop(name, None)
        self._modules.pop(f"custom_tool_{candidate.stem}", None)
        return candidate
    
    def reload(self):
        """Reload all custom tools."""
        self._tools.clear()
        self._modules.clear()
        self.discover()

    # --- export / import (portability) ------------------------------------- #

    TOOLS_BUNDLE = "codeassist-tools-bundle"

    def export_tools(self) -> dict:
        """Return a portable JSON manifest of every custom tool.

        Each entry preserves the tool's source verbatim so it can be shared and
        re-imported on another instance without modification.
        """
        manifest = {"format": self.TOOLS_BUNDLE, "version": 1, "tools": []}
        if not self.tools_dir.exists():
            return manifest
        for tool_file in sorted(self.tools_dir.glob("*.py")):
            if tool_file.name.startswith("_"):
                continue
            source = tool_file.read_text(encoding="utf-8")
            try:
                names = sorted(self._tool_names_from_tree(ast.parse(source)))
            except SyntaxError:
                names = [tool_file.stem]
            manifest["tools"].append({
                "filename": tool_file.name,
                "source": source,
                "tools": names or [tool_file.stem],
                "category": "custom",
            })
        return manifest

    def import_tools(self, manifest: dict) -> dict:
        """Write custom tools from an :meth:`export_tools` manifest to disk.

        Files are written verbatim into ``runtime/custom_tools`` and the registry
        is reloaded; freshly imported tools re-enter the trust flow as untrusted.
        """
        if manifest.get("format") != self.TOOLS_BUNDLE:
            raise ValueError("not a CodeAssist tool bundle")

        self.tools_dir.mkdir(parents=True, exist_ok=True)
        imported = []
        for entry in manifest.get("tools", []):
            target = self.tools_dir / entry["filename"]
            target.write_text(entry["source"], encoding="utf-8")
            imported.append(target.relative_to(self.workspace).as_posix())

        self.reload()
        return {"imported": imported}
    
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


def get_custom_tool_registry(workspace: Path, trust_registry: Optional[TrustRegistry] = None) -> CustomToolRegistry:
    """Get or create the custom tool registry singleton."""
    global _custom_tool_registry
    if _custom_tool_registry is None:
        _custom_tool_registry = CustomToolRegistry(workspace, trust_registry=trust_registry)
    return _custom_tool_registry
