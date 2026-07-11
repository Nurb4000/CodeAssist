from pathlib import Path
from tools import Tool, ToolResult

try:
    from wcmatch import glob as wcglob
    HAS_WCMATCH = True
except ImportError:
    HAS_WCMATCH = False


class GlobTool(Tool):
    name = "glob"
    description = "Find files matching a glob pattern. Returns matching file paths."
    workspace = Path(".")

    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.py', 'src/**/*.ts')"},
            "path": {"type": "string", "description": "Directory to search in (defaults to workspace)"},
        },
        "required": ["pattern"],
    }

    async def execute(self, pattern: str, path: str | None = None) -> ToolResult:
        search_dir = Path(path) if path else self.workspace
        if not search_dir.exists():
            return ToolResult(output=f"Error: directory not found: {search_dir}", error=True)

        try:
            if HAS_WCMATCH:
                matches = sorted(
                    str(p) for p in wcglob.glob(pattern, root_dir=search_dir, flags=wcglob.GLOBSTAR)
                )
            else:
                matches = sorted(str(p) for p in search_dir.rglob(pattern))
        except Exception as e:
            return ToolResult(output=f"Error with glob pattern: {e}", error=True)

        if not matches:
            return ToolResult(output=f"No files found matching '{pattern}' in {search_dir}")

        if len(matches) > 200:
            output = "\n".join(matches[:200])
            output += f"\n\n(200 of {len(matches)} results shown)"
        else:
            output = "\n".join(matches)

        return ToolResult(output=output)
