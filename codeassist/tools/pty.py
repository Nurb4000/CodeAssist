"""Persistent PTY sessions for interactive terminal workflows."""

from __future__ import annotations

import asyncio
import codecs
import fcntl
import os
import pty
import signal
import struct
import termios
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import Tool, ToolResult


@dataclass
class PTYSession:
    id: str
    command: str
    cwd: str
    master_fd: int
    proc: asyncio.subprocess.Process
    output: bytearray = field(default_factory=bytearray)
    read_offset: int = 0
    max_buffer: int = 256_000
    decoder: codecs.IncrementalDecoder = field(default_factory=lambda: codecs.getincrementaldecoder("utf-8")("replace"))

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None


class PTYTool(Tool):
    name = "pty"
    description = (
        "Manage persistent pseudo-terminal sessions. Use for interactive or long-running "
        "terminal commands where you need to send input and read output across tool calls. "
        "Actions: create, write, read, list, kill, resize."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "write", "read", "list", "kill", "resize"],
                "description": "PTY action.",
            },
            "command": {"type": "string", "description": "Command to run (create)."},
            "cwd": {"type": "string", "description": "Working directory (create)."},
            "pty_id": {"type": "string", "description": "PTY session id."},
            "input": {"type": "string", "description": "Input to write (write)."},
            "enter": {"type": "boolean", "description": "Append newline after input (write, default true)."},
            "timeout_ms": {"type": "integer", "description": "Read wait timeout milliseconds (read, default 200)."},
            "max_chars": {"type": "integer", "description": "Max chars to return from read (default 8000)."},
            "rows": {"type": "integer", "description": "Terminal rows (create/resize)."},
            "cols": {"type": "integer", "description": "Terminal cols (create/resize)."},
        },
        "required": ["action"],
    }

    def __init__(self):
        self._sessions: dict[str, PTYSession] = {}

    async def execute(self, action: str, **kwargs) -> ToolResult:
        try:
            if action == "create":
                return await self._create(kwargs)
            if action == "write":
                return await self._write(kwargs)
            if action == "read":
                return await self._read(kwargs)
            if action == "list":
                return self._list()
            if action == "kill":
                return await self._kill(kwargs)
            if action == "resize":
                return self._resize(kwargs)
            return ToolResult(output=f"Unknown action: {action}", error=True)
        except Exception as e:  # noqa: BLE001
            return ToolResult(output=f"PTY error: {e}", error=True)

    def _get(self, pty_id: str | None) -> PTYSession | None:
        if not pty_id:
            return None
        return self._sessions.get(pty_id)

    @staticmethod
    def _set_winsize(fd: int, rows: int, cols: int) -> None:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def _drain(self, session: PTYSession) -> None:
        fd = session.master_fd
        while True:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk:
                break
            session.output.extend(chunk)
            if len(session.output) > session.max_buffer:
                drop = len(session.output) - session.max_buffer
                del session.output[:drop]
                session.read_offset = max(0, session.read_offset - drop)

    async def _create(self, kwargs: dict) -> ToolResult:
        command = kwargs.get("command")
        if not command:
            return ToolResult(output="Error: command is required for create", error=True)
        cwd = Path(kwargs.get("cwd") or str(self.workspace)).resolve()
        rows = int(kwargs.get("rows") or 24)
        cols = int(kwargs.get("cols") or 80)
        master_fd, slave_fd = pty.openpty()
        self._set_winsize(slave_fd, rows, cols)
        os.set_blocking(master_fd, False)

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)

        session = PTYSession(
            id=f"pty_{uuid.uuid4().hex[:12]}",
            command=command,
            cwd=str(cwd),
            master_fd=master_fd,
            proc=proc,
        )
        self._sessions[session.id] = session
        await asyncio.sleep(0.05)
        self._drain(session)
        return ToolResult(output=f"PTY created: {session.id}\nPID: {proc.pid}\nCWD: {cwd}\nCommand: {command}")

    async def _write(self, kwargs: dict) -> ToolResult:
        session = self._get(kwargs.get("pty_id"))
        if not session:
            return ToolResult(output="Error: pty_id not found", error=True)
        if not session.alive:
            return ToolResult(output=f"Error: PTY {session.id} has exited ({session.proc.returncode})", error=True)
        data = kwargs.get("input")
        if data is None:
            return ToolResult(output="Error: input is required for write", error=True)
        if kwargs.get("enter", True):
            data += "\n"
        os.write(session.master_fd, data.encode("utf-8", errors="replace"))
        await asyncio.sleep(0.05)
        self._drain(session)
        return ToolResult(output=f"Wrote {len(data)} chars to {session.id}")

    async def _read(self, kwargs: dict) -> ToolResult:
        session = self._get(kwargs.get("pty_id"))
        if not session:
            return ToolResult(output="Error: pty_id not found", error=True)
        timeout_ms = int(kwargs.get("timeout_ms") or 200)
        max_chars = int(kwargs.get("max_chars") or 8000)
        if timeout_ms > 0:
            try:
                await asyncio.wait_for(self._wait_readable(session), timeout=timeout_ms / 1000)
            except TimeoutError:
                pass
        self._drain(session)
        data = bytes(session.output[session.read_offset:])
        session.read_offset = len(session.output)
        text = session.decoder.decode(data)
        if len(text) > max_chars:
            text = text[-max_chars:]
            text = "...(truncated)...\n" + text
        status = "running" if session.alive else f"exited ({session.proc.returncode})"
        return ToolResult(output=f"PTY {session.id} [{status}]\n\n{text}")

    async def _wait_readable(self, session: PTYSession) -> None:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fd = session.master_fd

        def _on_readable():
            if not fut.done():
                fut.set_result(None)

        try:
            loop.add_reader(fd, _on_readable)
            # also wake if process exits
            while not fut.done() and session.alive:
                await asyncio.sleep(0.05)
                self._drain(session)
                if bytes(session.output[session.read_offset:]):
                    if not fut.done():
                        fut.set_result(None)
                    break
            if not fut.done():
                await fut
        finally:
            with __import__("contextlib").suppress(Exception):
                loop.remove_reader(fd)

    def _list(self) -> ToolResult:
        if not self._sessions:
            return ToolResult(output="No PTY sessions.")
        lines = ["**PTY Sessions:**", ""]
        for s in self._sessions.values():
            status = "running" if s.alive else f"exited ({s.proc.returncode})"
            lines.append(f"- `{s.id}` pid={s.proc.pid} status={status} cwd=`{s.cwd}` cmd=`{s.command}`")
        return ToolResult(output="\n".join(lines))

    async def _kill(self, kwargs: dict) -> ToolResult:
        session = self._get(kwargs.get("pty_id"))
        if not session:
            return ToolResult(output="Error: pty_id not found", error=True)
        if session.alive:
            with __import__("contextlib").suppress(ProcessLookupError):
                os.killpg(os.getpgid(session.proc.pid), signal.SIGTERM)
            try:
                await asyncio.wait_for(session.proc.wait(), timeout=2)
            except TimeoutError:
                with __import__("contextlib").suppress(ProcessLookupError):
                    os.killpg(os.getpgid(session.proc.pid), signal.SIGKILL)
                with __import__("contextlib").suppress(Exception):
                    await asyncio.wait_for(session.proc.wait(), timeout=2)
        with __import__("contextlib").suppress(Exception):
            os.close(session.master_fd)
        del self._sessions[session.id]
        return ToolResult(output=f"Killed PTY {session.id}")

    def _resize(self, kwargs: dict) -> ToolResult:
        session = self._get(kwargs.get("pty_id"))
        if not session:
            return ToolResult(output="Error: pty_id not found", error=True)
        rows = int(kwargs.get("rows") or 24)
        cols = int(kwargs.get("cols") or 80)
        self._set_winsize(session.master_fd, rows, cols)
        return ToolResult(output=f"Resized PTY {session.id} to {cols}x{rows}")

    def cleanup(self):
        for s in list(self._sessions.values()):
            with __import__("contextlib").suppress(Exception):
                if s.alive:
                    os.killpg(os.getpgid(s.proc.pid), signal.SIGTERM)
            with __import__("contextlib").suppress(Exception):
                os.close(s.master_fd)
        self._sessions.clear()
