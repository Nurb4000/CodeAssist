import re
import shutil
import subprocess
from pathlib import Path
from tools import Tool, ToolResult


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents using regex. Uses ripgrep if available, otherwise Python regex."
    workspace = Path(".")

    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory to search in (defaults to workspace)"},
            "include": {"type": "string", "description": "File pattern to include (e.g. '*.py')"},
        },
        "required": ["pattern"],
    }

    async def execute(self, pattern: str, path: str | None = None, include: str | None = None) -> ToolResult:
        search_dir = Path(path) if path else self.workspace
        if not search_dir.exists():
            return ToolResult(output=f"Error: directory not found: {search_dir}", error=True)

        if shutil.which("rg"):
            return self._ripgrep(pattern, search_dir, include)

        return self._python_grep(pattern, search_dir, include)

    def _ripgrep(self, pattern: str, directory: Path, include: str | None) -> ToolResult:
        cmd = ["rg", "-n", "--max-count", "1000", pattern, str(directory)]
        if include:
            cmd.extend(["-g", include])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return ToolResult(output="Error: search timed out", error=True)

        output = result.stdout.strip()
        if not output:
            return ToolResult(output=f"No matches found for pattern '{pattern}'")

        lines = output.split("\n")
        if len(lines) > 200:
            output = "\n".join(lines[:200]) + f"\n\n(200 of {len(lines)} matches shown)"

        return ToolResult(output=output)

    def _python_grep(self, pattern: str, directory: Path, include: str | None) -> ToolResult:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(output=f"Error: invalid regex: {e}", error=True)

        matches = []
        glob_pattern = include or "**/*"

        for path_obj in directory.glob(glob_pattern):
            if not path_obj.is_file():
                continue
            try:
                text = path_obj.read_text(errors="replace")
            except Exception:
                continue

            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{path_obj}:{i}: {line.strip()}")
                    if len(matches) >= 200:
                        break
            if len(matches) >= 200:
                break

        if not matches:
            return ToolResult(output=f"No matches found for pattern '{pattern}'")

        return ToolResult(output="\n".join(matches))
