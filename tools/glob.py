import logging
from pathlib import Path
from tools import Tool, ToolResult
from tools.security import validate_directory, WorkspaceViolationError

try:
    from wcmatch import glob as wcglob
    HAS_WCMATCH = True
except ImportError:
    HAS_WCMATCH = False

log = logging.getLogger(__name__)


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
        search_dir_str = path or str(self.workspace)
        try:
            search_dir = validate_directory(search_dir_str, self.workspace)
        except WorkspaceViolationError as e:
            log.warning("Path validation failed for glob: %s", e)
            return ToolResult(output=f"Error: {e}", error=True)
        except Exception:
            return ToolResult(output=f"Error: directory not found: {search_dir_str}", error=True)

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
