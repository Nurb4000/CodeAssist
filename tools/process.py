import asyncio
import json
import logging
import signal
from pathlib import Path

from tools import Tool, ToolResult
from tools.security import validate_directory, WorkspaceViolationError

log = logging.getLogger(__name__)


class ProcessTool(Tool):
    name = "process"
    description = (
        "Manage running processes. Start, stop, monitor, and get information "
        "about long-running processes. Use this for development servers, "
        "background tasks, or monitoring process status."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "stop", "status", "logs", "list"],
                "description": "Process action to perform",
            },
            "command": {
                "type": "string",
                "description": "Command to run (for start action)",
            },
            "process_id": {
                "type": "string",
                "description": "Process ID or name (for stop, status, logs)",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the process",
            },
            "env": {
                "type": "object",
                "description": "Environment variables for the process",
            },
            "tail_lines": {
                "type": "integer",
                "description": "Number of log lines to return (default: 50)",
            },
        },
        "required": ["action"],
    }

    def __init__(self):
        self.workspace = Path.cwd()
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def execute(self, action: str, command: str | None = None,
                     process_id: str | None = None, cwd: str | None = None,
                     env: dict | None = None, tail_lines: int = 50) -> ToolResult:
        try:
            if action == "start":
                return await self._start(command, cwd, env)
            elif action == "stop":
                return await self._stop(process_id)
            elif action == "status":
                return await self._status(process_id)
            elif action == "logs":
                return await self._logs(process_id, tail_lines)
            elif action == "list":
                return await self._list()
            else:
                return ToolResult(output=f"Error: unknown action '{action}'", error=True)

        except Exception as e:
            log.exception("Process operation failed")
            return ToolResult(output=f"Process error: {e}", error=True)

    async def _start(self, command: str | None, cwd: str | None, env: dict | None) -> ToolResult:
        """Start a new process."""
        if not command:
            return ToolResult(output="Error: command is required for start action", error=True)

        proc_cwd = Path(cwd or str(self.workspace)).resolve()
        
        # Build environment
        proc_env = None
        if env:
            proc_env = {**__import__('os').environ, **env}

        proc = await asyncio.create_subprocess_exec(
            *command.split(),
            cwd=str(proc_cwd),
            env=proc_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        process_id = str(proc.pid)
        self._processes[process_id] = proc

        return ToolResult(
            output=f"Process started:\n  PID: {process_id}\n  Command: {command}\n  Working dir: {proc_cwd}"
        )

    async def _stop(self, process_id: str | None) -> ToolResult:
        """Stop a running process."""
        if not process_id:
            return ToolResult(output="Error: process_id is required for stop action", error=True)

        proc = self._processes.get(process_id)
        if not proc:
            # Try to find by command name
            for pid, p in self._processes.items():
                if str(pid) in str(p.pid) or process_id in str(p.pid):
                    proc = p
                    process_id = pid
                    break

        if not proc:
            return ToolResult(output=f"Error: process '{process_id}' not found", error=True)

        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

            del self._processes[process_id]
            return ToolResult(output=f"Process {process_id} stopped successfully")

        except Exception as e:
            return ToolResult(output=f"Error stopping process: {e}", error=True)

    async def _status(self, process_id: str | None) -> ToolResult:
        """Get status of a process."""
        if process_id:
            proc = self._processes.get(process_id)
            if not proc:
                return ToolResult(output=f"Error: process '{process_id}' not found", error=True)

            status = "running" if proc.returncode is None else f"exited ({proc.returncode})"
            return ToolResult(
                output=f"Process {process_id}:\n  Status: {status}\n  PID: {proc.pid}"
            )
        else:
            return await self._list()

    async def _logs(self, process_id: str | None, tail_lines: int) -> ToolResult:
        """Get logs from a process."""
        if not process_id:
            return ToolResult(output="Error: process_id is required for logs action", error=True)

        proc = self._processes.get(process_id)
        if not proc:
            return ToolResult(output=f"Error: process '{process_id}' not found", error=True)

        # Read available output
        stdout_lines = []
        stderr_lines = []

        try:
            if proc.stdout and not proc.stdout.at_eof():
                stdout_data = await asyncio.wait_for(proc.stdout.read(1024), timeout=1.0)
                stdout_lines = stdout_data.decode('utf-8', errors='replace').strip().split('\n')

            if proc.stderr and not proc.stderr.at_eof():
                stderr_data = await asyncio.wait_for(proc.stderr.read(1024), timeout=1.0)
                stderr_lines = stderr_data.decode('utf-8', errors='replace').strip().split('\n')
        except (asyncio.TimeoutError, Exception):
            pass

        output_parts = []
        if stdout_lines:
            output_parts.append(f"**stdout** (last {min(len(stdout_lines), tail_lines)} lines):\n")
            output_parts.extend(stdout_lines[-tail_lines:])

        if stderr_lines:
            output_parts.append(f"\n**stderr** (last {min(len(stderr_lines), tail_lines)} lines):\n")
            output_parts.extend(stderr_lines[-tail_lines:])

        if not output_parts:
            return ToolResult(output=f"No logs available for process {process_id}")

        return ToolResult(output="\n".join(output_parts))

    async def _list(self) -> ToolResult:
        """List all managed processes."""
        if not self._processes:
            return ToolResult(output="No running processes managed by CodeAssist")

        lines = ["**Running Processes:**\n"]
        for pid, proc in self._processes.items():
            status = "running" if proc.returncode is None else f"exited ({proc.returncode})"
            lines.append(f"- PID {pid}: {status}")

        return ToolResult(output="\n".join(lines))

    def cleanup(self):
        """Clean up all managed processes."""
        for pid, proc in self._processes.items():
            try:
                if proc.returncode is None:
                    proc.terminate()
            except Exception:
                pass
        self._processes.clear()
