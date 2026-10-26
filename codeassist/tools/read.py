import itertools
import logging
from pathlib import Path

from . import Tool, ToolResult
from .security import WorkspaceViolationError, validate_path

log = logging.getLogger(__name__)


class ReadTool(Tool):
    name = "read"
    description = "Read a file's contents. Returns numbered lines. Use offset/limit for large files."
    workspace = Path(".")

    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "offset": {"type": "integer", "description": "Line number to start from (0-indexed)"},
            "limit": {"type": "integer", "description": "Maximum number of lines to return"},
        },
        "required": ["file_path"],
    }

    async def execute(self, file_path: str, offset: int = 0, limit: int = 2000) -> ToolResult:
        try:
            path = validate_path(file_path, self.workspace)
        except WorkspaceViolationError as e:
            log.warning("Path validation failed for read: %s", e)
            return ToolResult(output=f"Error: {e}", error=True)

        if offset < 0 or limit < 0:
            return ToolResult(
                output="Error: offset and limit must be non-negative", error=True
            )
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {file_path}", error=True)
        if path.is_dir():
            return ToolResult(output=f"Error: {file_path} is a directory, not a file", error=True)

        try:
            # Read only the requested line window instead of loading the whole
            # file: read_text() pulled every line into memory up front, so slicing
            # a small range out of a large file blew the heap. islice keeps memory
            # to O(limit); the total is counted in its own streaming pass.
            with path.open("r", errors="replace") as f:
                sliced = [
                    ln.rstrip("\n")
                    for ln in itertools.islice(f, offset, offset + limit)
                ]
            with path.open("r", errors="replace") as f:
                total = sum(1 for _ in f)
        except Exception as e:  # noqa: BLE001
            return ToolResult(output=f"Error reading {file_path}: {e}", error=True)

        numbered = []
        for i, line in enumerate(sliced, start=offset + 1):
            numbered.append(f"{i}: {line}")

        result = "\n".join(numbered)
        if offset > 0 or offset + limit < total:
            result += f"\n\n(Showing lines {offset + 1}-{min(offset + limit, total)} of {total})"
        else:
            result += f"\n\n({total} lines)"

        return ToolResult(output=result)
