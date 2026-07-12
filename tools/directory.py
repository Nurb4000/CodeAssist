import asyncio
import logging
import os
from pathlib import Path

from tools import Tool, ToolResult
from tools.security import validate_directory, WorkspaceViolationError

log = logging.getLogger(__name__)


class DirectoryTool(Tool):
    name = "directory"
    description = (
        "List directory contents with metadata. Shows files and subdirectories "
        "with their sizes, modification times, and types. Use this to explore "
        "project structure or verify file operations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to list (defaults to workspace)",
            },
            "recursive": {
                "type": "boolean",
                "description": "List contents recursively",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum depth for recursive listing (0 = unlimited)",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include hidden files and directories (starting with .)",
            },
            "sort_by": {
                "type": "string",
                "enum": ["name", "size", "modified", "type"],
                "description": "Sort order for results",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of entries to return",
            },
        },
        "required": [],
    }

    def __init__(self):
        self.workspace = Path.cwd()

    async def execute(self, path: str | None = None, recursive: bool = False,
                     max_depth: int = 0, include_hidden: bool = False,
                     sort_by: str = "name", limit: int | None = None) -> ToolResult:
        try:
            dir_path = Path(path or str(self.workspace)).resolve()
            validate_directory(dir_path, self.workspace)

            entries = await self._list_directory(dir_path, recursive, max_depth, include_hidden, sort_by)

            if limit and len(entries) > limit:
                entries = entries[:limit]

            if not entries:
                return ToolResult(output=f"Directory is empty: {dir_path}")

            output = self._format_entries(entries, dir_path)
            return ToolResult(output=output)

        except WorkspaceViolationError as e:
            return ToolResult(output=f"Error: {e}", error=True)
        except Exception as e:
            log.exception("Directory listing failed")
            return ToolResult(output=f"Directory listing error: {e}", error=True)

    async def _list_directory(self, dir_path: Path, recursive: bool, max_depth: int,
                             include_hidden: bool, sort_by: str) -> list[dict]:
        """List directory contents asynchronously."""
        entries = []

        try:
            for entry in await asyncio.to_thread(os.scandir, str(dir_path)):
                try:
                    stat = await asyncio.to_thread(entry.stat)
                    
                    # Skip hidden files unless requested
                    if not include_hidden and entry.name.startswith('.'):
                        continue

                    entry_data = {
                        "name": entry.name,
                        "path": entry.path,
                        "is_dir": entry.is_dir(),
                        "size": stat.st_size if not entry.is_dir() else 0,
                        "modified": stat.st_mtime,
                        "type": "directory" if entry.is_dir() else "file",
                    }

                    entries.append(entry_data)

                    # Recurse into subdirectories if requested
                    if recursive and entry.is_dir():
                        if max_depth == 0 or max_depth > 1:
                            new_max_depth = max_depth - 1 if max_depth > 0 else 0
                            sub_entries = await self._list_directory(
                                Path(entry.path), True, new_max_depth, include_hidden, sort_by
                            )
                            entries.extend(sub_entries)

                except (PermissionError, OSError):
                    continue

        except (PermissionError, OSError) as e:
            raise WorkspaceViolationError(f"Cannot access directory: {e}")

        # Sort entries
        if sort_by == "name":
            entries.sort(key=lambda x: x["name"].lower())
        elif sort_by == "size":
            entries.sort(key=lambda x: x["size"], reverse=True)
        elif sort_by == "modified":
            entries.sort(key=lambda x: x["modified"], reverse=True)
        elif sort_by == "type":
            entries.sort(key=lambda x: (x["is_dir"], x["name"].lower()))

        return entries

    def _format_entries(self, entries: list[dict], base_path: Path) -> str:
        """Format directory entries for display."""
        lines = [f"**Directory:** {base_path}\n"]
        
        directories = [e for e in entries if e["is_dir"]]
        files = [e for e in entries if not e["is_dir"]]

        if directories:
            lines.append("\n**Directories:**")
            for entry in directories:
                lines.append(f"  📁 {entry['name']}/")

        if files:
            lines.append("\n**Files:**")
            for entry in files:
                size_str = self._format_size(entry["size"])
                lines.append(f"  📄 {entry['name']} ({size_str})")

        return "\n".join(lines)

    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
