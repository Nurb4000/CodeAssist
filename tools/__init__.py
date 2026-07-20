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

    async def execute(self, name: str, arguments: dict) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Error: unknown tool '{name}'"

        try:
            result = await tool.execute(**arguments)
            return result.output
        except Exception as e:
            log.exception("Tool '%s' failed", name)
            return f"Error executing {name}: {e}"

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def clear(self):
        """Remove all registered tools."""
        self._tools.clear()


def create_registry(workspace: Path, tool_config=None, mcp_client=None, skill_registry=None, plugin_registry=None) -> ToolRegistry:
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
    from tools.advanced import WebSearchTool, QuestionTool, TaskTool
    from skills import SkillTool
    from session_manager import SessionTool

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

    # Register Apply Patch tool
    apply_patch_tool = ApplyPatchTool()
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
    registry.register(websearch_tool)

    registry.register(QuestionTool())
    registry.register(TaskTool())

    # Register Skill tool (if skill registry exists)
    if skill_registry:
        registry.register(SkillTool(skill_registry))

    # Register Session tool
    registry.register(SessionTool(current_session_id=""))  # Will be updated per-session

    # Register MCP tools (if MCP client exists)
    if mcp_client:
        from mcp_client import MCPToolWrapper
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
    from tools.advanced import WebSearchTool, QuestionTool, TaskTool
    
    tools = {}
    workspace = Path(".")
    
    # Create tool instances
    tool_classes = [
        ReadTool, WriteTool, EditTool, ShellTool, GlobTool, GrepTool,
        WebFetchTool, TodoTool, GitTool, FossilTool, ApplyPatchTool,
        DirectoryTool, ProcessTool, HTTPTool, DatabaseTool, DocumentationTool,
        WebSearchTool, QuestionTool, TaskTool,
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
    from dynamic_tools import DynamicToolLoader
    
    loader = DynamicToolLoader(workspace)
    return loader.reload_registry(registry)
