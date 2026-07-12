import logging
from pathlib import Path
from tools import Tool, ToolResult
from tools.security import validate_path, WorkspaceViolationError

log = logging.getLogger(__name__)


class WriteTool(Tool):
    name = "write"
    description = "Write content to a file, overwriting if it exists. Creates parent directories."
    workspace = Path(".")

    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "content": {"type": "string", "description": "The content to write"},
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, file_path: str, content: str) -> ToolResult:
        try:
            path = validate_path(file_path, self.workspace)
        except WorkspaceViolationError as e:
            log.warning("Path validation failed for write: %s", e)
            return ToolResult(output=f"Error: {e}", error=True)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return ToolResult(output=f"Wrote {lines} lines to {file_path}")
        except Exception as e:
            return ToolResult(output=f"Error writing {file_path}: {e}", error=True)
