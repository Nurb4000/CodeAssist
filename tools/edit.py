import logging
from pathlib import Path
from tools import Tool, ToolResult
from tools.security import validate_path, WorkspaceViolationError

log = logging.getLogger(__name__)


class EditTool(Tool):
    name = "edit"
    description = "Replace an exact string in a file. The old_string must be unique or use replaceAll."
    workspace = Path(".")

    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "old_string": {"type": "string", "description": "The exact string to find and replace"},
            "new_string": {"type": "string", "description": "The replacement string"},
            "replaceAll": {"type": "boolean", "description": "Replace all occurrences", "default": False},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    async def execute(self, file_path: str, old_string: str, new_string: str, replaceAll: bool = False) -> ToolResult:
        try:
            path = validate_path(file_path, self.workspace)
        except WorkspaceViolationError as e:
            log.warning("Path validation failed for edit: %s", e)
            return ToolResult(output=f"Error: {e}", error=True)

        if not path.exists():
            return ToolResult(output=f"Error: file not found: {file_path}", error=True)

        try:
            content = path.read_text(errors="replace")
        except Exception as e:
            return ToolResult(output=f"Error reading {file_path}: {e}", error=True)

        count = content.count(old_string)
        if count == 0:
            return ToolResult(
                output=f"Error: old_string not found in {file_path}. Make sure the string matches exactly including whitespace and indentation.",
                error=True,
            )
        if count > 1 and not replaceAll:
            return ToolResult(
                output=f"Error: found {count} matches for old_string in {file_path}. "
                "Provide more surrounding context to identify the correct match, or set replaceAll=true.",
                error=True,
            )

        if replaceAll:
            new_content = content.replace(old_string, new_string)
            replacements = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replacements = 1

        try:
            path.write_text(new_content)
        except Exception as e:
            return ToolResult(output=f"Error writing {file_path}: {e}", error=True)

        return ToolResult(output=f"Replaced {replacements} occurrence(s) in {file_path}")
