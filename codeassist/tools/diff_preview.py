"""Diff Preview Tool - Show unified diff of planned changes before applying."""

import difflib
import logging
from pathlib import Path

from . import Tool, ToolResult

log = logging.getLogger(__name__)


class DiffPreviewTool(Tool):
    name = "diff_preview"
    description = (
        "Show a unified diff of planned changes before applying them. "
        "Compare two files, compare a file against new content, or preview "
        "a string replacement. Useful for verifying edits before committing."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the existing file to compare against",
            },
            "new_content": {
                "type": "string",
                "description": "New content to compare (full file or replacement text)",
            },
            "old_string": {
                "type": "string",
                "description": "String to find in the file (for replacement preview)",
            },
            "new_string": {
                "type": "string",
                "description": "String to replace with (for replacement preview)",
            },
            "context_lines": {
                "type": "integer",
                "description": "Number of context lines around changes (default: 3)",
            },
        },
        "required": ["file_path"],
    }

    async def execute(self, file_path: str, new_content: str | None = None,
                      old_string: str | None = None, new_string: str | None = None,
                      context_lines: int = 3) -> ToolResult:
        try:
            from .security import validate_path
            path = Path(file_path).resolve()
            validate_path(path)

            if not path.exists():
                return ToolResult(output=f"Error: File '{file_path}' does not exist", error=True)

            old_text = path.read_text(encoding="utf-8")
            old_lines = old_text.splitlines(keepends=True)

            if old_string and new_string is not None:
                if old_string not in old_text:
                    return ToolResult(
                        output=f"Error: old_string not found in {file_path}\n\n"
                               f"Hint: Use the 'edit' tool's stale-edit detection to find similar content.",
                        error=True,
                    )
                new_text = old_text.replace(old_string, new_string, 1)
            elif new_content is not None:
                new_text = new_content
            else:
                return ToolResult(output="Error: provide either new_content or old_string+new_string", error=True)

            new_lines = new_text.splitlines(keepends=True)

            diff = difflib.unified_diff(
                old_lines, new_lines,
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
                n=context_lines,
            )

            diff_text = "".join(diff)
            if not diff_text:
                return ToolResult(output="No changes — files are identical.")

            stats = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
            del_stats = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))

            header = f"Diff: {path.name} ({stats} addition{'s' if stats != 1 else ''}, {del_stats} deletion{'s' if del_stats != 1 else ''})\n"
            return ToolResult(output=header + "\n```diff\n" + diff_text + "```")

        except Exception as e:
            log.exception("diff_preview failed")
            return ToolResult(output=f"Error: {e}", error=True)
