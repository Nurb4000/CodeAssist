"""Persistent PTY sessions for interactive terminal workflows."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import fcntl
import json
import logging
import os
import pty
import signal
import struct
import termios
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import Tool, ToolResult

log = logging.getLogger(__name__)


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
    created_at: float = field(default_factory=time.time)
    session_id: str | None = None

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
        self._current_session_id: str | None = None
        self._event_sinks: dict[str, object] = {}  # session_id -> async callable
        self._read_tasks: dict[str, asyncio.Task] = {}
        self._state_path: Path | None = None

    def bind_session(self, session_id: str | None):
        self._current_session_id = session_id

    def set_event_sink(self, sink, session_id: str | None = None):
        sid = session_id or self._current_session_id
        if sid and sink is not None:
            self._event_sinks[sid] = sink

    def clear_event_sink(self, session_id: str | None = None):
        sid = session_id or self._current_session_id
        if sid:
            self._event_sinks.pop(sid, None)

    def set_state_path(self, path: Path | None):
        self._state_path = Path(path) if path else None

    def _buffer_path(self, pty_id: str) -> Path | None:
        if not self._state_path:
            return None
        return self._state_path.parent / "pty_buffers" / f"{pty_id}.log"

    def _persist_state(self):
        if not self._state_path:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"sessions": self.get_session_state()}
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._state_path)
            # persist output buffers for recovery/inspection
            buf_dir = self._state_path.parent / "pty_buffers"
            buf_dir.mkdir(parents=True, exist_ok=True)
            for s in self._sessions.values():
                bp = buf_dir / f"{s.id}.log"
                try:
                    bp.write_bytes(bytes(s.output))
                except Exception:  # noqa: BLE001
                    log.debug("Failed to persist PTY buffer %s", s.id, exc_info=True)
        except Exception:  # noqa: BLE001
            log.debug("Failed to persist PTY state", exc_info=True)

    def load_persisted_state(self):
        """Load persisted PTY metadata after restart.

        Live processes cannot be recovered across process restart; mark them as
        exited/lost so UI/API can report accurate state. Output buffers are
        retained on disk for inspection.
        """
        if not self._state_path or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.debug("Failed to load PTY state", exc_info=True)
            return
        sessions = data.get("sessions", []) if isinstance(data, dict) else []
        changed = False
        for s in sessions:
            if not isinstance(s, dict):
                continue
            if s.get("alive"):
                s["alive"] = False
                s["returncode"] = s.get("returncode")
                s["recovered"] = False
                s["lost_on_restart"] = True
                changed = True
            # Attach buffer preview path metadata if present.
            bp = self._buffer_path(str(s.get("id", "")))
            if bp and bp.exists():
                s["buffer_path"] = str(bp)
                try:
                    s["buffer_chars"] = bp.stat().st_size
                except Exception:  # noqa: BLE001
                    pass
        if changed:
            try:
                self._state_path.write_text(json.dumps({"sessions": sessions}, indent=2), encoding="utf-8")
            except Exception:  # noqa: BLE001
                log.debug("Failed to update PTY state", exc_info=True)
        self._persisted_sessions = sessions

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

    async def _emit(self, session: PTYSession, event: str, **data):
        payload = {
            "type": "pty_output",
            "event": event,
            "pty_id": session.id,
            "session_id": session.session_id,
            "command": session.command,
            "cwd": session.cwd,
            "alive": session.alive,
            "returncode": session.proc.returncode,
            **data,
        }
        targets = []
        if session.session_id and session.session_id in self._event_sinks:
            targets.append(self._event_sinks[session.session_id])
        elif self._event_sinks:
            # Fallback: no session-bound sink found; broadcast to all sinks.
            targets = list(self._event_sinks.values())
        for sink in targets:
            try:
                res = sink(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001
                log.debug("PTY event sink failed", exc_info=True)

    def _drain(self, session: PTYSession) -> str:
        fd = session.master_fd
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
            session.output.extend(chunk)
            if len(session.output) > session.max_buffer:
                drop = len(session.output) - session.max_buffer
                del session.output[:drop]
                session.read_offset = max(0, session.read_offset - drop)
        if not chunks:
            return ""
        return b"".join(chunks).decode("utf-8", errors="replace")

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
            session_id=self._current_session_id,
        )
        self._sessions[session.id] = session
        self._ensure_reader(session)
        self._persist_state()
        await asyncio.sleep(0.05)
        initial = self._drain(session)
        await self._emit(session, "created", output=initial)
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
        out = self._drain(session)
        if out:
            await self._emit(session, "output", output=out)
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
        out = self._drain(session)
        if out:
            await self._emit(session, "output", output=out)
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
                out = self._drain(session)
                if out and bytes(session.output[session.read_offset:]):
                    # keep in buffer; _read will emit/return
                    pass
                if bytes(session.output[session.read_offset:]) or not session.alive:
                    if not fut.done():
                        fut.set_result(None)
                    break
            if not fut.done():
                await fut
        finally:
            with contextlib.suppress(Exception):
                loop.remove_reader(fd)

    def _ensure_reader(self, session: PTYSession) -> None:
        if session.id in self._read_tasks and not self._read_tasks[session.id].done():
            return
        self._read_tasks[session.id] = asyncio.create_task(self._reader_loop(session))

    async def _reader_loop(self, session: PTYSession) -> None:
        try:
            while session.alive:
                try:
                    await self._wait_readable(session)
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(0.05)
                out = self._drain(session)
                if out:
                    await self._emit(session, "output", output=out)
                if not session.alive:
                    break
            # final drain
            out = self._drain(session)
            if out:
                await self._emit(session, "output", output=out)
            await self._emit(session, "exit")
        finally:
            self._read_tasks.pop(session.id, None)
            self._persist_state()

    def _list(self) -> ToolResult:
        sessions = list(self._sessions.values())
        persisted = getattr(self, "_persisted_sessions", [])
        live_ids = {s.id for s in sessions}
        for s in persisted:
            if isinstance(s, dict) and s.get("id") not in live_ids:
                sessions.append(s)
        if not sessions:
            return ToolResult(output="No PTY sessions.")
        lines = ["**PTY Sessions:**", ""]
        for s in sessions:
            if isinstance(s, dict):
                status = "running" if s.get("alive") else f"exited ({s.get('returncode')})"
                suffix = " [recovered metadata only; process lost on restart]" if s.get("lost_on_restart") else ""
                buf = f" buffer={s.get('buffer_chars','?')}B" if s.get('buffer_path') else ""
                lines.append(f"- `{s.get('id')}` status={status}{suffix}{buf} cwd=`{s.get('cwd','')}` cmd=`{s.get('command','')}`")
            else:
                status = "running" if s.alive else f"exited ({s.proc.returncode})"
                lines.append(f"- `{s.id}` pid={s.proc.pid} status={status} cwd=`{s.cwd}` cmd=`{s.command}`")
        return ToolResult(output="\n".join(lines))

    async def _kill(self, kwargs: dict) -> ToolResult:
        session = self._get(kwargs.get("pty_id"))
        if not session:
            return ToolResult(output="Error: pty_id not found", error=True)
        if session.alive:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(session.proc.pid), signal.SIGTERM)
            try:
                await asyncio.wait_for(session.proc.wait(), timeout=2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(os.getpgid(session.proc.pid), signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(session.proc.wait(), timeout=2)
        task = self._read_tasks.pop(session.id, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            os.close(session.master_fd)
        del self._sessions[session.id]
        self._persist_state()
        await self._emit(session, "killed")
        return ToolResult(output=f"Killed PTY {session.id}")

    def _resize(self, kwargs: dict) -> ToolResult:
        session = self._get(kwargs.get("pty_id"))
        if not session:
            return ToolResult(output="Error: pty_id not found", error=True)
        rows = int(kwargs.get("rows") or 24)
        cols = int(kwargs.get("cols") or 80)
        self._set_winsize(session.master_fd, rows, cols)
        return ToolResult(output=f"Resized PTY {session.id} to {cols}x{rows}")

    def get_session_state(self) -> list[dict]:
        state = []
        for s in self._sessions.values():
            state.append({
                "id": s.id,
                "command": s.command,
                "cwd": s.cwd,
                "alive": s.alive,
                "returncode": s.proc.returncode,
                "created_at": s.created_at,
                "session_id": s.session_id,
            })
        return state

    def cleanup(self):
        self._persist_state()
        for task in list(self._read_tasks.values()):
            task.cancel()
        self._read_tasks.clear()
        for s in list(self._sessions.values()):
            with contextlib.suppress(Exception):
                if s.alive:
                    os.killpg(os.getpgid(s.proc.pid), signal.SIGTERM)
            with contextlib.suppress(Exception):
                os.close(s.master_fd)
        self._sessions.clear()
