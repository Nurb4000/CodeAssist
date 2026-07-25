"""Git Snapshot Tool - Auto-commit before risky operations for safe experimentation."""

import asyncio
import logging
from pathlib import Path

from tools import Tool, ToolResult

log = logging.getLogger(__name__)


class GitSnapshotTool(Tool):
    name = "git_snapshot"
    description = (
        "Create a quick git snapshot (auto-commit) of the current workspace state. "
        "Use this before risky operations (writes, edits, shell commands) to provide "
        "a rollback point. The snapshot commit is marked with 'snapshot:' prefix for "
        "easy identification and can be reverted with 'git revert'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Optional description for the snapshot (default: auto-generated)",
            },
            "include_untracked": {
                "type": "boolean",
                "description": "Include untracked files in the snapshot (default: false)",
            },
        },
        "required": [],
    }

    async def execute(self, message: str | None = None,
                      include_untracked: bool = False) -> ToolResult:
        try:
            workspace = Path(self.workspace) if hasattr(self, "workspace") else Path(".")
            snapshot_msg = message or "snapshot: auto-save before risky operation"

            # Check if we're in a git repo
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--git-dir",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return ToolResult(output="Error: not a git repository", error=True)

            # Check for changes
            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, _ = await proc.communicate()
            status_output = stdout.decode().strip()

            if not status_output:
                return ToolResult(output="No changes to snapshot — working tree is clean.")

            # Stage changes
            stage_cmd = ["git", "add", "-A"] if include_untracked else ["git", "add", "-u"]
            proc = await asyncio.create_subprocess_exec(
                *stage_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            await proc.communicate()

            # Commit
            proc = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", snapshot_msg, "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                return ToolResult(
                    output=f"Error creating snapshot: {stderr.decode().strip()}",
                    error=True,
                )

            # Get the commit hash
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--short", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, _ = await proc.communicate()
            commit_hash = stdout.decode().strip()

            # Count files changed
            files_changed = len(status_output.splitlines())

            return ToolResult(
                output=f"Snapshot created: {commit_hash} ({snapshot_msg})\n"
                       f"Files included: {files_changed}\n\n"
                       f"To revert: git revert {commit_hash}"
            )

        except Exception as e:
            log.exception("git_snapshot failed")
            return ToolResult(output=f"Error: {e}", error=True)
