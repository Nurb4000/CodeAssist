"""Symbol Search Tool - ctags-based go-to-definition and find-references."""

import logging
import re
import subprocess
from pathlib import Path

from tools import Tool, ToolResult

log = logging.getLogger(__name__)


def _run_ctags(workspace: Path, extra_args: list[str] | None = None) -> list[dict]:
    """Run ctags and parse output."""
    cmd = ["ctags", "-f", "-", "--sort=no", "--fields=+nKsS"] + (extra_args or []) + ["-R", str(workspace)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return []
    except subprocess.TimeoutExpired:
        return []

    symbols = []
    for line in proc.stdout.splitlines():
        if line.startswith("!"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name = parts[0]
        file_path = parts[1]
        address = parts[2]
        kind = ""
        line_no = 0
        for p in parts[3:]:
            if p.startswith("kind:"):
                kind = p[5:]
            elif p.startswith("line:"):
                try:
                    line_no = int(p[5:])
                except ValueError:
                    pass
        symbols.append({
            "name": name,
            "file": file_path,
            "line": line_no,
            "kind": kind,
            "address": address,
        })
    return symbols


def _search_symbols(workspace: Path, query: str, kind_filter: str | None = None) -> list[dict]:
    """Search for symbols matching a query."""
    symbols = _run_ctags(workspace)
    results = []
    query_lower = query.lower()
    for sym in symbols:
        if kind_filter and sym["kind"].lower() != kind_filter.lower():
            continue
        if query_lower in sym["name"].lower():
            results.append(sym)
    return results


class SymbolSearchTool(Tool):
    name = "symbol_search"
    description = (
        "Search for code symbols (functions, classes, variables) using ctags. "
        "Supports go-to-definition (find where a symbol is defined) and "
        "find-references (find where a symbol is used). Requires ctags to be installed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["find_definition", "find_references", "list_symbols"],
                "description": "Action to perform",
            },
            "query": {
                "type": "string",
                "description": "Symbol name to search for",
            },
            "kind": {
                "type": "string",
                "description": "Filter by symbol kind (function, class, variable, etc.)",
            },
        },
        "required": ["action", "query"],
    }

    async def execute(self, action: str, query: str, kind: str | None = None) -> ToolResult:
        try:
            workspace = Path(self.workspace) if hasattr(self, "workspace") else Path(".")

            # Check if ctags is available
            try:
                subprocess.run(["ctags", "--version"], capture_output=True, timeout=5)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return ToolResult(
                    output="Error: ctags is not installed. Install with:\n"
                           "  Ubuntu/Debian: sudo apt install universal-ctags\n"
                           "  macOS: brew install universal-ctags\n"
                           "  Arch: sudo pacman -S ctags",
                    error=True,
                )

            if action == "find_definition":
                symbols = _search_symbols(workspace, query, kind)
                exact = [s for s in symbols if s["name"] == query]
                if not exact:
                    exact = symbols[:5]
                if not exact:
                    return ToolResult(output=f"No symbol '{query}' found.")
                lines = [f"**Definitions of '{query}':**\n"]
                for sym in exact[:10]:
                    lines.append(f"- `{sym['kind']}` in {sym['file']}:{sym['line']}")
                return ToolResult(output="\n".join(lines))

            elif action == "find_references":
                symbols = _search_symbols(workspace, query)
                if not symbols:
                    return ToolResult(output=f"No references to '{query}' found.")
                lines = [f"**References to '{query}' ({len(symbols)} found):**\n"]
                for sym in symbols[:20]:
                    lines.append(f"- `{sym['kind']}` {sym['name']} in {sym['file']}:{sym['line']}")
                return ToolResult(output="\n".join(lines))

            elif action == "list_symbols":
                symbols = _search_symbols(workspace, query, kind)
                if not symbols:
                    return ToolResult(output=f"No symbols matching '{query}'.")
                lines = [f"**Symbols matching '{query}' ({len(symbols)} found):**\n"]
                by_file = {}
                for sym in symbols[:50]:
                    by_file.setdefault(sym["file"], []).append(sym)
                for file_name, syms in by_file.items():
                    lines.append(f"\n`{file_name}`:")
                    for sym in syms:
                        lines.append(f"  - `{sym['kind']}` {sym['name']} (line {sym['line']})")
                return ToolResult(output="\n".join(lines))

            else:
                return ToolResult(output=f"Error: unknown action '{action}'", error=True)

        except Exception as e:
            log.exception("symbol_search failed")
            return ToolResult(output=f"Error: {e}", error=True)
