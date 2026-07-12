import asyncio
import logging
from pathlib import Path

from tools import Tool, ToolResult
from tools.security import validate_path, WorkspaceViolationError

log = logging.getLogger(__name__)


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = (
        "Apply a unified diff patch to one or more files. "
        "Use this to apply pre-generated patches, revert changes, "
        "or apply multi-file updates atomically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "patch_content": {
                "type": "string",
                "description": "Unified diff patch content to apply",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Show what would be changed without applying",
            },
            "reverse": {
                "type": "boolean",
                "description": "Reverse the patch (undo changes)",
            },
        },
        "required": ["patch_content"],
    }

    def __init__(self):
        self.workspace = Path.cwd()

    async def execute(self, patch_content: str, dry_run: bool = False, reverse: bool = False) -> ToolResult:
        try:
            # Validate that patch content is not empty
            if not patch_content or not patch_content.strip():
                return ToolResult(output="Error: patch_content cannot be empty", error=True)

            # Create a temporary patch file
            import tempfile
            import os

            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.patch',
                delete=False,
                dir=str(self.workspace)
            ) as f:
                f.write(patch_content)
                patch_file = f.name

            try:
                # Build git apply command
                args = ["apply"]
                
                if dry_run:
                    args.append("--check")  # git apply doesn't have --dry-run, use --check instead
                
                if reverse:
                    args.append("--reverse")

                args.append(patch_file)

                # Execute git apply
                proc = await asyncio.create_subprocess_exec(
                    "git", *args,
                    cwd=str(self.workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                stdout, stderr = await proc.communicate()
                rc = proc.returncode

                if rc != 0:
                    error_msg = stderr.decode('utf-8', errors='replace')
                    return ToolResult(
                        output=f"Patch application failed:\n{error_msg}",
                        error=True
                    )

                if dry_run:
                    return ToolResult(output="Dry run complete. Patch would apply successfully.")
                
                else:
                    # Show what was changed
                    status_result = await self._get_status()
                    return ToolResult(
                        output=f"Patch applied successfully.\n\nChanged files:\n{status_result}"
                    )

            finally:
                # Clean up temp file
                try:
                    os.unlink(patch_file)
                except OSError:
                    pass

        except WorkspaceViolationError as e:
            return ToolResult(output=f"Error: {e}", error=True)
        except Exception as e:
            log.exception("Apply patch failed")
            return ToolResult(output=f"Patch application error: {e}", error=True)

    async def _get_status(self) -> str:
        """Get git status of changed files."""
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--short",
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode('utf-8', errors='replace').strip()
