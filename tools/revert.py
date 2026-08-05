import logging
from tools import Tool, ToolResult

log = logging.getLogger(__name__)


class RevertTool(Tool):
    name = "revert"
    description = (
        "Revert workspace changes using snapshots. Use 'list' to see available snapshots, "
        "'diff' to preview changes since a snapshot, or 'apply' to revert to a snapshot.\n\n"
        "Snapshots are created automatically at session boundaries."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "diff", "apply"],
                "description": "Revert action: 'list' snapshots, 'diff' against snapshot, or 'apply' revert",
            },
            "snapshot_id": {
                "type": "string",
                "description": "Snapshot ID (required for diff and apply actions)",
            },
        },
        "required": ["action"],
    }

    async def execute(self, action: str, snapshot_id: str | None = None) -> ToolResult:
        from codeassist.snapshot import get_snapshot_manager

        sm = get_snapshot_manager(None, enabled=False)  # Placeholder — get real instance
        if not sm or not sm.enabled:
            return ToolResult(output="Snapshot system is not enabled.", error=True)

        try:
            if action == "list":
                return await self._list_snapshots(sm)
            elif action == "diff":
                if not snapshot_id:
                    return ToolResult(output="Error: snapshot_id is required for diff action", error=True)
                return await self._get_diff(sm, snapshot_id)
            elif action == "apply":
                if not snapshot_id:
                    return ToolResult(output="Error: snapshot_id is required for apply action", error=True)
                return await self._apply_revert(sm, snapshot_id)
            else:
                return ToolResult(output=f"Error: unknown action '{action}'", error=True)

        except Exception as e:
            log.exception("Revert tool failed")
            return ToolResult(output=f"Error: {e}", error=True)

    async def _list_snapshots(self, sm) -> ToolResult:
        """List available snapshots."""
        # Get all snapshots from memory
        snapshots = list(sm._snapshots.values())
        if not snapshots:
            return ToolResult(output="No snapshots available.")

        lines = ["**Available Snapshots:**\n"]
        for s in sorted(snapshots, key=lambda x: x.created_at, reverse=True)[:20]:
            files = f" ({len(s.files_changed)} files)" if s.files_changed else ""
            lines.append(f"- `{s.id}`: turn {s.turn_number} at {s.created_at}{files}")

        return ToolResult(output="\n".join(lines))

    async def _get_diff(self, sm, snapshot_id: str) -> ToolResult:
        """Get diff against a snapshot."""
        record = sm._snapshots.get(snapshot_id)
        if not record:
            return ToolResult(output=f"Snapshot '{snapshot_id}' not found.", error=True)

        diff = await sm.get_diff(snapshot_id)
        if not diff:
            return ToolResult(output="No changes since snapshot.")

        # Truncate large diffs
        if len(diff) > 10000:
            return ToolResult(
                output=f"Diff is large ({len(diff)} chars). First 5000 chars:\n\n```\n{diff[:5000]}\n...\n```"
            )

        return ToolResult(output=f"**Diff since snapshot {snapshot_id}:**\n\n```\n{diff}\n```")

    async def _apply_revert(self, sm, snapshot_id: str) -> ToolResult:
        """Apply revert to a snapshot."""
        record = sm._snapshots.get(snapshot_id)
        if not record:
            return ToolResult(output=f"Snapshot '{snapshot_id}' not found.", error=True)

        success = await sm.revert_to_snapshot(snapshot_id)
        if success:
            return ToolResult(
                output=f"Successfully reverted to snapshot `{snapshot_id}` (turn {record.turn_number}).\n"
                       f"Workspace has been restored to its state at that point."
            )
        else:
            return ToolResult(output="Failed to revert. The snapshot may be invalid.", error=True)
