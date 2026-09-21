import asyncio
import logging
import re
from pathlib import Path

from . import Tool, ToolResult
from .security import validate_path, WorkspaceViolationError

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

    def __init__(self, lsp_client=None):
        self.workspace = Path.cwd()
        self.lsp_client = lsp_client

    async def execute(self, patch_content: str, dry_run: bool = False, reverse: bool = False) -> ToolResult:
        try:
            if not patch_content or not patch_content.strip():
                return ToolResult(output="Error: patch_content cannot be empty", error=True)

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
                args = ["apply"]
                
                if dry_run:
                    args.append("--check")
                
                if reverse:
                    args.append("--reverse")

                args.append(patch_file)

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
                
                # Show what was changed with structured metadata
                status_result = await self._get_status()
                changed_files = self._parse_changed_files(patch_content, status_result)
                
                output_parts = [f"Patch applied successfully.\n\nChanged files:\n{status_result}"]
                
                # Add LSP diagnostics feedback for changed files
                if self.lsp_client and changed_files:
                    lsp_feedback = await self._get_lsp_diagnostics(changed_files)
                    if lsp_feedback:
                        output_parts.append(lsp_feedback)

                return ToolResult(output="\n\n".join(output_parts))

            finally:
                try:
                    os.unlink(patch_file)
                except OSError:
                    pass

        except WorkspaceViolationError as e:
            return ToolResult(output=f"Error: {e}", error=True)
        except Exception as e:
            log.exception("Apply patch failed")
            return ToolResult(output=f"Patch application error: {e}", error=True)

    def _parse_changed_files(self, patch_content: str, status: str) -> list[str]:
        """Extract list of changed file paths from patch content."""
        files = set()
        for match in re.finditer(r'^--- a/(.+)$', patch_content, re.MULTILINE):
            files.add(match.group(1))
        for match in re.finditer(r'^\+\+\+ b/(.+)$', patch_content, re.MULTILINE):
            files.add(match.group(1))
        return list(files)

    async def _get_lsp_diagnostics(self, file_paths: list[str]) -> str:
        """Query LSP for diagnostics on changed files."""
        if not self.lsp_client:
            return ""
        
        diagnostics = []
        for fp in file_paths:
            try:
                full_path = self.workspace / fp
                if full_path.exists():
                    diag = await self.lsp_client.get_diagnostics(str(full_path))
                    if diag:
                        diagnostics.append(f"  {fp}:\n{diag}")
            except Exception:
                pass
        
        if not diagnostics:
            return ""
        
        return "## LSP Diagnostics\nLanguage server feedback on changed files:\n" + "\n".join(diagnostics)

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


def is_gpt_model(model_id: str) -> bool:
    """Check if the model is a GPT-family model that benefits from apply_patch."""
    return "gpt-" in (model_id or "").lower() or "o1" in (model_id or "").lower() or "o3" in (model_id or "").lower()
