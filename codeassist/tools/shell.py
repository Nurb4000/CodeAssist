import asyncio
import logging
import os
import signal
import time
from pathlib import Path

from . import Tool, ToolResult
from .security import WorkspaceViolationError, validate_directory

log = logging.getLogger(__name__)


class ShellTool(Tool):
    name = "shell"
    description = "Execute a shell command. Returns stdout and stderr. Use workdir to set the working directory."
    workspace = Path(".")
    timeout: int = 120
    max_output_chars: int = 20000

    # Read chunk and per-stream memory ceiling. Output is streamed in chunks and
    # stopped once a stream exceeds MAX_OUTPUT_BUFFER_BYTES, so a command that
    # emits gigabytes (e.g. `yes`, `dd`) cannot exhaust RAM buffering it all
    # before display truncation (re-scan M6). Well above max_output_chars, so
    # ordinary command output is never cut off by this ceiling.
    _READ_CHUNK_SIZE = 65536
    MAX_OUTPUT_BUFFER_BYTES = 10 * 1024 * 1024  # 10 MiB per stream

    # SECURITY NOTE: This tool uses subprocess_shell which passes the command
    # string to /bin/sh -c. Mitigations in place:
    #   1. User confirmation required by default (see agent.py CONFIRM_TOOLS)
    #   2. Arity-based approval reduction: read-only commands (ls, cat, grep,
    #      git diff, etc.) auto-approve; destructive commands (rm, chmod, mv)
    #      and unknown commands still require confirmation (agent.py:103-145)
    #   3. Working directory validated to stay within workspace boundary
    #   4. Configurable timeout prevents runaway processes
    # For an internal-only deployment this is acceptable. If exposing externally,
    # consider switching to subprocess_exec with argument lists.

    parameters = {  # noqa: RUF012
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
        except Exception:  # noqa: BLE001
            return ToolResult(output=f"Error: workdir does not exist: {cwd_str}", error=True)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd_path,
                start_new_session=True,
            )
            # Read both pipes concurrently (like communicate()) but with a hard
            # per-stream memory budget so a command that emits gigabytes cannot
            # exhaust RAM before we truncate for display (re-scan M6). As soon as
            # one stream exceeds the budget the other reader is cancelled, so we
            # never wait on a silent sibling pipe.
            stdout_bytes, stdout_truncated, stderr_bytes, stderr_truncated = (
                await self._read_both(proc, timeout)
            )
        except TimeoutError:
            # `command` runs under /bin/sh -c; a backgrounded grandchild inherits
            # the shell's process group. proc.kill() only reaps the shell and
            # orphans the grandchild, which then runs for the full original
            # lifetime. Putting the child in its own session and killing the
            # whole group on timeout reaps grandchildren too.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            return ToolResult(output=f"Error: command timed out after {timeout}s", error=True)
        except Exception as e:  # noqa: BLE001
            return ToolResult(output=f"Error executing command: {e}", error=True)

        # A stream that hit its memory budget means the process is still running
        # and producing output; stop it rather than leave a runaway command going.
        if stdout_truncated or stderr_truncated:
            self._kill_process_group(proc)

        stdout_str = stdout_bytes.decode(errors="replace").strip()
        stderr_str = stderr_bytes.decode(errors="replace").strip()

        parts = []
        if stdout_str:
            parts.append(f"STDOUT:\n{stdout_str}")
        if stderr_str:
            parts.append(f"STDERR:\n{stderr_str}")
        if stdout_truncated or stderr_truncated:
            parts.append("Output truncated: exceeded the tool's memory safety buffer.")
        elif proc.returncode is not None:
            parts.append(f"\nExit code: {proc.returncode}")

        output = "\n\n".join(parts) if parts else "(no output)"

        if len(output) > self.max_output_chars:
            half = self.max_output_chars // 2
            output = output[:half] + "\n\n... (truncated) ...\n\n" + output[-half:]

        return ToolResult(
            output=output,
            error=(proc.returncode != 0) or stdout_truncated or stderr_truncated,
        )

    @staticmethod
    def _kill_process_group(proc):
        """SIGKILL the command's process group (reaps backgrounded grandchildren)."""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def _read_budgeted(self, stream):
        """Read a subprocess pipe with a hard memory budget.

        Returns ``(bytes, truncated)``. Reads in fixed-size chunks; as soon as
        combined output exceeds ``MAX_OUTPUT_BUFFER_BYTES`` it returns
        ``truncated=True`` (the caller cancels the sibling reader). A normal EOF
        returns ``truncated=False``. Bounds memory so a command emitting
        gigabytes (e.g. ``yes``, ``dd``) cannot exhaust RAM before display
        truncation (re-scan M6).
        """
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await stream.read(self._READ_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > self.MAX_OUTPUT_BUFFER_BYTES:
                overflow = total - self.MAX_OUTPUT_BUFFER_BYTES
                last = chunks[-1]
                chunks[-1] = last[:-overflow] if overflow else last
                return b"".join(chunks), True
        return b"".join(chunks), False

    async def _read_both(self, proc, timeout):
        """Read stdout and stderr concurrently under a per-stream memory budget.

        Returns ``(stdout_bytes, stdout_truncated, stderr_bytes, stderr_truncated)``.
        The first reader to finish (EOF or budget) unblocks us; if it hit its
        budget the sibling is cancelled so a runaway command on one pipe never
        leaves the other blocked waiting. Raises ``TimeoutError`` if neither
        completes within ``timeout``.
        """
        start = time.monotonic()

        def _done(task):
            """Reader result, mapping cancellation/exception to a truncated read."""
            if task.cancelled():
                return b"", True
            try:
                return task.result()
            except Exception:  # noqa: BLE001
                return b"", True

        stdout_task = asyncio.create_task(self._read_budgeted(proc.stdout))
        stderr_task = asyncio.create_task(self._read_budgeted(proc.stderr))
        try:
            completed, pending = await asyncio.wait(
                {stdout_task, stderr_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            stdout_task.cancel()
            stderr_task.cancel()
            raise

        pending = list(pending)
        for task in completed:
            if _done(task)[1]:
                for p in pending:
                    p.cancel()
                break
        else:
            # First reader hit EOF (not budget): give the sibling a bounded
            # chance to finish on its own before we time out.
            if pending:
                remaining = timeout - (time.monotonic() - start)
                try:
                    await asyncio.wait_for(pending[0], timeout=max(remaining, 0))
                except (TimeoutError, asyncio.TimeoutError):
                    pending[0].cancel()

        return (
            _done(stdout_task)[0],
            _done(stdout_task)[1],
            _done(stderr_task)[0],
            _done(stderr_task)[1],
        )
