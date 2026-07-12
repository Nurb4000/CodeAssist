import asyncio
import logging
import shutil
from pathlib import Path
from tools import Tool, ToolResult
from tools.security import validate_directory, WorkspaceViolationError

log = logging.getLogger(__name__)


class ShellTool(Tool):
    name = "shell"
    description = "Execute a shell command. Returns stdout and stderr. Use workdir to set the working directory."
    workspace = Path(".")
    timeout: int = 120
    max_output_chars: int = 20000

    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)", "default": 120},
            "workdir": {"type": "string", "description": "Working directory (defaults to workspace)"},
        },
        "required": ["command"],
    }

    async def execute(self, command: str, timeout: int | None = None, workdir: str | None = None) -> ToolResult:
        if timeout is None:
            timeout = self.timeout
        cwd_str = workdir or str(self.workspace)
        try:
            cwd_path = validate_directory(cwd_str, self.workspace)
        except WorkspaceViolationError as e:
            log.warning("Path validation failed for shell workdir: %s", e)
            return ToolResult(output=f"Error: {e}", error=True)
        except Exception as e:
            return ToolResult(output=f"Error: workdir does not exist: {cwd_str}", error=True)

        shell = shutil.which("bash") or shutil.which("sh") or "sh"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd_path,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return ToolResult(output=f"Error: command timed out after {timeout}s", error=True)
        except Exception as e:
            return ToolResult(output=f"Error executing command: {e}", error=True)

        stdout_str = stdout.decode(errors="replace").strip()
        stderr_str = stderr.decode(errors="replace").strip()

        parts = []
        if stdout_str:
            parts.append(f"STDOUT:\n{stdout_str}")
        if stderr_str:
            parts.append(f"STDERR:\n{stderr_str}")
        parts.append(f"\nExit code: {proc.returncode}")

        output = "\n\n".join(parts) if parts else "(no output)"

        if len(output) > self.max_output_chars:
            half = self.max_output_chars // 2
            output = output[:half] + "\n\n... (truncated) ...\n\n" + output[-half:]

        return ToolResult(output=output, error=proc.returncode != 0)
