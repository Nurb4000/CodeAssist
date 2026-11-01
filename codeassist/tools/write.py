import contextlib
import logging
import os
import stat
import tempfile
from pathlib import Path

from . import Tool, ToolResult
from .security import WorkspaceViolationError, validate_path

log = logging.getLogger(__name__)


class WriteTool(Tool):
    name = "write"
    description = "Write content to a file, overwriting if it exists. Creates parent directories. Backs up existing files to .bak before overwriting."
    workspace = Path(".")

    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "content": {"type": "string", "description": "The content to write"},
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, file_path: str, content: str) -> ToolResult:
        try:
            path = validate_path(file_path, self.workspace)
        except WorkspaceViolationError as e:
            log.warning("Path validation failed for write: %s", e)
            return ToolResult(output=f"Error: {e}", error=True)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            # Preserve the original file's mode (if any) so an executable or
            # otherwise-permissioned file stays that way after the swap.
            existing_mode = path.stat().st_mode if path.exists() else None

            # Write the new content to a temp file in the same directory first,
            # so a failure here (bad input, full disk) leaves the original file
            # untouched.
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
            try:
                with os.fdopen(fd, "w") as tmp:
                    tmp.write(content)
                # Preserve an existing file's permission bits; new files get a
                # conventional 0644 rather than mkstemp's restrictive 0600.
                if existing_mode is not None:
                    os.chmod(tmp_name, stat.S_IMODE(existing_mode))
                else:
                    os.chmod(tmp_name, 0o644)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise

            # Swap the old file into <name>.bak, then the temp into place. Both
            # are single rename() syscalls, so the window where the path is
            # absent is microscopic and either side is always a complete file --
            # no half-written target or backup (the data-loss race this replaces).
            if path.exists():
                # Collapse a trailing dot so `foo.` -> `foo.bak`, not `foo..bak`.
                backup_path = path.with_name(path.name.rstrip(".") + ".bak")
                os.replace(path, backup_path)
            os.replace(tmp_name, path)

            lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return ToolResult(output=f"Wrote {lines} lines to {file_path}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(output=f"Error writing {file_path}: {e}", error=True)
