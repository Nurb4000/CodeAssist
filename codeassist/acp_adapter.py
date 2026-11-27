"""Minimal ACP (Agent Client Protocol) stdio adapter for CodeAssist.

Implements newline-delimited JSON-RPC 2.0 messages over stdin/stdout, enough for
ACP clients to initialize, create/load/list sessions, prompt, and cancel.

Protocol reference: agentclientprotocol.com (JSON-RPC 2.0, UTF-8 ndjson).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from . import server
from .config import Config
from .headless import bootstrap, resolve_agent
from .headless_run import run_prompt
from .session import Session

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
REQUEST_CANCELLED = -32800


class ACPError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass
class ACPState:
    config: Config
    initialized: bool = False
    sessions: dict[str, dict[str, Any]] = None  # acp_session_id -> info
    session_runs: dict[str, asyncio.Task] = None
    session_agents: dict[str, Any] = None
    writer: asyncio.StreamWriter | None = None
    write_lock: asyncio.Lock = None

    def __post_init__(self):
        if self.sessions is None:
            self.sessions = {}
        if self.session_runs is None:
            self.session_runs = {}
        if self.session_agents is None:
            self.session_agents = {}
        if self.write_lock is None:
            self.write_lock = asyncio.Lock()


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


async def _write(state: ACPState, obj: dict[str, Any]) -> None:
    line = _json_dumps(obj) + "\n"
    data = line.encode("utf-8")
    if state.writer is not None:
        async with state.write_lock:
            state.writer.write(data)
            await state.writer.drain()
    else:
        # fallback: stdout
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


async def _notify(state: ACPState, method: str, params: dict[str, Any]) -> None:
    await _write(state, {"jsonrpc": "2.0", "method": method, "params": params})


async def _respond(state: ACPState, req_id: Any, result: Any) -> None:
    await _write(state, {"jsonrpc": "2.0", "id": req_id, "result": result})


async def _respond_error(state: ACPState, req_id: Any, code: int, message: str, data: Any = None) -> None:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    await _write(state, {"jsonrpc": "2.0", "id": req_id, "error": err})


def _text_from_prompt(prompt: Any) -> tuple[str, list[dict]]:
    """Extract text and attachment-like items from ACP prompt content blocks."""
    if isinstance(prompt, str):
        return prompt, []
    if not isinstance(prompt, list):
        return str(prompt), []

    texts: list[str] = []
    attachments: list[dict] = []
    for block in prompt:
        if not isinstance(block, dict):
            texts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(block.get("text", ""))
        elif btype == "resource_link":
            uri = block.get("uri", "")
            name = block.get("name") or uri
            texts.append(f"[resource: {name} {uri}]".strip())
        elif btype == "resource":
            res = block.get("resource") or {}
            uri = res.get("uri", "")
            text = res.get("text")
            if text:
                name = block.get("name") or uri or "resource"
                attachments.append({"name": name, "content": text, "type": res.get("mimeType") or "text/plain"})
            else:
                texts.append(f"[resource: {uri}]".strip())
        elif btype == "image":
            data = block.get("data")
            mime = block.get("mimeType") or "image/png"
            if data:
                attachments.append({"name": block.get("name") or "image", "content": data, "type": mime, "encoding": "base64"})
        else:
            # unknown block; preserve as JSON-ish text to avoid dropping user intent
            texts.append(block.get("text") or json.dumps(block, ensure_ascii=False))
    return "\n".join(t for t in texts if t is not None), attachments


async def _ensure_session(state: ACPState, acp_session_id: str | None, cwd: str | None = None) -> str:
    if acp_session_id and acp_session_id in state.sessions:
        return acp_session_id
    # Create backing CodeAssist session
    session = await Session.create(name=f"ACP {cwd or ''}".strip() or None)
    sid = acp_session_id or f"acp_{session.id}"
    state.sessions[sid] = {
        "id": sid,
        "sessionId": session.id,
        "cwd": cwd,
        "created": True,
    }
    return sid


async def _handle_initialize(state: ACPState, params: dict[str, Any]) -> dict[str, Any]:
    state.initialized = True
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "agentCapabilities": {
            "loadSession": True,
            "promptCapabilities": {"image": True, "embeddedContext": True},
        },
        "agentInfo": {"name": "CodeAssist", "version": "1.0.0"},
        "authMethods": [],
    }


async def _handle_session_new(state: ACPState, params: dict[str, Any]) -> dict[str, Any]:
    cwd = params.get("cwd")
    acp_sid = await _ensure_session(state, None, cwd=cwd)
    info = state.sessions[acp_sid]
    # mode/agent selection: accept but keep simple
    agent = params.get("modeId") or params.get("agent")
    if agent:
        info["agent"] = agent
    return {"sessionId": acp_sid}


async def _handle_session_load(state: ACPState, params: dict[str, Any]) -> dict[str, Any]:
    acp_sid = params.get("sessionId")
    if not acp_sid:
        raise ACPError(INVALID_PARAMS, "sessionId is required")
    if acp_sid not in state.sessions:
        # try to map acp_<codeassist_id>
        if acp_sid.startswith("acp_"):
            csid = acp_sid[4:]
            try:
                row = await Session.get(csid)
                if row:
                    state.sessions[acp_sid] = {"id": acp_sid, "sessionId": csid, "cwd": params.get("cwd"), "created": False}
            except Exception:
                pass
    if acp_sid not in state.sessions:
        raise ACPError(INVALID_PARAMS, f"Unknown sessionId: {acp_sid}")
    return {}


async def _handle_session_list(state: ACPState, params: dict[str, Any]) -> dict[str, Any]:
    rows = await Session.list_all()
    sessions = []
    for r in rows:
        sid = r.get("id") if isinstance(r, dict) else None
        if not sid:
            continue
        acp_sid = f"acp_{sid}"
        state.sessions.setdefault(acp_sid, {"id": acp_sid, "sessionId": sid, "cwd": None, "created": False})
        sessions.append({"sessionId": acp_sid, "title": r.get("name") or r.get("title")})
    return {"sessions": sessions}


async def _session_prompt(state: ACPState, req_id: Any, params: dict[str, Any]) -> None:
    acp_sid = params.get("sessionId")
    if not acp_sid or acp_sid not in state.sessions:
        raise ACPError(INVALID_PARAMS, "Unknown sessionId")
    prompt = params.get("prompt")
    text, attachments = _text_from_prompt(prompt)
    info = state.sessions[acp_sid]
    session_id = info["sessionId"]
    agent_name = info.get("agent")

    cancelled = {"value": False}
    acc = {"text": ""}

    async def sink(event):
        et = event.type
        if et == "text_delta":
            chunk = event.data.get("content", "")
            acc["text"] += chunk
            await _notify(state, "session/update", {
                "sessionId": acp_sid,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": chunk},
                },
            })
        elif et == "reasoning":
            chunk = event.data.get("content", "")
            if chunk:
                await _notify(state, "session/update", {
                    "sessionId": acp_sid,
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": chunk},
                    },
                })
        elif et == "tool_call":
            await _notify(state, "session/update", {
                "sessionId": acp_sid,
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": event.data.get("id"),
                    "title": event.data.get("name"),
                    "kind": "other",
                    "status": "in_progress",
                    "rawInput": event.data.get("arguments"),
                },
            })
        elif et == "tool_result":
            status = "completed"
            out = event.data.get("output")
            if isinstance(out, str) and out.lower().startswith(("error", "denied", "tool '")):
                # heuristic; keep simple
                pass
            await _notify(state, "session/update", {
                "sessionId": acp_sid,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": event.data.get("id"),
                    "status": status,
                    "rawOutput": out,
                    "content": [{"type": "content", "content": {"type": "text", "text": str(out)[:20000]}}] if out is not None else [],
                },
            })

    agent_out: list = []

    async def run_it():
        try:
            result = await run_prompt(
                session_id=session_id,
                message=text,
                attachments=attachments,
                agent_name=agent_name,
                sink=sink,
                agent_out=agent_out,
            )
            stop = "cancelled" if cancelled["value"] else result.stop_reason
            if stop == "error":
                stop = "end_turn"
            # ACP stopReason values: end_turn, max_tokens, max_turn_requests, refusal, cancelled
            if stop not in {"end_turn", "cancelled", "max_tokens", "max_turn_requests", "refusal"}:
                stop = "end_turn"
            await _respond(state, req_id, {"stopReason": stop})
        except asyncio.CancelledError:
            cancelled["value"] = True
            await _respond(state, req_id, {"stopReason": "cancelled"})
        except Exception as e:
            log.exception("ACP prompt failed")
            await _respond_error(state, req_id, INTERNAL_ERROR, str(e))
        finally:
            state.session_runs.pop(acp_sid, None)
            state.session_agents.pop(acp_sid, None)

    task = asyncio.create_task(run_it())
    state.session_runs[acp_sid] = task
    state.session_agents[acp_sid] = agent_out


async def _handle_session_cancel(state: ACPState, params: dict[str, Any]) -> None:
    acp_sid = params.get("sessionId")
    agent_out = state.session_agents.get(acp_sid)
    if agent_out and agent_out:
        agent = agent_out[0]
        if hasattr(agent, "cancel"):
            agent.cancel()
    task = state.session_runs.get(acp_sid)
    if task and not task.done():
        task.cancel()


async def handle_message(state: ACPState, msg: dict[str, Any]) -> None:
    if msg.get("jsonrpc") != "2.0":
        # ignore or error; JSON-RPC requires version
        if "id" in msg:
            await _respond_error(state, msg.get("id"), INVALID_REQUEST, "Invalid JSON-RPC version")
        return

    has_method = "method" in msg
    has_id = "id" in msg

    if not has_method:
        # response from client; ignore for now
        return

    method = msg.get("method")
    params = msg.get("params") or {}
    req_id = msg.get("id") if has_id else None

    try:
        if method == "initialize":
            result = await _handle_initialize(state, params)
            await _respond(state, req_id, result)
        elif method == "session/new":
            result = await _handle_session_new(state, params)
            await _respond(state, req_id, result)
        elif method == "session/load":
            result = await _handle_session_load(state, params)
            await _respond(state, req_id, result)
        elif method == "session/list":
            result = await _handle_session_list(state, params)
            await _respond(state, req_id, result)
        elif method == "session/prompt":
            if req_id is None:
                raise ACPError(INVALID_REQUEST, "session/prompt must be a request")
            await _session_prompt(state, req_id, params)
        elif method == "session/cancel":
            await _handle_session_cancel(state, params)
            # notification; no response
        elif method in {"shutdown", "exit"}:
            if has_id:
                await _respond(state, req_id, {})
        else:
            if has_id:
                await _respond_error(state, req_id, METHOD_NOT_FOUND, f"Method not found: {method}")
    except ACPError as e:
        if has_id:
            await _respond_error(state, req_id, e.code, e.message, e.data)
    except Exception as e:
        log.exception("ACP handler error")
        if has_id:
            await _respond_error(state, req_id, INTERNAL_ERROR, str(e))


async def serve_stdio(config: Config) -> None:
    """Run ACP adapter over stdio (ndjson)."""
    await bootstrap(config)
    state = ACPState(config=config)

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
    writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)
    state.writer = writer

    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except Exception:
                # parse error; cannot know id reliably
                await _respond_error(state, None, PARSE_ERROR, "Parse error")
                continue
            if isinstance(msg, list):
                for item in msg:
                    if isinstance(item, dict):
                        await handle_message(state, item)
            elif isinstance(msg, dict):
                await handle_message(state, msg)
            else:
                await _respond_error(state, None, INVALID_REQUEST, "Invalid request")
    finally:
        # cancel running prompts
        for task in list(state.session_runs.values()):
            task.cancel()
        for task in list(state.session_runs.values()):
            try:
                await task
            except Exception:
                pass
        if server.mcp_client:
            try:
                await server.mcp_client.close()
            except Exception:
                pass
