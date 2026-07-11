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


def create_registry(workspace: Path) -> ToolRegistry:
    from tools.read import ReadTool
    from tools.write import WriteTool
    from tools.edit import EditTool
    from tools.shell import ShellTool
    from tools.glob import GlobTool
    from tools.grep import GrepTool
    from tools.webfetch import WebFetchTool
    from tools.todo import TodoTool

    registry = ToolRegistry(workspace)
    for tool_cls in [ReadTool, WriteTool, EditTool, ShellTool, GlobTool, GrepTool, WebFetchTool, TodoTool]:
        tool = tool_cls()
        tool.workspace = workspace
        registry.register(tool)

    return registry
