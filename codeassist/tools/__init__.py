import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class ToolResult:
    output: str
    error: bool = False


def live_config():
    """The live server Config, for the few tools that need LLM/server settings.

    Prefer ``self.workspace`` (injected by the registry) for anything touching
    the user's files. Reach for this only for settings a tool cannot be handed
    at construction time. Unlike ``config.load_config()`` it also sees settings
    changed at runtime via the settings API, and it resolves the workspace the
    same way the server did.
    """
    from codeassist.server import get_config  # local: server imports this module

    return get_config()


def _validate_args(schema: dict, args: dict) -> str | None:
    """Validate tool arguments against JSON Schema. Returns error message or None."""
    props = schema.get("properties", {})

    # Check required fields
    for field_name in schema.get("required", []):
        if field_name not in args or args[field_name] is None:
            return f"Missing required argument '{field_name}'"

    # Check types and enum values
    for field_name, field_schema in props.items():
        if field_name not in args:
            continue
        value = args[field_name]
        expected = field_schema.get("type", "string")

        if expected == "string" and not isinstance(value, str):
            return f"Argument '{field_name}' must be a string, got {type(value).__name__}"
        if expected == "integer" and not isinstance(value, int):
            return f"Argument '{field_name}' must be an integer, got {type(value).__name__}"
        if expected == "boolean" and not isinstance(value, bool):
            return f"Argument '{field_name}' must be a boolean, got {type(value).__name__}"
        if expected == "number" and not isinstance(value, (int, float)):
            return f"Argument '{field_name}' must be a number, got {type(value).__name__}"
        if expected == "array" and not isinstance(value, list):
            return f"Argument '{field_name}' must be an array, got {type(value).__name__}"
        if expected == "object" and not isinstance(value, dict):
            return f"Argument '{field_name}' must be an object, got {type(value).__name__}"

        enum_vals = field_schema.get("enum")
        if enum_vals is not None and value not in enum_vals:
            return f"Argument '{field_name}' must be one of {enum_vals}, got '{value}'"

    return None


def tool_error(message: str, exc: Exception | None = None) -> ToolResult:
    """Create a ToolResult for a known error condition.

    Use this for errors the tool expects and can describe meaningfully
    (e.g. "file not found", "invalid argument"). Unexpected exceptions should
    bubble up to the registry's catch-all, which logs the full traceback and
    returns a generic error result. This avoids double-swallowing: the agent
    loop has its own broad catch, so tools should only catch what they know
    how to handle.
    """
    if exc is not None:
        log.debug("Tool error: %s (caused by %s: %s)", message, type(exc).__name__, exc)
    return ToolResult(output=f"Error: {message}", error=True)


class Tool(ABC):
    """Base class for all tools.

    Every tool must return a ``ToolResult`` from ``execute()``. The registry
    enforces this at runtime with a defensive coercion (str → ToolResult) and
    a warning log, so third-party or dynamically loaded tools that forget the
    return type don't abort the whole turn.

    Error handling convention:
    - Use ``tool_error()`` for known, expected errors (file not found, etc.)
    - Let unexpected exceptions bubble up to the registry's catch-all
    - The agent loop has its own broad catch; avoid double-swallowing
    """

    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        ...

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(output=f"Error: unknown tool '{name}'", error=True)

        # Validate arguments against tool schema before execution
        err = _validate_args(tool.parameters, arguments)
        if err:
            return ToolResult(output=f"Error: {name}: {err}", error=True)

        try:
            result = await tool.execute(**arguments)
        except Exception as e:
            log.exception("Tool '%s' failed", name)
            return ToolResult(output=f"Error executing {name}: {e}", error=True)

        # Callers (notably the agent, which reads `result.output`) assume a
        # ToolResult. Third-party and dynamically loaded tools are not held to
        # the base class's signature, and one that returned a bare str aborted
        # the whole turn with "'str' object has no attribute 'output'". Coerce
        # defensively rather than trusting every registration to conform.
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, str):
            log.warning("Tool '%s' returned str instead of ToolResult; coercing", name)
            return ToolResult(output=result)
        log.warning(
            "Tool '%s' returned %s instead of ToolResult; wrapping in error",
            name, type(result).__name__,
        )
        return ToolResult(
            output=f"Error: tool '{name}' returned {type(result).__name__}, expected a ToolResult",
            error=True,
        )

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def clear(self):
        """Remove all registered tools."""
        self._tools.clear()


