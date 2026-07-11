from pathlib import Path
from tools import Tool, ToolResult


class ReadTool(Tool):
    name = "read"
    description = "Read a file's contents. Returns numbered lines. Use offset/limit for large files."
    workspace = Path(".")

    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "offset": {"type": "integer", "description": "Line number to start from (0-indexed)"},
            "limit": {"type": "integer", "description": "Maximum number of lines to return"},
        },
        "required": ["file_path"],
    }

    async def execute(self, file_path: str, offset: int = 0, limit: int = 2000) -> ToolResult:
        path = Path(file_path)
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {file_path}", error=True)
        if path.is_dir():
            return ToolResult(output=f"Error: {file_path} is a directory, not a file", error=True)

        try:
            content = path.read_text(errors="replace")
        except Exception as e:
            return ToolResult(output=f"Error reading {file_path}: {e}", error=True)

        lines = content.splitlines()
        total = len(lines)
        sliced = lines[offset : offset + limit]

        numbered = []
        for i, line in enumerate(sliced, start=offset + 1):
            numbered.append(f"{i}: {line}")

        result = "\n".join(numbered)
        if offset > 0 or offset + limit < total:
            result += f"\n\n(Showing lines {offset + 1}-{min(offset + limit, total)} of {total})"
        else:
            result += f"\n\n({total} lines)"

        return ToolResult(output=result)
