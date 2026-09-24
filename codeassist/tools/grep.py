import logging
import re
import shutil
import subprocess
from pathlib import Path

from . import Tool, ToolResult
from .security import WorkspaceViolationError, validate_directory

log = logging.getLogger(__name__)


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents using regex. Uses ripgrep if available, otherwise Python regex."
    workspace = Path(".")

    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory to search in (defaults to workspace)"},
            "include": {"type": "string", "description": "File pattern to include (e.g. '*.py')"},
            "exclude": {"type": "string", "description": "File pattern to exclude (e.g. '*.log', 'node_modules')"},
            "context": {"type": "integer", "description": "Number of context lines to show before and after each match", "default": 0},
        },
        "required": ["pattern"],
    }

    async def execute(
        self,
        pattern: str,
        path: str | None = None,
        include: str | None = None,
        exclude: str | None = None,
        context: int = 0,
    ) -> ToolResult:
        search_dir_str = path or str(self.workspace)
        try:
            search_dir = validate_directory(search_dir_str, self.workspace)
        except WorkspaceViolationError as e:
            log.warning("Path validation failed for grep: %s", e)
            return ToolResult(output=f"Error: {e}", error=True)
        except Exception:  # noqa: BLE001
            return ToolResult(output=f"Error: directory not found: {search_dir_str}", error=True)

        if shutil.which("rg"):
            return self._ripgrep(pattern, search_dir, include, exclude, context)

        return self._python_grep(pattern, search_dir, include, exclude, context)

    def _ripgrep(self, pattern: str, directory: Path, include: str | None, exclude: str | None, context: int) -> ToolResult:
        cmd = ["rg", "-n", "--max-count", "1000"]
        if context > 0:
            cmd.extend(["-C", str(context)])
        cmd.extend([pattern, str(directory)])
        if include:
            cmd.extend(["-g", include])
        if exclude:
            cmd.extend(["-g", f"!{exclude}"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired:
            return ToolResult(output="Error: search timed out", error=True)

        output = result.stdout.strip()
        if not output:
            return ToolResult(output=f"No matches found for pattern '{pattern}'")

        lines = output.split("\n")
        if len(lines) > 200:
            output = "\n".join(lines[:200]) + f"\n\n(200 of {len(lines)} lines shown)"

        return ToolResult(output=output)

    def _python_grep(self, pattern: str, directory: Path, include: str | None, exclude: str | None, context: int) -> ToolResult:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(output=f"Error: invalid regex: {e}", error=True)

        matches = []
        search_pattern = include or "*.*"

        for path_obj in directory.rglob(search_pattern):
            if not path_obj.is_file():
                continue
            if exclude:
                try:
                    if path_obj.match(exclude):
                        continue
                except Exception:  # noqa: BLE001, S110
                    pass
                try:
                    text = path_obj.read_text(errors="replace")
                except OSError:
                    continue

            lines = text.splitlines()
            for i, line in enumerate(lines, 1):
                if regex.search(line):
                    start = max(0, i - 1 - context)
                    end = min(len(lines), i + context)
                    for ctx_i in range(start, end):
                        prefix = ">" if ctx_i == i - 1 else " "
                        matches.append(f"{path_obj}:{ctx_i + 1}: {prefix} {lines[ctx_i].rstrip()}")
                    if len(matches) >= 200:
                        break
            if len(matches) >= 200:
                break

        if not matches:
            return ToolResult(output=f"No matches found for pattern '{pattern}'")

        return ToolResult(output="\n".join(matches))
