"""Confined JavaScript code-mode execution tool.

Runs model-authored JavaScript in a Node.js vm context. The program can call
CodeAssist tools through ``tools.<name>(args)`` but has no direct filesystem,
network, process, import, or timer access. Tool calls are bridged over a JSONL
stdio protocol to the host Python process, where they execute through the normal
tool registry/permission path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import Tool, ToolResult

log = logging.getLogger(__name__)

# Hard caps. These are defensive defaults for the initial D1 slice; they keep a
# runaway program from burning CPU, tool budget, or transcript space.
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 300_000
MAX_TOOL_CALLS = 64
MAX_OUTPUT_CHARS = 20_000
MAX_CODE_CHARS = 100_000

# Tools that must not be callable from inside code mode. `execute` would recurse;
# `question`/confirm-like interactive flows are not bridged by this runtime.
EXCLUDED_TOOLS = {"execute", "question"}


_HARNESS_JS = r"""
'use strict';
const vm = require('node:vm');
const readline = require('node:readline');

const MAX_PENDING = 4096;
let nextId = 1;
const pending = new Map();
let toolCalls = 0;
const MAX_TOOL_CALLS = Number(process.env.CODEMODE_MAX_TOOL_CALLS || 64);
let settled = false;

function send(msg) {
  try { process.stdout.write(JSON.stringify(msg) + '\n'); } catch (_) {}
}

function fail(message) {
  if (settled) return;
  settled = true;
  send({ type: 'result', ok: false, error: String(message || 'Code mode error') });
  setImmediate(() => process.exit(1));
}

process.on('uncaughtException', (e) => fail(e && e.stack || e));
process.on('unhandledRejection', (e) => fail(e && e.stack || e));

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', (line) => {
  let msg;
  try { msg = JSON.parse(line); } catch (_) { return; }
  if (msg.type === 'tool_result') {
    const entry = pending.get(msg.id);
    if (!entry) return;
    pending.delete(msg.id);
    if (msg.error) entry.reject(new Error(String(msg.error)));
    else entry.resolve(msg.output);
  }
});

function callTool(name, input) {
  if (settled) return Promise.reject(new Error('Execution finished'));
  toolCalls += 1;
  if (toolCalls > MAX_TOOL_CALLS) {
    return Promise.reject(new Error(`Tool call limit exceeded (${MAX_TOOL_CALLS})`));
  }
  const id = nextId++;
  send({ type: 'tool_call', id, name: String(name), input: input === undefined ? {} : input });
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    if (pending.size > MAX_PENDING) {
      pending.delete(id);
      reject(new Error('Too many pending tool calls'));
    }
  });
}

function makeTools(names) {
  const root = Object.create(null);
  for (const full of names) {
    const parts = String(full).split('.').filter(Boolean);
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const last = i === parts.length - 1;
      if (last) {
        const fn = (input) => callTool(full, input);
        // Expose both dotted leaf and full path behavior; leaf wins for plain names.
        node[part] = fn;
        // Also expose bracket-safe callable on root via path helper below.
      } else {
        if (!node[part] || typeof node[part] !== 'object') node[part] = Object.create(null);
        node = node[part];
      }
    }
  }
  // Provide a path-based fallback: tools.call("name", input) is not needed, but
  // preserve exact full names as properties for bracket access on root.
  for (const full of names) {
    if (!Object.prototype.hasOwnProperty.call(root, full)) {
      root[full] = (input) => callTool(full, input);
    }
  }
  return root;
}

async function main() {
  let input = '';
  process.stdin.setEncoding('utf8');
  // We already attached readline; collect initial config from first message.
  // The host sends one JSON config line before code execution starts.
}

const configLinePromise = new Promise((resolve) => {
  const onLine = (line) => {
    rl.off('line', onLine);
    // Re-attach normal handler for tool results.
    rl.on('line', (line2) => {
      let msg;
      try { msg = JSON.parse(line2); } catch (_) { return; }
      if (msg.type === 'tool_result') {
        const entry = pending.get(msg.id);
        if (!entry) return;
        pending.delete(msg.id);
        if (msg.error) entry.reject(new Error(String(msg.error)));
        else entry.resolve(msg.output);
      }
    });
    try { resolve(JSON.parse(line)); } catch (e) { fail(e); }
  };
  rl.on('line', onLine);
});

