import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from tools import Tool, ToolResult
from tools.security import validate_directory, validate_path, WorkspaceViolationError

log = logging.getLogger(__name__)


class GitTool(Tool):
    name = "git"
    description = (
        "Perform Git operations: status, diff, log, commit, push, pull, clone, "
        "checkout, branch, fetch, worktree, apply patch. Use 'status' to see current state, "
        "'diff' to see changes, 'log' to view history."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "status", "diff", "log", "commit", "push", "pull", "clone",
                    "checkout", "branch", "fetch", "worktree", "apply_patch",
                    "rebase", "merge", "stash", "reset", "clean", "tag",
                ],
                "description": "Git operation to perform",
            },
            "repo_path": {
                "type": "string",
                "description": "Path to Git repository (defaults to workspace)",
            },
            # Status/Diff parameters
            "path": {
                "type": "string",
                "description": "Specific file or directory to operate on",
            },
            # Log parameters
            "limit": {
                "type": "integer",
                "description": "Number of commits to show (default: 20)",
            },
            "author": {
                "type": "string",
                "description": "Filter by author",
            },
            "since": {
                "type": "string",
                "description": "Show commits since date (e.g., '2024-01-01')",
            },
            # Commit parameters
            "message": {
                "type": "string",
                "description": "Commit message",
            },
            "all": {
                "type": "boolean",
                "description": "Stage all changes before committing",
            },
            "amend": {
                "type": "boolean",
                "description": "Amend last commit",
            },
            # Checkout parameters
            "branch": {
                "type": "string",
                "description": "Branch name to checkout or create",
            },
            "create_branch": {
                "type": "boolean",
                "description": "Create a new branch",
            },
            "start_point": {
                "type": "string",
                "description": "Start point for new branch",
            },
            # Branch parameters
            "list_branches": {
                "type": "boolean",
                "description": "List all branches",
            },
            "delete_branch": {
                "type": "string",
                "description": "Delete a branch",
            },
            # Clone parameters
            "url": {
                "type": "string",
                "description": "Repository URL to clone",
            },
            "target_path": {
                "type": "string",
                "description": "Target directory for clone",
            },
            "depth": {
                "type": "integer",
                "description": "Clone depth (shallow clone)",
            },
            # Worktree parameters
            "worktree_operation": {
                "type": "string",
                "enum": ["add", "list", "remove", "prune"],
                "description": "Worktree operation",
            },
            "worktree_path": {
                "type": "string",
                "description": "Path for new worktree",
            },
            # Apply patch parameters
            "patch_content": {
                "type": "string",
                "description": "Patch content to apply",
            },
            "patch_file": {
                "type": "string",
                "description": "Path to patch file",
            },
            # Rebase/Merge parameters
            "upstream": {
                "type": "string",
                "description": "Upstream branch for rebase/merge",
            },
            "abort": {
                "type": "boolean",
                "description": "Abort an in-progress rebase/merge",
            },
            "rebase_continue": {
                "type": "boolean",
                "description": "Continue an in-progress rebase after resolving conflicts",
            },
            # Stash parameters
            "stash_message": {
                "type": "string",
                "description": "Message for stash",
            },
            "stash_pop": {
                "type": "boolean",
                "description": "Pop latest stash",
            },
            # Reset parameters
            "mode": {
                "type": "string",
                "enum": ["soft", "mixed", "hard"],
                "description": "Reset mode",
            },
            "commit": {
                "type": "string",
                "description": "Commit to reset to",
            },
            # Clean parameters
            "dry_run": {
                "type": "boolean",
                "description": "Show what would be deleted",
            },
            "force": {
                "type": "boolean",
                "description": "Force operation",
            },
            # Tag parameters
            "tag_name": {
                "type": "string",
                "description": "Tag name",
            },
            "tag_message": {
                "type": "string",
                "description": "Tag message (annotated tags)",
            },
        },
        "required": ["operation"],
    }

    def __init__(self):
        self.workspace = Path.cwd()

    async def execute(self, operation: str, **kwargs) -> ToolResult:
        try:
            repo_path = Path(kwargs.get("repo_path", str(self.workspace))).resolve()
            validate_directory(repo_path, self.workspace)

            if operation == "status":
                return await self._status(repo_path, kwargs.get("path"))
            elif operation == "diff":
                return await self._diff(repo_path, kwargs.get("path"))
            elif operation == "log":
                return await self._log(repo_path, kwargs)
            elif operation == "commit":
                return await self._commit(repo_path, kwargs)
            elif operation == "push":
                return await self._push(repo_path, kwargs)
            elif operation == "pull":
                return await self._pull(repo_path, kwargs)
            elif operation == "clone":
                return await self._clone(kwargs)
            elif operation == "checkout":
                return await self._checkout(repo_path, kwargs)
            elif operation == "branch":
                return await self._branch(repo_path, kwargs)
            elif operation == "fetch":
                return await self._fetch(repo_path, kwargs)
            elif operation == "worktree":
                return await self._worktree(repo_path, kwargs)
            elif operation == "apply_patch":
                return await self._apply_patch(repo_path, kwargs)
            elif operation == "rebase":
                return await self._rebase(repo_path, kwargs)
            elif operation == "merge":
                return await self._merge(repo_path, kwargs)
            elif operation == "stash":
                return await self._stash(repo_path, kwargs)
            elif operation == "reset":
                return await self._reset(repo_path, kwargs)
            elif operation == "clean":
                return await self._clean(repo_path, kwargs)
            elif operation == "tag":
                return await self._tag(repo_path, kwargs)
            else:
                return ToolResult(output=f"Unknown git operation: {operation}", error=True)
        except WorkspaceViolationError as e:
            return ToolResult(output=f"Error: {e}", error=True)
        except Exception as e:
            log.exception("Git operation failed")
            return ToolResult(output=f"Git error: {e}", error=True)

    async def _run_git(self, repo_path: Path, args: list[str], cwd: Path | None = None) -> tuple[str, str, int]:
        cmd = ["git", *args]
        workdir = cwd or repo_path
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"), proc.returncode

    async def _status(self, repo_path: Path, path: str | None = None) -> ToolResult:
        args = ["status", "--short", "--branch"]
        if path:
            args.append(path)
        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Failed to get status: {stderr}", error=True)

        if not stdout.strip():
            return ToolResult(output="Working tree clean. No changes detected.")

        lines = stdout.strip().split("\n")
        summary = []
        for line in lines:
            if line.startswith("##"):
                summary.append(f"**Branch:** {line[2:].strip()}")
            elif line.strip():
                status = line[:3].strip()
                file_path = line[3:].strip()
                summary.append(f"`{status}` {file_path}")

        return ToolResult(output="\n".join(summary))

    async def _diff(self, repo_path: Path, path: str | None = None) -> ToolResult:
        args = ["diff", "--unified=3", "--color=never"]
        if path:
            args.extend(["--", path])
        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Failed to get diff: {stderr}", error=True)

        if not stdout.strip():
            return ToolResult(output="No changes to display.")

        return ToolResult(output=f"```diff\n{stdout}\n```")

    async def _log(self, repo_path: Path, kwargs: dict) -> ToolResult:
        limit = kwargs.get("limit", 20)
        author = kwargs.get("author")
        since = kwargs.get("since")

        args = ["log", f"--max-count={limit}", "--pretty=format:%h|%s|%an|%ad", "--date=short"]
        if author:
            args.extend(["--author", author])
        if since:
            args.extend(["--since", since])

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Failed to get log: {stderr}", error=True)

        if not stdout.strip():
            return ToolResult(output="No commits found.")

        commits = []
        for line in stdout.strip().split("\n"):
            if "|" in line:
                parts = line.split("|", 3)
                if len(parts) == 4:
                    hash_val, subject, author, date = parts
                    commits.append(f"**{hash_val}** - {subject} ({author}, {date})")

        return ToolResult(output="\n".join(commits))

    async def _commit(self, repo_path: Path, kwargs: dict) -> ToolResult:
        message = kwargs.get("message")
        if not message:
            return ToolResult(output="Error: commit message is required", error=True)

        if kwargs.get("all"):
            await self._run_git(repo_path, ["add", "-A"])

        args = ["commit", "-m", message]
        if kwargs.get("amend"):
            args.append("--amend")

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Commit failed: {stderr}", error=True)

        return ToolResult(output=f"Committed: {message}")

    async def _push(self, repo_path: Path, kwargs: dict) -> ToolResult:
        args = ["push"]
        if kwargs.get("force"):
            args.append("--force")
        if kwargs.get("set_upstream"):
            args.append("-u")

        remote = kwargs.get("remote", "origin")
        branch = kwargs.get("branch")

        args.extend([remote])
        if branch:
            args.append(branch)

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Push failed: {stderr}", error=True)

        return ToolResult(output=f"Pushed to {remote}/{branch or 'current branch'}")

    async def _pull(self, repo_path: Path, kwargs: dict) -> ToolResult:
        args = ["pull"]
        if kwargs.get("rebase"):
            args.append("--rebase")

        remote = kwargs.get("remote", "origin")
        branch = kwargs.get("branch")

        args.extend([remote])
        if branch:
            args.append(branch)

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Pull failed: {stderr}", error=True)

        return ToolResult(output=f"Pulled from {remote}/{branch or 'current branch'}")

    async def _clone(self, kwargs: dict) -> ToolResult:
        url = kwargs.get("url")
        if not url:
            return ToolResult(output="Error: repository URL is required", error=True)

        target_path = kwargs.get("target_path")
        depth = kwargs.get("depth")

        args = ["clone"]
        if depth:
            args.extend(["--depth", str(depth)])

        args.append(url)
        if target_path:
            args.append(target_path)

        # Validate target path is within workspace
        if target_path:
            target = Path(target_path).resolve()
            validate_path(str(target), self.workspace)

        stdout, stderr, rc = await self._run_git(self.workspace, args)
        if rc != 0:
            return ToolResult(output=f"Clone failed: {stderr}", error=True)

        return ToolResult(output=f"Cloned {url}")

    async def _checkout(self, repo_path: Path, kwargs: dict) -> ToolResult:
        branch = kwargs.get("branch")
        if not branch:
            return ToolResult(output="Error: branch name is required", error=True)

        args = ["checkout"]
        if kwargs.get("create_branch"):
            args.append("-b")

        start_point = kwargs.get("start_point")
        if start_point:
            args.extend([branch, start_point])
        else:
            args.append(branch)

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Checkout failed: {stderr}", error=True)

        action = "Created and switched to" if kwargs.get("create_branch") else "Switched to"
        return ToolResult(output=f"{action} branch '{branch}'")

    async def _branch(self, repo_path: Path, kwargs: dict) -> ToolResult:
        if kwargs.get("list_branches"):
            stdout, stderr, rc = await self._run_git(repo_path, ["branch", "-a"])
            if rc != 0:
                return ToolResult(output=f"Failed to list branches: {stderr}", error=True)

            branches = []
            for line in stdout.strip().split("\n"):
                line = line.strip()
                if line:
                    prefix = "* " if line.startswith("*") else "  "
                    branches.append(f"{prefix}{line[1:] if line.startswith('*') else line}")

            return ToolResult(output="\n".join(branches))

        delete_branch = kwargs.get("delete_branch")
        if delete_branch:
            args = ["branch", "-d"]
            if kwargs.get("force_delete"):
                args[-1] = "-D"
            args.append(delete_branch)

            stdout, stderr, rc = await self._run_git(repo_path, args)
            if rc != 0:
                return ToolResult(output=f"Branch deletion failed: {stderr}", error=True)

            return ToolResult(output=f"Deleted branch '{delete_branch}'")

        return ToolResult(output="Use list_branches=true to list branches, or provide a branch name to delete")

    async def _fetch(self, repo_path: Path, kwargs: dict) -> ToolResult:
        args = ["fetch", "--all"]
        if kwargs.get("prune"):
            args.append("--prune")

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Fetch failed: {stderr}", error=True)

        return ToolResult(output="Fetched all remotes")

    async def _worktree(self, repo_path: Path, kwargs: dict) -> ToolResult:
        operation = kwargs.get("worktree_operation", "list")

        if operation == "list":
            stdout, stderr, rc = await self._run_git(repo_path, ["worktree", "list", "--porcelain"])
            if rc != 0:
                return ToolResult(output=f"Failed to list worktrees: {stderr}", error=True)

            worktrees = []
            current_wt = {}
            for line in stdout.split("\n"):
                if line.startswith("worktree "):
                    if current_wt:
                        worktrees.append(current_wt)
                    current_wt = {"worktree": line[9:]}
                elif line.startswith("HEAD "):
                    current_wt["head"] = line[5:]
                elif line.startswith("branch "):
                    current_wt["branch"] = line[7:]

            if current_wt:
                worktrees.append(current_wt)

            result = []
            for wt in worktrees:
                result.append(f"**{wt.get('worktree', 'N/A')}**")
                if "branch" in wt:
                    result.append(f"  Branch: {wt['branch']}")
                if "head" in wt:
                    result.append(f"  HEAD: {wt['head']}")
                result.append("")

            return ToolResult(output="\n".join(result) if result else "No worktrees found.")

        elif operation == "add":
            branch = kwargs.get("branch")
            worktree_path = kwargs.get("worktree_path")

            if not branch or not worktree_path:
                return ToolResult(output="Error: branch and worktree_path are required", error=True)

            validate_directory(Path(worktree_path))

            args = ["worktree", "add", worktree_path, branch]
            stdout, stderr, rc = await self._run_git(repo_path, args)
            if rc != 0:
                return ToolResult(output=f"Worktree creation failed: {stderr}", error=True)

            return ToolResult(output=f"Created worktree at {worktree_path} for branch '{branch}'")

        elif operation == "remove":
            worktree_path = kwargs.get("worktree_path")
            if not worktree_path:
                return ToolResult(output="Error: worktree_path is required", error=True)

            args = ["worktree", "remove", worktree_path]
            if kwargs.get("force"):
                args.append("--force")

            stdout, stderr, rc = await self._run_git(repo_path, args)
            if rc != 0:
                return ToolResult(output=f"Worktree removal failed: {stderr}", error=True)

            return ToolResult(output=f"Removed worktree at {worktree_path}")

        elif operation == "prune":
            stdout, stderr, rc = await self._run_git(repo_path, ["worktree", "prune"])
            if rc != 0:
                return ToolResult(output=f"Worktree prune failed: {stderr}", error=True)

            return ToolResult(output="Pruned expired worktrees")

        return ToolResult(output=f"Unknown worktree operation: {operation}")

    async def _apply_patch(self, repo_path: Path, kwargs: dict) -> ToolResult:
        patch_content = kwargs.get("patch_content")
        patch_file = kwargs.get("patch_file")

        if not patch_content and not patch_file:
            return ToolResult(output="Error: patch_content or patch_file is required", error=True)

        if patch_file:
            patch_path = Path(patch_file).resolve()
            validate_path(str(patch_path), self.workspace)
            args = ["apply", str(patch_path)]
        else:
            args = ["apply", "--stdin"]

        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(repo_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=patch_content.encode() if patch_content else None)
        rc = proc.returncode

        if rc != 0:
            return ToolResult(output=f"Patch application failed: {stderr.decode('utf-8', errors='replace')}", error=True)

        return ToolResult(output="Patch applied successfully")

    async def _rebase(self, repo_path: Path, kwargs: dict) -> ToolResult:
        if kwargs.get("abort"):
            args = ["rebase", "--abort"]
        elif kwargs.get("rebase_continue"):
            args = ["rebase", "--continue"]
        else:
            upstream = kwargs.get("upstream")
            if not upstream:
                return ToolResult(output="Error: upstream branch is required", error=True)
            args = ["rebase", upstream]

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Rebase failed: {stderr}", error=True)

        return ToolResult(output=f"Rebased onto {upstream}")

    async def _merge(self, repo_path: Path, kwargs: dict) -> ToolResult:
        if kwargs.get("abort"):
            args = ["merge", "--abort"]
            stdout, stderr, rc = await self._run_git(repo_path, args)
            if rc != 0:
                return ToolResult(output=f"Merge abort failed: {stderr}", error=True)
            return ToolResult(output="Merge aborted")

        branch = kwargs.get("branch")
        if not branch:
            return ToolResult(output="Error: branch to merge is required", error=True)

        args = ["merge", branch]

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Merge failed: {stderr}", error=True)

        return ToolResult(output=f"Merged {branch}")

    async def _stash(self, repo_path: Path, kwargs: dict) -> ToolResult:
        if kwargs.get("stash_pop"):
            args = ["stash", "pop"]
            if kwargs.get("index"):
                args.append("--index")

            stdout, stderr, rc = await self._run_git(repo_path, args)
            if rc != 0:
                return ToolResult(output=f"Stash pop failed: {stderr}", error=True)

            return ToolResult(output="Popped latest stash")

        message = kwargs.get("stash_message")
        args = ["stash"]
        if message:
            args.extend(["push", "-m", message])
        else:
            args.append("push")

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Stash failed: {stderr}", error=True)

        return ToolResult(output="Stashed changes" + (f": {message}" if message else ""))

    async def _reset(self, repo_path: Path, kwargs: dict) -> ToolResult:
        mode = kwargs.get("mode", "mixed")
        commit = kwargs.get("commit", "HEAD")

        args = ["reset", f"--{mode}", commit]

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Reset failed: {stderr}", error=True)

        return ToolResult(output=f"Reset to {commit} ({mode} mode)")

    async def _clean(self, repo_path: Path, kwargs: dict) -> ToolResult:
        args = ["clean"]
        if kwargs.get("dry_run"):
            args.append("--dry-run")
        else:
            args.append("-fd")
            if kwargs.get("force"):
                args.append("-f")

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Clean failed: {stderr}", error=True)

        if kwargs.get("dry_run"):
            return ToolResult(output=f"Files that would be removed:\n{stdout}")
        else:
            return ToolResult(output="Cleaned untracked files")

    async def _tag(self, repo_path: Path, kwargs: dict) -> ToolResult:
        tag_name = kwargs.get("tag_name")
        if not tag_name:
            return ToolResult(output="Error: tag_name is required", error=True)

        if kwargs.get("list_tags"):
            stdout, stderr, rc = await self._run_git(repo_path, ["tag", "-l"])
            if rc != 0:
                return ToolResult(output=f"Failed to list tags: {stderr}", error=True)

            tags = [t.strip() for t in stdout.strip().split("\n") if t.strip()]
            return ToolResult(output="\n".join(tags) if tags else "No tags found.")

        message = kwargs.get("tag_message")
        args = ["tag"]
        if message:
            args.extend(["-a", tag_name, "-m", message])
        else:
            args.append(tag_name)

        stdout, stderr, rc = await self._run_git(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Tag creation failed: {stderr}", error=True)

        return ToolResult(output=f"Created tag '{tag_name}'")
