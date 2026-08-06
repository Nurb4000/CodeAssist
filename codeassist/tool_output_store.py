"""Managed tool output file storage.

When tool output exceeds configured limits, saves the full output to a managed
file and returns a truncated preview with a path hint for the agent.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class ToolOutputStore:
    """Manages tool output files that exceed inline token limits."""

    def __init__(
        self,
        workspace: Path,
        max_lines: int = 2000,
        max_bytes: int = 51200,
        retention_days: int = 7,
    ):
        self.workspace = Path(workspace).resolve()
        self.output_dir = self.workspace / ".codeassist" / "tool-output"
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_filename(self, tool_name: str, session_id: str) -> str:
        """Generate a unique filename for a tool output."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        safe_tool = tool_name.replace("/", "_").replace("\\", "_")
        return f"{safe_tool}_{session_id[:8]}_{ts}.txt"

    async def save_if_needed(
        self,
        output: str,
        tool_name: str,
        session_id: str,
    ) -> str | None:
        """Save full output to file if it exceeds limits. Returns file path or None."""
        if not output:
            return None

        line_count = output.count("\n") + 1
        byte_count = len(output.encode("utf-8", errors="replace"))

        if line_count <= self.max_lines and byte_count <= self.max_bytes:
            return None

        filename = self._get_filename(tool_name, session_id)
        filepath = self.output_dir / filename

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, filepath.write_text, output, "utf-8", "replace")
            log.info(
                "Saved oversized tool output: %s (%d lines, %d bytes) -> %s",
                tool_name, line_count, byte_count, filepath,
            )
            return str(filepath)
        except Exception as e:
            log.error("Failed to save tool output: %s", e)
            return None

    def build_preview(self, output: str, filepath: str, tool_name: str) -> str:
        """Build a truncated preview with path hint for the agent."""
        lines = output.split("\n")

        # Keep head and tail
        head_count = max(50, self.max_lines // 4)
        tail_count = max(50, self.max_lines // 4)

        head = "\n".join(lines[:head_count])
        tail = "\n".join(lines[-tail_count:]) if len(lines) > head_count + tail_count else ""

        preview_lines = [
            f"[Tool output too large ({len(lines)} lines, {len(output.encode('utf-8', errors='replace'))} bytes). "
            f"Showing first and last {head_count} lines.]",
            "",
            head,
        ]

        if tail:
            preview_lines.extend(["", f"... ({len(lines) - head_count - tail_count} lines omitted) ...", "", tail])

        # Add path hint
        safe_path = filepath
        if self.workspace in Path(filepath).resolve().parents or Path(filepath).resolve() == self.workspace:
            rel_path = Path(filepath).relative_to(self.workspace)
            safe_path = str(rel_path)

        preview_lines.extend([
            "",
            f"Full output saved to: `{safe_path}`. Use Read with offset/limit to examine specific sections, "
            f"or Grep to search for patterns.",
        ])

        return "\n".join(preview_lines)

    async def cleanup(self) -> int:
        """Remove files older than retention period. Returns count of removed files."""
        if not self.output_dir.exists():
            return 0

        cutoff = time.time() - (self.retention_days * 86400)
        removed = 0

        try:
            for filepath in self.output_dir.iterdir():
                if filepath.is_file() and filepath.stat().st_mtime < cutoff:
                    filepath.unlink()
                    removed += 1
        except Exception as e:
            log.error("Cleanup failed: %s", e)

        if removed:
            log.info("Cleaned up %d old tool output files", removed)
        return removed

    async def list_outputs(self, session_id: str = None) -> list[dict]:
        """List saved tool outputs, optionally filtered by session."""
        results = []
        if not self.output_dir.exists():
            return results

        for filepath in sorted(self.output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not filepath.is_file():
                continue

            # Extract session_id from filename: {tool}_{session8}_{timestamp}.txt
            name = filepath.stem
            parts = name.split("_")
            file_session = None
            if len(parts) >= 2:
                # Session ID is the part that's 8 chars and looks like a UUID prefix
                for p in parts[1:-1]:  # Skip tool name and timestamp
                    if len(p) == 8:
                        file_session = p
                        break

            if session_id and not (file_session and session_id.startswith(file_session)):
                continue

            stat = filepath.stat()
            results.append({
                "path": str(filepath),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "session_prefix": file_session,
            })

        return results


# Module-level singleton (lazy initialization)
_store_instance: ToolOutputStore | None = None


def get_tool_output_store(workspace: Path, **kwargs) -> ToolOutputStore:
    """Get or create the tool output store for a workspace."""
    global _store_instance
    if _store_instance is None or _store_instance.workspace != workspace.resolve():
        _store_instance = ToolOutputStore(workspace=workspace, **kwargs)
    return _store_instance
