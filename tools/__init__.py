import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class ToolResult:
    output: str
    error: bool = False


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


class Tool(ABC):
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
            return await tool.execute(**arguments)
        except Exception as e:
            log.exception("Tool '%s' failed", name)
            return ToolResult(output=f"Error executing {name}: {e}", error=True)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def clear(self):
        """Remove all registered tools."""
        self._tools.clear()


def create_registry(workspace: Path, tool_config=None, mcp_client=None, skill_registry=None, plugin_registry=None, lsp_client=None) -> ToolRegistry:
    """Create a tool registry with all tools registered."""
    from tools.read import ReadTool
    from tools.write import WriteTool
    from tools.edit import EditTool
    from tools.shell import ShellTool
    from tools.glob import GlobTool
    from tools.grep import GrepTool
    from tools.webfetch import WebFetchTool
    from tools.todo import TodoTool
    from tools.git import GitTool
    from tools.fossil import FossilTool
    from tools.apply_patch import ApplyPatchTool
    from tools.directory import DirectoryTool
    from tools.process import ProcessTool
    from tools.http import HTTPTool
    from tools.database import DatabaseTool
    from tools.documentation import DocumentationTool
    from tools.tool_manager import ToolManagerTool
    from tools.advanced import WebSearchTool, QuestionTool
    from tools.revert import RevertTool
    from tools.create_tool import CreateTool
    from tools.create_skill import CreateSkill
    from tools.diff_preview import DiffPreviewTool
    from tools.test_runner import TestRunnerTool
    from tools.symbol_search import SymbolSearchTool
    from tools.package_manager import PackageManagerTool
    from tools.git_snapshot import GitSnapshotTool
    from tools.docker_tool import DockerTool
    from tools.image_analyze import ImageAnalyzeTool
    from codeassist.skills import SkillTool
    from codeassist.session_manager import SessionTool

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
    registry.register(CreateTool())
    registry.register(CreateSkill())

    # Register Diff Preview tool
    registry.register(DiffPreviewTool())

    # Register Test Runner tool
    registry.register(TestRunnerTool())

    # Register Symbol Search tool
    registry.register(SymbolSearchTool())

    # Register Package Manager tool
    registry.register(PackageManagerTool())

    # Register Git Snapshot tool
    registry.register(GitSnapshotTool())

    # Register Docker tool
    registry.register(DockerTool())

    # Register Image Analyze tool
    registry.register(ImageAnalyzeTool())

    # Register Skill tool (if skill registry exists)
    if skill_registry:
        registry.register(SkillTool(skill_registry))

    # Register Session tool
    registry.register(SessionTool(current_session_id=""))  # Will be updated per-session

    # Register Revert tool
    registry.register(RevertTool())

    # Register LSP tool (if LSP client exists)
    if lsp_client:
        from codeassist.lsp_client import LSPTool
        registry.register(LSPTool(lsp_client))

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
    """Get a dictionary of all built-in tools."""
    from tools.read import ReadTool
    from tools.write import WriteTool
    from tools.edit import EditTool
    from tools.shell import ShellTool
    from tools.glob import GlobTool
    from tools.grep import GrepTool
    from tools.webfetch import WebFetchTool
    from tools.todo import TodoTool
    from tools.git import GitTool
    from tools.fossil import FossilTool
    from tools.apply_patch import ApplyPatchTool
    from tools.directory import DirectoryTool
    from tools.process import ProcessTool
    from tools.http import HTTPTool
    from tools.database import DatabaseTool
    from tools.documentation import DocumentationTool
    from tools.advanced import WebSearchTool, QuestionTool
    from tools.create_tool import CreateTool
    from tools.create_skill import CreateSkill
    from tools.diff_preview import DiffPreviewTool
    from tools.test_runner import TestRunnerTool
    from tools.symbol_search import SymbolSearchTool
    from tools.package_manager import PackageManagerTool
    from tools.git_snapshot import GitSnapshotTool
    from tools.docker_tool import DockerTool
    from tools.image_analyze import ImageAnalyzeTool
    
    tools = {}
    workspace = Path(".")
    
    # Create tool instances
    tool_classes = [
        ReadTool, WriteTool, EditTool, ShellTool, GlobTool, GrepTool,
        WebFetchTool, TodoTool, GitTool, FossilTool, ApplyPatchTool,
        DirectoryTool, ProcessTool, HTTPTool, DatabaseTool, DocumentationTool,
        WebSearchTool, QuestionTool, CreateTool, CreateSkill, DiffPreviewTool,
        TestRunnerTool, SymbolSearchTool, PackageManagerTool,
        GitSnapshotTool, DockerTool, ImageAnalyzeTool,
    ]
    
    for tool_cls in tool_classes:
        tool = tool_cls()
        tool.workspace = workspace
        if config and hasattr(tool, "timeout"):
            tool.timeout = config.tools.shell_timeout
        if config and hasattr(tool, "max_output_chars"):
            tool.max_output_chars = config.tools.max_output_chars
        if config and hasattr(tool, "max_chars"):
            tool.max_chars = config.tools.webfetch_max_chars
        tools[tool.name] = tool
    
    return tools


def reload_tools(workspace: Path, registry: ToolRegistry) -> int:
    """Reload all tools from the tools directory. Returns count of loaded tools."""
    from codeassist.dynamic_tools import DynamicToolLoader
    
    loader = DynamicToolLoader(workspace)
    return loader.reload_registry(registry)