(async () => {
  const cfg = await configLinePromise;
  const code = String(cfg.code || '');
  const toolNames = Array.isArray(cfg.tools) ? cfg.tools : [];
  const tools = makeTools(toolNames);

  // Prevent recursive/forbidden tool access even if code probes.
  tools.execute = (input) => callTool('execute', input);

  const context = vm.createContext(Object.create(null));
  // Minimal safe globals.
  context.globalThis = context;
  context.tools = tools;
  context.console = {
    log: (...args) => send({ type: 'log', level: 'log', text: args.map(String).join(' ') }),
    error: (...args) => send({ type: 'log', level: 'error', text: args.map(String).join(' ') }),
    warn: (...args) => send({ type: 'log', level: 'warn', text: args.map(String).join(' ') }),
  };
  context.JSON = JSON;
  context.Math = Math;
  context.Date = Date;
  context.Array = Array;
  context.Object = Object;
  context.String = String;
  context.Number = Number;
  context.Boolean = Boolean;
  context.RegExp = RegExp;
  context.Error = Error;
  context.TypeError = TypeError;
  context.RangeError = RangeError;
  context.Map = Map;
  context.Set = Set;
  context.WeakMap = WeakMap;
  context.WeakSet = WeakSet;
  context.Promise = Promise;
  context.Symbol = Symbol;
  context.BigInt = BigInt;
  context.parseInt = parseInt;
  context.parseFloat = parseFloat;
  context.isNaN = isNaN;
  context.isFinite = isFinite;
  context.encodeURIComponent = encodeURIComponent;
  context.decodeURIComponent = decodeURIComponent;
  context.encodeURI = encodeURI;
  context.decodeURI = decodeURI;
  context.atob = typeof atob === 'function' ? atob : undefined;
  context.btoa = typeof btoa === 'function' ? btoa : undefined;
  context.TextEncoder = typeof TextEncoder !== 'undefined' ? TextEncoder : undefined;
  context.TextDecoder = typeof TextDecoder !== 'undefined' ? TextDecoder : undefined;
  context.URL = typeof URL !== 'undefined' ? URL : undefined;
  context.URLSearchParams = typeof URLSearchParams !== 'undefined' ? URLSearchParams : undefined;
  context.structuredClone = typeof structuredClone === 'function' ? structuredClone : undefined;
  context.fetch = undefined;
  context.process = undefined;
  context.require = undefined;
  context.module = undefined;
  context.exports = undefined;
  context.Buffer = undefined;
  context.setTimeout = undefined;
  context.setInterval = undefined;
  context.setImmediate = undefined;

  const wrapped = `(async () => {\n${code}\n})()`;
  let value;
  try {
    const script = new vm.Script(wrapped, { filename: 'codemode.js' });
    value = script.runInContext(context, { timeout: Number(cfg.timeoutMs || 120000) });
  } catch (e) {
    fail(e && e.stack || e);
    return;
  }
  try {
    const result = await value;
    if (settled) return;
    settled = true;
    send({ type: 'result', ok: true, value: result === undefined ? null : result });
    setImmediate(() => process.exit(0));
  } catch (e) {
    fail(e && e.stack || e);
  }
})();
"""


class ExecuteTool(Tool):
    name = "execute"
    description = (
        "Run JavaScript in a confined Code Mode runtime to script tool calls and "
        "compose their results. Use plain JavaScript only (no code fences/wrappers). "
        "Call tools via `await tools.<name>({ ... })` using exact tool names from the "
        "available catalog. `fetch`, imports, filesystem, process, and timers are "
        "unavailable. Prefer an explicit `return`. Await calls whose completion matters; "
        "independent calls can use Promise.all/Promise.allSettled."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "JavaScript code to execute in the confined runtime.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": f"Optional timeout in milliseconds (default {DEFAULT_TIMEOUT_MS}, max {MAX_TIMEOUT_MS}).",
            },
        },
        "required": ["code"],
    }

    def __init__(self):
        self.workspace: Path | None = None
        self._registry = None
        self._executor: Callable[[str, dict[str, Any]], Awaitable[ToolResult]] | None = None

    def set_registry(self, registry) -> None:
        self._registry = registry

    def set_executor(self, executor: Callable[[str, dict[str, Any]], Awaitable[ToolResult]] | None) -> None:
        """Optional host executor; when set it must enforce permissions/policy."""
        self._executor = executor

    def apply_config(self, config) -> None:
        cm = getattr(getattr(config, "tools", None), "codemode", None)
        if not cm:
            return
        self.enabled = bool(getattr(cm, "enabled", True))
        self.timeout_ms = max(1, int(getattr(cm, "timeout_ms", DEFAULT_TIMEOUT_MS)))
        self.max_tool_calls = max(1, int(getattr(cm, "max_tool_calls", MAX_TOOL_CALLS)))
        self.max_output_chars = max(1000, int(getattr(cm, "max_output_chars", MAX_OUTPUT_CHARS)))

    def _available_tools(self) -> list[str]:
        if not self._registry:
            return []
        names = []
        for name in self._registry.list_names():
            if name in EXCLUDED_TOOLS:
                continue
            names.append(name)
        return sorted(names)

    async def _execute_tool_call(self, name: str, arguments: Any) -> ToolResult:
        if name in EXCLUDED_TOOLS:
            return ToolResult(output=f"Tool '{name}' is not available inside execute", error=True)
        if not isinstance(arguments, dict):
            return ToolResult(output=f"Tool '{name}' arguments must be an object", error=True)
        if self._executor is not None:
            return await self._executor(name, arguments)
        if self._registry is None:
            return ToolResult(output="Code mode runtime is not configured", error=True)
        return await self._registry.execute(name, arguments)

    async def execute(self, code: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ToolResult:
        if not getattr(self, "enabled", True):
            return ToolResult(output="Error: code mode is disabled by configuration", error=True)
        if not isinstance(code, str) or not code.strip():
            return ToolResult(output="Error: code is required", error=True)
        if len(code) > MAX_CODE_CHARS:
            return ToolResult(output=f"Error: code exceeds {MAX_CODE_CHARS} characters", error=True)

        default_timeout = int(getattr(self, "timeout_ms", DEFAULT_TIMEOUT_MS))
        max_tool_calls = int(getattr(self, "max_tool_calls", MAX_TOOL_CALLS))
        max_output_chars = int(getattr(self, "max_output_chars", MAX_OUTPUT_CHARS))
        try:
            timeout_ms = int(timeout_ms)
        except Exception:
            timeout_ms = default_timeout
        timeout_ms = max(1, min(timeout_ms, MAX_TIMEOUT_MS))

        node = shutil.which("node")
        if not node:
            return ToolResult(output="Error: Node.js is required for execute tool but was not found", error=True)

        available = self._available_tools()
        if not available:
            return ToolResult(output="Error: no tools available for code mode execution", error=True)

        harness_path = None
        logs: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".codemode.cjs", delete=False, encoding="utf-8") as f:
                f.write(_HARNESS_JS)
                harness_path = f.name

            env = os.environ.copy()
            env["CODEMODE_MAX_TOOL_CALLS"] = str(max_tool_calls)
            proc = await asyncio.create_subprocess_exec(
                node,
                harness_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(self.workspace or Path.cwd()),
            )
            assert proc.stdin is not None and proc.stdout is not None

            config = json.dumps({"code": code, "tools": available, "timeoutMs": timeout_ms})
            proc.stdin.write((config + "\n").encode("utf-8"))
            await proc.stdin.drain()

            result_msg: dict[str, Any] | None = None
            deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000) + 5

            async def read_stdout():
                nonlocal result_msg
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        return
                    try:
                        msg = json.loads(line.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                    mtype = msg.get("type")
                    if mtype == "tool_call":
                        call_id = msg.get("id")
                        name = str(msg.get("name", ""))
                        args = msg.get("input", {})
                        entry = {"id": call_id, "name": name, "status": "running"}
                        tool_calls.append(entry)
                        try:
                            tr = await self._execute_tool_call(name, args)
                            entry["status"] = "error" if tr.error else "completed"
                            payload = {
                                "type": "tool_result",
                                "id": call_id,
                                "output": tr.output,
                                "error": tr.output if tr.error else None,
                            }
                        except Exception as e:  # noqa: BLE001
                            entry["status"] = "error"
                            payload = {"type": "tool_result", "id": call_id, "output": "", "error": str(e)}
                        if proc.stdin and not proc.stdin.is_closing():
                            proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                            await proc.stdin.drain()
                    elif mtype == "log":
                        text = str(msg.get("text", ""))
                        if text:
                            logs.append(f"[{msg.get('level', 'log')}] {text}")
                    elif mtype == "result":
                        result_msg = msg
                        return

            try:
                await asyncio.wait_for(read_stdout(), timeout=max(1, deadline - asyncio.get_running_loop().time()))
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(output=f"Error: execute timed out after {timeout_ms}ms", error=True)

            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

            stderr = b""
            if proc.stderr:
                try:
                    stderr = await asyncio.wait_for(proc.stderr.read(), timeout=1)
                except Exception:
                    stderr = b""

            if result_msg is None:
                err = stderr.decode("utf-8", errors="replace").strip()
                return ToolResult(output=f"Error: execute failed{': ' + err if err else ''}", error=True)

            if not result_msg.get("ok"):
                err = str(result_msg.get("error", "Unknown code mode error"))
                out = err
                if logs:
                    out += "\n\nLogs:\n" + "\n".join(logs[-20:])
                return ToolResult(output=out[:max_output_chars], error=True)

            value = result_msg.get("value")
            if isinstance(value, str):
                out = value
            else:
                try:
                    out = json.dumps(value, ensure_ascii=False)
                except Exception:
                    out = str(value)
            if logs:
                out = (out + "\n\nLogs:\n" + "\n".join(logs[-20:])).strip()
            if len(out) > max_output_chars:
                out = out[:max_output_chars] + "\n...[truncated]"
            return ToolResult(output=out or "null")
        finally:
            if harness_path:
                try:
                    os.unlink(harness_path)
                except OSError:
                    pass
