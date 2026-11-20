import logging
import re
import shutil
import subprocess
from pathlib import Path

from . import Tool, ToolResult
from .security import WorkspaceViolationError, validate_directory

log = logging.getLogger(__name__)

# Hard cap on files the Python regex fallback will traverse before bailing. The
# ripgrep path bounds results with --max-count; the pure-Python fallback had no
# such ceiling, so searching a large tree (e.g. one with node_modules/) with few
# matches read every file unbounded -- a memory/IO DoS (re-scan M5).
MAX_FILES_SCANNED = 10_000


def _is_excluded(path_obj: Path, directory: Path, exclude: str) -> bool:
    """Whether *path_obj* matches the user's ``exclude`` pattern.

    ``Path.match`` compares only the final component, so a bare directory name
    (``node_modules``) would not exclude the files inside it. Match every parent
    path relative to the search root too, which also covers slash patterns such
    as ``tests/*``.
    """
    try:
        relative = path_obj.relative_to(directory)
    except ValueError:
        relative = path_obj
    parts = relative.parts
    for i in range(1, len(parts) + 1):
        try:
            if Path(*parts[:i]).match(exclude):
                return True
        except Exception:  # noqa: BLE001, S112
            continue
    return False


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
        # Glob filters must come BEFORE `--`: everything after the separator is a
        # positional path, so a trailing `-g`/`!pattern` would be searched for as
        # a file name instead of filtering results (and rg would report errors on
        # stderr that the old code never looked at).
        if include:
            cmd.extend(["-g", include])
        if exclude:
            cmd.extend(["-g", f"!{exclude}"])
        # `--` guards against a pattern that begins with `-` (e.g. "-foo") being
        # parsed as an rg flag rather than a literal search pattern.
        cmd.extend(["--", pattern, str(directory)])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired:
            return ToolResult(output="Error: search timed out", error=True)

        # rg exits 1 for "no matches" and >=2 for real errors (bad regex, bad
        # path, ...). Checking stdout alone used to turn those errors into a
        # misleading "No matches found" with error=False.
        returncode = getattr(result, "returncode", 0)
        if returncode and returncode > 1:
            detail = (getattr(result, "stderr", "") or "").strip()
            return ToolResult(
                output=f"Error: {detail or f'rg exited with code {returncode}'}",
                error=True,
            )

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
        # "*.*" skipped files without a dot (Makefile, LICENSE, ...); "*" also
        # matches those. Directories are filtered out by is_file() below.
        search_pattern = include or "*"
        scanned = 0

        for path_obj in directory.rglob(search_pattern):
            if not path_obj.is_file():
                continue
            scanned += 1
            if scanned > MAX_FILES_SCANNED:
                # Stop traversing before reading the whole tree; surface a clear
                # notice rather than silently returning a partial match set.
                return ToolResult(
                    output=(
                        f"Search stopped after scanning {MAX_FILES_SCANNED} files "
                        f"({len(matches)} match(es) found so far). Narrow the "
                        f"search with include/exclude, or use ripgrep for "
                        f"larger trees."
                    ),
                    error=True,
                )
            if exclude and _is_excluded(path_obj, directory, exclude):
                continue
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
