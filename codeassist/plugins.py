import importlib
import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from .trust_registry import TrustRegistry, TrustStatus

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

    def __init__(self, workspace: Path, config=None, trust_registry: Optional[TrustRegistry] = None):
        self.workspace = workspace
        self.config = config
        self.trust_registry = trust_registry
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

            # Check trust before loading
            if self.trust_registry:
                trust_status = self.trust_registry.check_trust(plugin_file, file_type="plugin")
                if trust_status == TrustStatus.UNTRUSTED:
                    log.warning("Plugin '%s' is not trusted, skipping", plugin_name)
                    return
                elif trust_status == TrustStatus.PENDING_APPROVAL:
                    log.info("Plugin '%s' pending approval, skipping load", plugin_name)
                    return

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

    def remove_plugin(self, name: str) -> "Path | None":
        """Delete a plugin's directory from disk (admin-managed removal).

        Returns the removed directory path, or None if the plugin is unknown or
        not backed by a discoverable workspace directory. The source file's
        module ``__file__`` points inside the plugin directory, which we scope
        to the workspace to avoid deleting arbitrary paths.
        """
        import shutil

        plugin = self._plugins.get(name)
        if plugin is None:
            return None
        module = getattr(plugin, "_module", None)
        module_path = getattr(module, "__file__", None) if module else None
        if not module_path:
            return None
        candidate = Path(str(module_path)).parent.resolve()
        root = self.workspace.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if not candidate.is_dir():
            return None
        shutil.rmtree(candidate)
        return candidate

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

    async def fire_hook(self, hook_name: str, *args, **kwargs) -> list[Any]:
        """Fire a lifecycle hook and collect results from all plugins.

        Supported hooks:
        - agent.transform: modify agent configurations
        - tool.definition: modify tool definitions
        - session.compacting: inject context during compaction
        - chat.system.transform: modify system prompt
        """
        results = []
        for hook_fn in self.get_all_hooks(hook_name):
            try:
                if asyncio.iscoroutinefunction(hook_fn):
                    result = await hook_fn(*args, **kwargs)
                else:
                    result = hook_fn(*args, **kwargs)
                if result is not None:
                    results.append(result)
            except Exception as e:
                log.error("Hook %s failed: %s", hook_name, e)
        return results

    def reload(self):
        """Hot-reload all plugins."""
        for plugin_name in list(self._plugins.keys()):
            plugin_path = None
            for dir_name in (self.config.directories if self.config else []):
                candidate = self.workspace / dir_name / plugin_name
                if candidate.exists():
                    plugin_path = candidate
                    break
            if plugin_path:
                del self._plugins[plugin_name]
                self._load_plugin(plugin_path)
        log.info("Reloaded %d plugins", len(self._plugins))


class PluginTool:
    """Base class for plugin-provided tools."""

    name: str = ""
    description: str = ""

    def __init__(self):
        self.parameters: dict = {}

    async def execute(self, **kwargs) -> str:
        raise NotImplementedError
