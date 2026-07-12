import asyncio
import json
import logging
from pathlib import Path

from tools import Tool, ToolResult
from tools.security import validate_directory, WorkspaceViolationError

log = logging.getLogger(__name__)


class FossilTool(Tool):
    name = "fossil"
    description = (
        "Manage Fossil RCS repositories. Fossil is a distributed version control system "
        "with built-in wiki, issue tracker, and forum. Supports status, diff, log, commit, "
        "checkout, branch, tag, and more operations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "status", "diff", "log", "commit", "checkout", 
                    "branch", "tag", "info", "timeline", "open",
                    "close", "merge", "revert", "export", "import",
                ],
                "description": "Fossil operation to perform",
            },
            "repo_path": {
                "type": "string",
                "description": "Path to Fossil repository (.fslckout or .fossil)",
            },
            # Status/Diff parameters
            "path": {
                "type": "string",
                "description": "Specific file or directory to operate on",
            },
            # Log/Timeline parameters
            "limit": {
                "type": "integer",
                "description": "Number of entries to show (default: 20)",
            },
            "since": {
                "type": "string",
                "description": "Show entries since date (e.g., '2024-01-01')",
            },
            "user": {
                "type": "string",
                "description": "Filter by user",
            },
            # Commit parameters
            "message": {
                "type": "string",
                "description": "Commit message",
            },
            "all": {
                "type": "boolean",
                "description": "Commit all changes",
            },
            # Checkout parameters
            "revision": {
                "type": "string",
                "description": "Revision to checkout (branch, tag, or commit ID)",
            },
            "target_path": {
                "type": "string",
                "description": "Target directory for checkout",
            },
            # Branch parameters
            "list_branches": {
                "type": "boolean",
                "description": "List all branches",
            },
            "create_branch": {
                "type": "string",
                "description": "Create a new branch",
            },
            "delete_branch": {
                "type": "string",
                "description": "Delete a branch",
            },
            # Tag parameters
            "list_tags": {
                "type": "boolean",
                "description": "List all tags",
            },
            "create_tag": {
                "type": "string",
                "description": "Create a new tag",
            },
            "delete_tag": {
                "type": "string",
                "description": "Delete a tag",
            },
            # Merge parameters
            "source_branch": {
                "type": "string",
                "description": "Branch to merge from",
            },
            # Revert parameters
            "revision_to_revert": {
                "type": "string",
                "description": "Revision to revert to",
            },
            # Export parameters
            "format": {
                "type": "string",
                "enum": ["tgz", "zip", "tar"],
                "description": "Export format",
            },
            "filename": {
                "type": "string",
                "description": "Output filename for export",
            },
        },
        "required": ["operation"],
    }

    def __init__(self):
        self.workspace = Path.cwd()

    async def execute(self, operation: str, **kwargs) -> ToolResult:
        try:
            repo_path = Path(kwargs.get("repo_path", str(self.workspace))).resolve()
            
            # Validate it's a fossil repo
            if not self._is_fossil_repo(repo_path):
                return ToolResult(
                    output=f"Error: Not a Fossil repository: {repo_path}",
                    error=True
                )

            if operation == "status":
                return await self._status(repo_path, kwargs.get("path"))
            elif operation == "diff":
                return await self._diff(repo_path, kwargs.get("path"))
            elif operation == "log":
                return await self._log(repo_path, kwargs)
            elif operation == "commit":
                return await self._commit(repo_path, kwargs)
            elif operation == "checkout":
                return await self._checkout(repo_path, kwargs)
            elif operation == "branch":
                return await self._branch(repo_path, kwargs)
            elif operation == "tag":
                return await self._tag(repo_path, kwargs)
            elif operation == "info":
                return await self._info(repo_path)
            elif operation == "timeline":
                return await self._timeline(repo_path, kwargs)
            elif operation == "open":
                return await self._open(repo_path)
            elif operation == "close":
                return await self._close(repo_path, kwargs.get("revision"))
            elif operation == "merge":
                return await self._merge(repo_path, kwargs)
            elif operation == "revert":
                return await self._revert(repo_path, kwargs)
            elif operation == "export":
                return await self._export(repo_path, kwargs)
            else:
                return ToolResult(output=f"Unknown fossil operation: {operation}", error=True)
        except WorkspaceViolationError as e:
            return ToolResult(output=f"Error: {e}", error=True)
        except Exception as e:
            log.exception("Fossil operation failed")
            return ToolResult(output=f"Fossil error: {e}", error=True)

    def _is_fossil_repo(self, path: Path) -> bool:
        """Check if path is a Fossil repository."""
        # Check for .fslckout (working tree) or .fossil (repository file)
        return (path / ".fslckout").exists() or (path / ".fossil").exists()

    async def _run_fossil(self, repo_path: Path, args: list[str]) -> tuple[str, str, int]:
        """Run a fossil command."""
        cmd = ["fossil", *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"), proc.returncode

    async def _status(self, repo_path: Path, path: str | None) -> ToolResult:
        """Get repository status."""
        args = ["status"]
        if path:
            args.extend(["--", path])
        
        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Failed to get status: {stderr}", error=True)

        if not stdout.strip():
            return ToolResult(output="Working tree clean. No changes detected.")

        lines = stdout.strip().split("\n")
        result_lines = []
        for line in lines:
            if line.startswith("M "):
                result_lines.append(f"`M` {line[2:].strip()}")
            elif line.startswith("A "):
                result_lines.append(f"`A` {line[2:].strip()}")
            elif line.startswith("R "):
                result_lines.append(f"`R` {line[2:].strip()}")
            elif line.startswith("D "):
                result_lines.append(f"`D` {line[2:].strip()}")
            elif line.startswith("? "):
                result_lines.append(f"`?` {line[2:].strip()} (unmanaged)")
            elif line.strip():
                result_lines.append(line)

        return ToolResult(output="\n".join(result_lines))

    async def _diff(self, repo_path: Path, path: str | None) -> ToolResult:
        """Get diff."""
        args = ["diff"]
        if path:
            args.extend(["--", path])
        
        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Failed to get diff: {stderr}", error=True)

        if not stdout.strip():
            return ToolResult(output="No changes to display.")

        return ToolResult(output=f"```diff\n{stdout}\n```")

    async def _log(self, repo_path: Path, kwargs: dict) -> ToolResult:
        """Get commit log."""
        limit = kwargs.get("limit", 20)
        user = kwargs.get("user")
        since = kwargs.get("since")

        args = ["log", "-l", str(limit)]
        if user:
            args.extend(["-u", user])
        if since:
            args.extend(["-D", since])

        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Failed to get log: {stderr}", error=True)

        if not stdout.strip():
            return ToolResult(output="No commits found.")

        # Parse fossil log output
        commits = []
        current_commit = {}
        
        for line in stdout.split("\n"):
            if line.startswith("check-in "):
                if current_commit:
                    commits.append(current_commit)
                current_commit = {"id": line.split()[1]}
            elif line.startswith("user "):
                current_commit["user"] = line[5:].strip()
            elif line.startswith("date "):
                current_commit["date"] = line[5:].strip()
            elif line.startswith("comment: "):
                current_commit["message"] = line[9:].strip()
        
        if current_commit:
            commits.append(current_commit)

        result_lines = []
        for commit in commits[:limit]:
            msg = commit.get("message", "No message")
            user = commit.get("user", "unknown")
            date = commit.get("date", "unknown")
            cid = commit.get("id", "?")
            result_lines.append(f"**{cid}** - {msg} ({user}, {date})")

        return ToolResult(output="\n".join(result_lines))

    async def _commit(self, repo_path: Path, kwargs: dict) -> ToolResult:
        """Commit changes."""
        message = kwargs.get("message")
        if not message:
            return ToolResult(output="Error: commit message is required", error=True)

        args = ["commit", "-m", message]
        if kwargs.get("all"):
            args.append("--all")

        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Commit failed: {stderr}", error=True)

        return ToolResult(output=f"Committed: {message}")

    async def _checkout(self, repo_path: Path, kwargs: dict) -> ToolResult:
        """Checkout a revision."""
        revision = kwargs.get("revision")
        if not revision:
            return ToolResult(output="Error: revision is required", error=True)

        args = ["checkout", revision]
        
        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Checkout failed: {stderr}", error=True)

        return ToolResult(output=f"Checked out revision: {revision}")

    async def _branch(self, repo_path: Path, kwargs: dict) -> ToolResult:
        """Manage branches."""
        if kwargs.get("list_branches"):
            stdout, stderr, rc = await self._run_fossil(repo_path, ["branch"])
            if rc != 0:
                return ToolResult(output=f"Failed to list branches: {stderr}", error=True)
            
            branches = [b.strip() for b in stdout.strip().split("\n") if b.strip()]
            return ToolResult(output="\n".join(f"- {b}" for b in branches))

        create_branch = kwargs.get("create_branch")
        if create_branch:
            args = ["branch", create_branch]
            stdout, stderr, rc = await self._run_fossil(repo_path, args)
            if rc != 0:
                return ToolResult(output=f"Branch creation failed: {stderr}", error=True)
            return ToolResult(output=f"Created branch: {create_branch}")

        delete_branch = kwargs.get("delete_branch")
        if delete_branch:
            args = ["branch", "-d", delete_branch]
            stdout, stderr, rc = await self._run_fossil(repo_path, args)
            if rc != 0:
                return ToolResult(output=f"Branch deletion failed: {stderr}", error=True)
            return ToolResult(output=f"Deleted branch: {delete_branch}")

        return ToolResult(output="Use list_branches=true, create_branch=<name>, or delete_branch=<name>")

    async def _tag(self, repo_path: Path, kwargs: dict) -> ToolResult:
        """Manage tags."""
        if kwargs.get("list_tags"):
            stdout, stderr, rc = await self._run_fossil(repo_path, ["tag"])
            if rc != 0:
                return ToolResult(output=f"Failed to list tags: {stderr}", error=True)
            
            tags = [t.strip() for t in stdout.strip().split("\n") if t.strip()]
            return ToolResult(output="\n".join(f"- {t}" for t in tags))

        create_tag = kwargs.get("create_tag")
        if create_tag:
            args = ["tag", create_tag]
            stdout, stderr, rc = await self._run_fossil(repo_path, args)
            if rc != 0:
                return ToolResult(output=f"Tag creation failed: {stderr}", error=True)
            return ToolResult(output=f"Created tag: {create_tag}")

        delete_tag = kwargs.get("delete_tag")
        if delete_tag:
            args = ["tag", "-d", delete_tag]
            stdout, stderr, rc = await self._run_fossil(repo_path, args)
            if rc != 0:
                return ToolResult(output=f"Tag deletion failed: {stderr}", error=True)
            return ToolResult(output=f"Deleted tag: {delete_tag}")

        return ToolResult(output="Use list_tags=true, create_tag=<name>, or delete_tag=<name>")

    async def _info(self, repo_path: Path) -> ToolResult:
        """Get repository info."""
        stdout, stderr, rc = await self._run_fossil(repo_path, ["info"])
        if rc != 0:
            return ToolResult(output=f"Failed to get info: {stderr}", error=True)

        return ToolResult(output=f"**Repository Info:**\n{stdout}")

    async def _timeline(self, repo_path: Path, kwargs: dict) -> ToolResult:
        """Get timeline."""
        limit = kwargs.get("limit", 20)
        
        args = ["timeline", "-l", str(limit)]
        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Failed to get timeline: {stderr}", error=True)

        return ToolResult(output=f"**Timeline:**\n{stdout}")

    async def _open(self, repo_path: Path) -> ToolResult:
        """List open check-ins."""
        stdout, stderr, rc = await self._run_fossil(repo_path, ["open"])
        if rc != 0:
            return ToolResult(output=f"Failed to list open check-ins: {stderr}", error=True)

        if not stdout.strip():
            return ToolResult(output="No open check-ins.")

        return ToolResult(output=f"**Open Check-ins:**\n{stdout}")

    async def _close(self, repo_path: Path, revision: str | None) -> ToolResult:
        """Close a check-in."""
        if not revision:
            return ToolResult(output="Error: revision is required", error=True)

        args = ["close", revision]
        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Close failed: {stderr}", error=True)

        return ToolResult(output=f"Closed check-in: {revision}")

    async def _merge(self, repo_path: Path, kwargs: dict) -> ToolResult:
        """Merge a branch."""
        source = kwargs.get("source_branch")
        if not source:
            return ToolResult(output="Error: source_branch is required", error=True)

        args = ["merge", source]
        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Merge failed: {stderr}", error=True)

        return ToolResult(output=f"Merged branch: {source}")

    async def _revert(self, repo_path: Path, kwargs: dict) -> ToolResult:
        """Revert to a revision."""
        revision = kwargs.get("revision_to_revert")
        if not revision:
            return ToolResult(output="Error: revision_to_revert is required", error=True)

        args = ["revert", "--all", revision]
        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Revert failed: {stderr}", error=True)

        return ToolResult(output=f"Reverted to revision: {revision}")

    async def _export(self, repo_path: Path, kwargs: dict) -> ToolResult:
        """Export repository."""
        fmt = kwargs.get("format", "tgz")
        filename = kwargs.get("filename")
        
        if not filename:
            return ToolResult(output="Error: filename is required for export", error=True)

        args = ["export", f"--{fmt}", filename]
        stdout, stderr, rc = await self._run_fossil(repo_path, args)
        if rc != 0:
            return ToolResult(output=f"Export failed: {stderr}", error=True)

        return ToolResult(output=f"Exported to {filename}")
