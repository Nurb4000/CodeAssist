import importlib
import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class Plugin:
    """Represents a loaded plugin."""

    def __init__(self, name: str, version: str | None = None, config: dict | None = None):
        self.name = name
        self.version = version
        self.config = config or {}
        self._module = None
        self._tools = []
        self._hooks = {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "config": self.config,
            "tools": [t.name for t in self._tools],
        }


class PluginRegistry:
    """Discovers and manages plugins from the workspace."""

    def __init__(self, workspace: Path, config=None):
        self.workspace = workspace
        self.config = config
        self._plugins: dict[str, Plugin] = {}

    def discover(self) -> list[Plugin]:
        """Discover plugins from configured directories."""
        if not self.config or not self.config.enabled:
            return []

        directories = self.config.directories
        for dir_name in directories:
            plugin_dir = self.workspace / dir_name
            if plugin_dir.exists() and plugin_dir.is_dir():
                self._discover_from_directory(plugin_dir)

        return list(self._plugins.values())

    def _discover_from_directory(self, directory: Path):
        """Discover plugins from a directory."""
        for plugin_dir in directory.iterdir():
            if plugin_dir.is_dir() and (plugin_dir / "plugin.py").exists():
                self._load_plugin(plugin_dir)

    def _load_plugin(self, plugin_path: Path):
        """Load a plugin from a directory."""
        try:
            plugin_name = plugin_path.name
            plugin_file = plugin_path / "plugin.py"

            # Check for plugin.json metadata
            version = None
            config = {}
            meta_file = plugin_path / "plugin.json"
            if meta_file.exists():
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    version = meta.get("version")
                    config = meta.get("config", {})

            # Load the plugin module
            spec = importlib.util.spec_from_file_location(
                f"codeassist_plugin_{plugin_name}",
                str(plugin_file),
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Check if module has required interface
                if hasattr(module, "register"):
                    plugin = Plugin(
                        name=plugin_name,
                        version=version,
                        config=config,
                    )
                    plugin._module = module

                    # Call register function to get tools and hooks
                    if callable(module.register):
                        result = module.register(plugin.config)
                        if isinstance(result, dict):
                            plugin._tools = result.get("tools", [])
                            plugin._hooks = result.get("hooks", {})

                    self._plugins[plugin_name] = plugin
                    log.info("Loaded plugin: %s v%s", plugin_name, version or "unknown")
                else:
                    log.warning("Plugin '%s' missing required 'register' function", plugin_name)

        except Exception as e:
            log.error("Failed to load plugin from %s: %s", plugin_path, e)

    def get_plugin(self, name: str) -> Plugin | None:
        """Get a plugin by name."""
        return self._plugins.get(name)

    def list_plugins(self) -> list[dict]:
        """List all loaded plugins."""
        return [plugin.to_dict() for plugin in self._plugins.values()]

    def get_all_tools(self) -> list:
        """Get all tools from all plugins."""
        tools = []
        for plugin in self._plugins.values():
            tools.extend(plugin._tools)
        return tools

    def get_all_hooks(self, hook_name: str) -> list[Callable]:
        """Get all hooks of a specific type from all plugins."""
        hooks = []
        for plugin in self._plugins.values():
            if hook_name in plugin._hooks:
                hooks.extend(plugin._hooks[hook_name])
        return hooks


class PluginTool:
    """Base class for plugin-provided tools."""

    name: str = ""
    description: str = ""

    def __init__(self):
        self.parameters: dict = {}

    async def execute(self, **kwargs) -> str:
        raise NotImplementedError
