"""Dedicated git worktree management tool."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from . import Tool, ToolResult
from .security import validate_directory

log = logging.getLogger(__name__)


class WorktreeTool(Tool):
    name = "worktree"
    description = (
        "Manage git worktrees: list existing worktrees, create a new worktree for a "
        "branch, remove a worktree, or prune stale worktree metadata. Use this for "
        "isolated parallel work without disturbing the main working tree."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "remove", "prune"],
                "description": "Worktree action to perform.",
            },
            "path": {
                "type": "string",
                "description": "Target worktree path (required for create/remove).",
            },
            "branch": {
                "type": "string",
                "description": "Branch name for create. Existing branch is checked out; use new_branch to create one.",
            },
            "new_branch": {
                "type": "string",
                "description": "Create and checkout this new branch in the worktree (create action).",
            },
            "base": {
                "type": "string",
                "description": "Optional base ref for new_branch (defaults to HEAD).",
            },
            "force": {
                "type": "boolean",
                "description": "Force removal of a worktree with modifications.",
            },
        },
        "required": ["action"],
    }

    async def _run_git(self, args: list[str], cwd: Path) -> tuple[str, str, int]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode or 0,
        )

    async def execute(self, action: str, **kwargs) -> ToolResult:
        cwd = Path(self.workspace)
        try:
            if action == "list":
                return await self._list(cwd)
            if action == "create":
                return await self._create(cwd, kwargs)
            if action == "remove":
                return await self._remove(cwd, kwargs)
            if action == "prune":
                return await self._prune(cwd)
            return ToolResult(output=f"Unknown action: {action}", error=True)
        except Exception as e:  # noqa: BLE001
            log.exception("Worktree operation failed")
            return ToolResult(output=f"Worktree error: {e}", error=True)

    async def _list(self, cwd: Path) -> ToolResult:
        stdout, stderr, rc = await self._run_git(["worktree", "list", "--porcelain"], cwd)
        if rc != 0:
            return ToolResult(output=f"Failed to list worktrees: {stderr.strip()}", error=True)
        entries = []
        cur: dict[str, str] = {}
        for line in stdout.splitlines():
            if line.startswith("worktree "):
                if cur:
                    entries.append(cur)
                cur = {"path": line[9:].strip()}
            elif line.startswith("HEAD "):
                cur["head"] = line[5:].strip()
            elif line.startswith("branch "):
                cur["branch"] = line[7:].strip().removeprefix("refs/heads/")
            elif line == "detached":
                cur["detached"] = "true"
        if cur:
            entries.append(cur)
        if not entries:
            return ToolResult(output="No worktrees found.")
        lines = ["**Worktrees:**", ""]
        for e in entries:
            lines.append(f"- `{e.get('path', '')}`")
            if e.get("branch"):
                lines.append(f"  - branch: `{e['branch']}`")
            elif e.get("detached") == "true":
                lines.append("  - detached HEAD")
            if e.get("head"):
                lines.append(f"  - head: `{e['head'][:12]}`")
        return ToolResult(output="\n".join(lines))

    async def _create(self, cwd: Path, kwargs: dict) -> ToolResult:
        path = kwargs.get("path")
        branch = kwargs.get("branch")
        new_branch = kwargs.get("new_branch")
        base = kwargs.get("base")
        if not path:
            return ToolResult(output="Error: path is required for create", error=True)
        if not branch and not new_branch:
            return ToolResult(output="Error: branch or new_branch is required for create", error=True)
        wt_path = Path(path)
        if not wt_path.is_absolute():
            wt_path = (cwd / wt_path).resolve()
        else:
            wt_path = wt_path.resolve()
        ws = Path(self.workspace).resolve()
        try:
            wt_path.relative_to(ws)
        except ValueError:
            return ToolResult(output=f"Error: worktree path must be inside workspace: {ws}", error=True)
        wt_path.parent.mkdir(parents=True, exist_ok=True)

        args = ["worktree", "add"]
        if new_branch:
            args.extend(["-b", new_branch, str(wt_path)])
            if base:
                args.append(base)
        else:
            args.extend([str(wt_path), branch])
        stdout, stderr, rc = await self._run_git(args, cwd)
        if rc != 0:
            return ToolResult(output=f"Worktree create failed: {stderr.strip() or stdout.strip()}", error=True)
        ref = new_branch or branch
        return ToolResult(output=f"Created worktree `{wt_path}` for `{ref}`.\n{stdout.strip()}".strip())

    async def _remove(self, cwd: Path, kwargs: dict) -> ToolResult:
        path = kwargs.get("path")
        if not path:
            return ToolResult(output="Error: path is required for remove", error=True)
        args = ["worktree", "remove"]
        if kwargs.get("force"):
            args.append("--force")
        args.append(str(path))
        stdout, stderr, rc = await self._run_git(args, cwd)
        if rc != 0:
            return ToolResult(output=f"Worktree remove failed: {stderr.strip() or stdout.strip()}", error=True)
        return ToolResult(output=f"Removed worktree `{path}`.\n{stdout.strip()}".strip())

    async def _prune(self, cwd: Path) -> ToolResult:
        stdout, stderr, rc = await self._run_git(["worktree", "prune", "-v"], cwd)
        if rc != 0:
            return ToolResult(output=f"Worktree prune failed: {stderr.strip()}", error=True)
        msg = stdout.strip() or stderr.strip() or "Pruned stale worktree metadata."
        return ToolResult(output=msg)
