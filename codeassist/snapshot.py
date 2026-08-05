import asyncio
import json
import logging
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class SnapshotRecord:
    """Represents a workspace snapshot."""
    id: str
    session_id: str
    turn_number: int
    git_hash: str
    files_changed: list[str]
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "turn_number": self.turn_number,
            "git_hash": self.git_hash,
            "files_changed": self.files_changed,
            "created_at": self.created_at,
        }


class SnapshotManager:
    """Manages workspace snapshots using a hidden git repo."""

    def __init__(self, workspace: Path, enabled: bool = True):
        self.workspace = workspace.resolve()
        self.enabled = enabled
        self.snapshot_dir = self.workspace / ".codeassist" / "snapshot"
        self._initialized = False
        self._snapshots: dict[str, SnapshotRecord] = {}
        self._turn_counter = 0

    async def initialize(self):
        """Initialize the snapshot git repo."""
        if not self.enabled:
            return

        try:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)

            # Check if git repo exists
            git_dir = self.snapshot_dir / ".git"
            if not git_dir.exists():
                subprocess.run(
                    ["git", "init", "-q"],
                    cwd=self.snapshot_dir,
                    capture_output=True,
                    check=True,
                )
                # Configure git user for snapshot repo
                subprocess.run(
                    ["git", "config", "user.name", "CodeAssist"],
                    cwd=self.snapshot_dir,
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    ["git", "config", "user.email", "codeassist@local"],
                    cwd=self.snapshot_dir,
                    capture_output=True,
                    check=True,
                )

            # Create .gitignore to exclude snapshot metadata
            gitignore = self.snapshot_dir / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text("*.pyc\n__pycache__/\n.env\nnode_modules/\n")

            # Initial commit if empty
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.snapshot_dir,
                capture_output=True,
                text=True,
            )
            if not result.stdout.strip():
                # Repo is empty, create initial file
                (self.snapshot_dir / ".snapshot-init").write_text("initialized")
                subprocess.run(
                    ["git", "add", "."],
                    cwd=self.snapshot_dir,
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", "Initial snapshot", "--quiet"],
                    cwd=self.snapshot_dir,
                    capture_output=True,
                    check=True,
                )

            self._initialized = True
            log.info("Snapshot manager initialized at %s", self.snapshot_dir)

        except Exception as e:
            log.error("Failed to initialize snapshot manager: %s", e)
            self.enabled = False

    def _mirror_workspace(self):
        """Mirror workspace files into snapshot repo."""
        if not self._initialized:
            return

        try:
            # Use rsync-like approach: copy workspace to snapshot dir
            # excluding .codeassist, .git, and other hidden dirs
            exclude_patterns = {".codeassist", ".git", "__pycache__", "node_modules", ".venv"}

            for item in self.workspace.iterdir():
                if item.name.startswith(".") or item.name in exclude_patterns:
                    continue

                target = self.snapshot_dir / item.name
                if item.is_dir():
                    # Mirror directory (skip existing snapshot dirs)
                    if target.exists():
                        self._mirror_directory(item, target, exclude_patterns)
                    else:
                        import shutil
                        shutil.copytree(item, target, ignore=self._ignore_func(exclude_patterns), symlinks=True)
                else:
                    import shutil
                    shutil.copy2(item, target)

        except Exception as e:
            log.debug("Error mirroring workspace: %s", e)

    def _mirror_directory(self, src: Path, dst: Path, exclude: set):
        """Recursively mirror a directory."""
        try:
            for item in src.iterdir():
                if item.name in exclude:
                    continue
                target = dst / item.name
                if item.is_dir():
                    if not target.exists():
                        target.mkdir(exist_ok=True)
                    self._mirror_directory(item, target, exclude)
                else:
                    import shutil
                    shutil.copy2(item, target)
        except Exception as e:
            log.debug("Error mirroring directory %s: %s", src, e)

    @staticmethod
    def _ignore_func(exclude):
        """Create an ignore function for shutil.copytree."""
        def ignore(directory, contents):
            return {c for c in contents if c in exclude or c.startswith(".")}
        return ignore

    async def create_snapshot(self, session_id: str, turn_number: int = 0) -> SnapshotRecord | None:
        """Create a snapshot of the current workspace state."""
        if not self.enabled or not self._initialized:
            return None

        self._turn_counter = turn_number

        try:
            # Mirror workspace to snapshot repo
            self._mirror_workspace()

            # Stage all changes
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.snapshot_dir,
                capture_output=True,
                check=True,
            )

            # Check if there are changes
            result = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=self.snapshot_dir,
                capture_output=True,
            )

            files_changed = []
            if result.returncode != 0:
                # There are changes, commit them
                msg = f"Snapshot: session={session_id} turn={turn_number}"
                subprocess.run(
                    ["git", "commit", "-m", msg, "--quiet"],
                    cwd=self.snapshot_dir,
                    capture_output=True,
                    check=True,
                )

                # Get list of changed files
                diff_result = subprocess.run(
                    ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
                    cwd=self.snapshot_dir,
                    capture_output=True,
                    text=True,
                )
                files_changed = [f.strip() for f in diff_result.stdout.split("\n") if f.strip()]

            # Get current hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.snapshot_dir,
                capture_output=True,
                text=True,
            )
            git_hash = hash_result.stdout.strip()

            snapshot_id = str(uuid.uuid4())[:8]
            now = datetime.now(timezone.utc).isoformat()

            record = SnapshotRecord(
                id=snapshot_id,
                session_id=session_id,
                turn_number=turn_number,
                git_hash=git_hash,
                files_changed=files_changed,
                created_at=now,
            )

            self._snapshots[snapshot_id] = record

            # Persist to database
            await self._persist_snapshot(record)

            log.debug("Created snapshot %s: %d files changed", snapshot_id, len(files_changed))
            return record

        except Exception as e:
            log.error("Failed to create snapshot: %s", e)
            return None

    async def get_diff(self, snapshot_id: str) -> str | None:
        """Get diff between a snapshot and current state."""
        if not self.enabled or not self._initialized:
            return None

        record = self._snapshots.get(snapshot_id)
        if not record:
            return None

        try:
            # Mirror current state
            self._mirror_workspace()
            subprocess.run(["git", "add", "-A"], cwd=self.snapshot_dir, capture_output=True)

            # Get diff against the snapshot
            result = subprocess.run(
                ["git", "diff", record.git_hash, "--cached"],
                cwd=self.snapshot_dir,
                capture_output=True,
                text=True,
            )
            return result.stdout

        except Exception as e:
            log.error("Failed to get diff: %s", e)
            return None

    async def revert_to_snapshot(self, snapshot_id: str) -> bool:
        """Revert workspace to a specific snapshot."""
        if not self.enabled or not self._initialized:
            return False

        record = self._snapshots.get(snapshot_id)
        if not record:
            return None

        try:
            # Checkout the snapshot state
            subprocess.run(
                ["git", "checkout", record.git_hash, "--", "."],
                cwd=self.snapshot_dir,
                capture_output=True,
                check=True,
            )

            # Copy snapshot files back to workspace
            self._restore_to_workspace()

            log.info("Reverted to snapshot %s (hash=%s)", snapshot_id, record.git_hash)
            return True

        except Exception as e:
            log.error("Failed to revert to snapshot: %s", e)
            return False

    def _restore_to_workspace(self):
        """Copy files from snapshot repo back to workspace."""
        try:
            for item in self.snapshot_dir.iterdir():
                if item.name.startswith(".") or item.name == ".snapshot-init":
                    continue

                target = self.workspace / item.name
                if item.is_dir():
                    import shutil
                    if target.exists():
                        self._restore_directory(item, target)
                    else:
                        shutil.copytree(item, target)
                else:
                    import shutil
                    shutil.copy2(item, target)

        except Exception as e:
            log.debug("Error restoring to workspace: %s", e)

    def _restore_directory(self, src: Path, dst: Path):
        """Recursively restore a directory."""
        try:
            for item in src.iterdir():
                if item.name.startswith("."):
                    continue
                target = dst / item.name
                if item.is_dir():
                    if not target.exists():
                        target.mkdir(exist_ok=True)
                    self._restore_directory(item, target)
                else:
                    import shutil
                    shutil.copy2(item, target)
        except Exception as e:
            log.debug("Error restoring directory %s: %s", src, e)

    async def get_session_snapshots(self, session_id: str) -> list[SnapshotRecord]:
        """Get all snapshots for a session."""
        return [s for s in self._snapshots.values() if s.session_id == session_id]

    async def get_latest_snapshot(self, session_id: str) -> SnapshotRecord | None:
        """Get the latest snapshot for a session."""
        session_snapshots = await self.get_session_snapshots(session_id)
        if not session_snapshots:
            return None
        return max(session_snapshots, key=lambda s: s.turn_number)

    async def cleanup_old_snapshots(self, retention_days: int = 7):
        """Prune old snapshots beyond retention period."""
        if not self.enabled or not self._initialized:
            return

        try:
            cutoff = datetime.now(timezone.utc).timestamp() - (retention_days * 86400)

            # Find and delete old commits
            result = subprocess.run(
                ["git", "reflog", "expire", "--expire-unreachable=now", "--all"],
                cwd=self.snapshot_dir,
                capture_output=True,
            )

            # Run garbage collection
            subprocess.run(
                ["git", "gc", "--prune=now"],
                cwd=self.snapshot_dir,
                capture_output=True,
            )

            # Clean up in-memory records
            expired = [
                sid for sid, s in self._snapshots.items()
                if datetime.fromisoformat(s.created_at).timestamp() < cutoff
            ]
            for sid in expired:
                del self._snapshots[sid]

            log.info("Cleaned up %d old snapshots", len(expired))

        except Exception as e:
            log.error("Failed to cleanup snapshots: %s", e)

    async def _persist_snapshot(self, record: SnapshotRecord):
        """Persist snapshot record to database."""
        try:
            from codeassist.session import get_db
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO snapshots (id, session_id, turn_number, git_hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (record.id, record.session_id, record.turn_number, record.git_hash, record.created_at),
                )
                await db.commit()
        except Exception as e:
            log.debug("Failed to persist snapshot: %s", e)

    async def load_snapshots(self):
        """Load snapshots from database."""
        try:
            from codeassist.session import get_db
            async with get_db() as db:
                cursor = await db.execute(
                    "SELECT * FROM snapshots ORDER BY created_at DESC"
                )
                rows = await cursor.fetchall()
                for row in rows:
                    record = SnapshotRecord(
                        id=row["id"],
                        session_id=row["session_id"],
                        turn_number=row["turn_number"],
                        git_hash=row["git_hash"],
                        files_changed=[],
                        created_at=row["created_at"],
                    )
                    self._snapshots[record.id] = record
        except Exception as e:
            log.debug("Failed to load snapshots: %s", e)


# Singleton instance
snapshot_manager: SnapshotManager | None = None


def get_snapshot_manager(workspace: Path, enabled: bool = True) -> SnapshotManager:
    """Get or create the snapshot manager singleton."""
    global snapshot_manager
    if snapshot_manager is None:
        snapshot_manager = SnapshotManager(workspace, enabled)
    return snapshot_manager
