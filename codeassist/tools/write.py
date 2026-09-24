import logging
from pathlib import Path

from . import Tool, ToolResult
from .security import WorkspaceViolationError, validate_path

log = logging.getLogger(__name__)


class WriteTool(Tool):
    name = "write"
    description = "Write content to a file, overwriting if it exists. Creates parent directories. Backs up existing files to .bak before overwriting."
    workspace = Path(".")

    parameters = {  # noqa: RUF012
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
            if path.exists():
                backup_path = path.with_suffix(path.suffix + ".bak")
                backup_path.write_text(path.read_text(errors="replace"))
            path.write_text(content)
            lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return ToolResult(output=f"Wrote {lines} lines to {file_path}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(output=f"Error writing {file_path}: {e}", error=True)