def create_registry(workspace: Path, tool_config=None, mcp_client=None, skill_registry=None, plugin_registry=None, lsp_client=None) -> ToolRegistry:
    """Create a tool registry with all tools registered."""
    from codeassist.session_manager import SessionTool
    from codeassist.skills import SkillTool

    from .advanced import QuestionTool, WebSearchTool
    from .apply_patch import ApplyPatchTool
    from .create_skill import CreateSkill
    from .create_tool import CreateTool
    from .database import DatabaseTool
    from .diff_preview import DiffPreviewTool
    from .directory import DirectoryTool
    from .docker_tool import DockerTool
    from .documentation import DocumentationTool
    from .edit import EditTool
    from .fossil import FossilTool
    from .git import GitTool
    from .git_snapshot import GitSnapshotTool
    from .glob import GlobTool
    from .grep import GrepTool
    from .http import HTTPTool
    from .image_analyze import ImageAnalyzeTool
    from .package_manager import PackageManagerTool
    from .process import ProcessTool
    from .read import ReadTool
    from .screenshot import ScreenshotTool
    from .shell import ShellTool
    from .symbol_search import SymbolSearchTool
    from .test_runner import TestRunnerTool
    from .todo import TodoTool
    from .tool_manager import ToolManagerTool
    from .webfetch import WebFetchTool
    from .write import WriteTool

    registry = ToolRegistry(workspace)

    # Register built-in tools
    for tool_cls in [ReadTool, WriteTool, EditTool, ShellTool, GlobTool, GrepTool, WebFetchTool, TodoTool]:
        tool = tool_cls()
        tool.workspace = workspace
        if tool_config and hasattr(tool, "timeout"):
            tool.timeout = tool_config.shell_timeout
        if tool_config and hasattr(tool, "max_output_chars"):
            tool.max_output_chars = tool_config.max_output_chars
        if tool_config and hasattr(tool, "max_chars"):
            tool.max_chars = tool_config.webfetch_max_chars
        registry.register(tool)

    # Register Git tool
    git_tool = GitTool()
    git_tool.workspace = workspace
    registry.register(git_tool)

    # Register Fossil tool
    fossil_tool = FossilTool()
    fossil_tool.workspace = workspace
    registry.register(fossil_tool)

    # Register Apply Patch tool (with LSP client for diagnostics feedback)
    apply_patch_tool = ApplyPatchTool(lsp_client=lsp_client)
    apply_patch_tool.workspace = workspace
    registry.register(apply_patch_tool)

    # Register Directory tool
    directory_tool = DirectoryTool()
    directory_tool.workspace = workspace
    registry.register(directory_tool)

    # Register Process tool
    process_tool = ProcessTool()
    process_tool.workspace = workspace
    registry.register(process_tool)

    # Register HTTP tool
    http_tool = HTTPTool()
    registry.register(http_tool)

    # Register Database tool
    database_tool = DatabaseTool()
    registry.register(database_tool)

    # Register Documentation tool
    doc_tool = DocumentationTool()
    doc_tool.workspace = workspace
    registry.register(doc_tool)

    # Register Tool Manager tool (for dynamic tool management)
    registry.register(ToolManagerTool())

    # Register advanced tools
    websearch_tool = WebSearchTool()
    if tool_config:
        websearch_tool.max_chars = tool_config.websearch_max_chars
        websearch_tool._search_engine = tool_config.websearch_engine
    registry.register(websearch_tool)

    # Register Question tool
    registry.register(QuestionTool())

    # Register Create Tool and Create Skill tools
    create_tool = CreateTool()
    create_tool.workspace = workspace
    registry.register(create_tool)
    create_skill_tool = CreateSkill()
    create_skill_tool.workspace = workspace
    registry.register(create_skill_tool)

    # Every remaining built-in tool below is workspace-scoped: each one reads
    # self.workspace to sandbox its filesystem access. They must be injected
    # with the configured workspace here or they silently fall back to the
    # process CWD and operate on the app tree instead of the user's project.
    for tool_cls in [
        DiffPreviewTool,
        TestRunnerTool,
        SymbolSearchTool,
        PackageManagerTool,
        GitSnapshotTool,
        DockerTool,
        ImageAnalyzeTool,
    ]:
        tool = tool_cls()
        tool.workspace = workspace
        registry.register(tool)

    # Register Screenshot tool
    screenshot_tool = ScreenshotTool()
    screenshot_tool.workspace = workspace
    registry.register(screenshot_tool)

    # Register Skill tool (if skill registry exists)
    if skill_registry:
        registry.register(SkillTool(skill_registry))

    # Register Session tool
    registry.register(SessionTool(current_session_id="", workspace=workspace))  # ID updated per-session

    # Register LSP tool (if LSP client exists)
    if lsp_client:
        from codeassist.lsp_client import LSPTool
        lsp_tool = LSPTool(lsp_client)
        lsp_tool.workspace = workspace
        registry.register(lsp_tool)

    # Register MCP tools (if MCP client exists)
    if mcp_client:
        from codeassist.mcp_client import MCPToolWrapper
        for mcp_tool in mcp_client.get_tools():
            registry.register(MCPToolWrapper(mcp_tool, mcp_client))

    # Register plugin tools
    if plugin_registry:
        for plugin_tool in plugin_registry.get_all_tools():
            registry.register(plugin_tool)

    return registry


def get_tools(config=None) -> dict:
    """Get a dictionary of all built-in tools.

    Thin wrapper around ``create_registry()`` that returns a plain dict for
    callers that don't need the full registry (e.g. the GUI tool list).
    Consolidates with ``create_registry()`` to avoid duplicating the import
    and instantiation logic.
    """
    workspace = Path(config.workspace) if config is not None else Path(".")
    registry = create_registry(workspace, tool_config=getattr(config, "tools", None))
    return dict(registry._tools)


def reload_tools(workspace: Path, registry: ToolRegistry) -> int:
    """Reload all tools from the tools directory. Returns count of loaded tools."""
    from codeassist.dynamic_tools import DynamicToolLoader
    
    loader = DynamicToolLoader(workspace)
    return loader.reload_registry(registry)


# --- export / import (portability) ----------------------------------------- #

BASE_TOOLS_BUNDLE = "codeassist-base-tools-bundle"


def export_base_tools() -> dict:
    """Return the source of every shipped (base) tool module.

    Mirrors the KB export envelope (``version`` / ``exported_at`` / ``data``).
    Base tools are package code, so this is a read-only shareable snapshot;
    re-importing would require registering the tool in this package.
    """
    from datetime import datetime

    payload = {
        "format": BASE_TOOLS_BUNDLE,
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "data": {"base_tools": []},
    }
    for py in sorted(Path(__file__).parent.glob("*.py")):
        if py.name.startswith("_") or py.name == "__init__.py":
            continue
        payload["data"]["base_tools"].append({
            "file": py.name,
            "source": py.read_text(encoding="utf-8"),
        })
    return payload
