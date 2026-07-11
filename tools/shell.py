import asyncio
import shutil
from pathlib import Path
from tools import Tool, ToolResult


class ShellTool(Tool):
    name = "shell"
    description = "Execute a shell command. Returns stdout and stderr. Use workdir to set the working directory."
    workspace = Path(".")

    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)", "default": 120},
            "workdir": {"type": "string", "description": "Working directory (defaults to workspace)"},
        },
        "required": ["command"],
    }

    async def execute(self, command: str, timeout: int = 120, workdir: str | None = None) -> ToolResult:
        cwd = workdir or str(self.workspace)
        cwd_path = Path(cwd)
        if not cwd_path.exists():
            return ToolResult(output=f"Error: workdir does not exist: {cwd}", error=True)

        shell = shutil.which("bash") or shutil.which("sh") or "sh"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
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

        if len(output) > 20000:
            output = output[:10000] + "\n\n... (truncated) ...\n\n" + output[-10000:]

        return ToolResult(output=output, error=proc.returncode != 0)
