import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from tools import Tool, ToolResult

log = logging.getLogger(__name__)


class LSPDiagnostic:
    """Represents a diagnostic from an LSP server."""

    def __init__(self, uri: str, range_data: dict, message: str, severity: int, source: str):
        self.uri = uri
        self.range = range_data
        self.message = message
        self.severity = severity
        self.source = source

    SEVERITY_ERROR = 1
    SEVERITY_WARNING = 2
    SEVERITY_INFORMATION = 3
    SEVERITY_HINT = 4

    def to_dict(self) -> dict:
        return {
            "uri": self.uri,
            "range": self.range,
            "message": self.message,
            "severity": self.severity,
            "source": self.source,
        }


class LSPClient:
    """Client for Language Server Protocol servers."""

    def __init__(self):
        self._servers: dict[str, asyncio.subprocess.Process] = {}
        self._next_id = 1
        self._responses: dict[int, asyncio.Future] = {}
        self._readers: dict[str, asyncio.Task] = {}
        self._notifications: asyncio.Queue = asyncio.Queue()

    async def start_server(self, name: str, command: str, args: list[str],
                          languages: list[str], workspace: Path):
        """Start an LSP server process."""
        try:
            proc = await asyncio.create_subprocess_exec(
                command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            self._servers[name] = proc

            # Start background reader for this server
            self._readers[name] = asyncio.create_task(
                self._read_loop(name, proc)
            )

            # Send initialize request
            init_response = await self._send_request(
                proc,
                "initialize",
                {
                    "processId": None,
                    "rootUri": workspace.as_uri(),
                    "capabilities": {
                        "textDocument": {
                            "diagnostic": {"dynamicRegistration": False},
                            "completion": {"completionItem": {"snippetSupport": True}},
                            "formatting": True,
                        },
                    },
                },
            )

            # Send initialized notification
            await self._send_notification(proc, "initialized", {})

            log.info("Started LSP server: %s for languages: %s (pid=%d)", name, languages, proc.pid)

        except Exception as e:
            log.error("Failed to start LSP server '%s': %s", name, e)
            raise

    async def _read_loop(self, name: str, proc: asyncio.subprocess.Process):
        """Background task that reads and dispatches LSP messages from stdout."""
        try:
            while True:
                header_line = await proc.stdout.readline()
                if not header_line:
                    log.info("LSP server '%s' stdout closed", name)
                    break

                header_str = header_line.decode("utf-8").strip()
                if not header_str:
                    continue

                # Parse Content-Length
                content_length = 0
                for part in header_str.split("\r\n"):
                    if part.startswith("Content-Length:"):
                        content_length = int(part.split(":")[1].strip())
                        break

                if content_length == 0:
                    log.warning("LSP server '%s': no Content-Length in header: %s", name, header_str)
                    continue

                # Read the blank line separator
                sep = await proc.stdout.readline()

                # Read the JSON-RPC body
                body = await proc.stdout.readexactly(content_length)
                message = json.loads(body.decode("utf-8"))

                # Dispatch: response (has id) or notification (no id)
                if "id" in message:
                    req_id = message["id"]
                    if req_id in self._responses:
                        self._responses[req_id].set_result(message)
                else:
                    await self._notifications.put((name, message))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("LSP reader for '%s' crashed: %s", name, e)

    async def _send_request(self, proc: asyncio.subprocess.Process, method: str, params: dict,
                           timeout: float = 10.0) -> dict:
        """Send a request and wait for the response."""
        request_id = self._next_id
        self._next_id += 1

        future = asyncio.get_event_loop().create_future()
        self._responses[request_id] = future

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        body = json.dumps(request)
        message = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
        proc.stdin.write(message.encode())
        await proc.stdin.drain()

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            if "error" in response:
                log.warning("LSP error for %s: %s", method, response["error"])
                return {}
            return response.get("result", {})
        except asyncio.TimeoutError:
            log.warning("LSP request %s timed out after %.1fs", method, timeout)
            return {}
        finally:
            self._responses.pop(request_id, None)

    async def _send_notification(self, proc: asyncio.subprocess.Process, method: str, params: dict):
        """Send a notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        body = json.dumps(notification)
        message = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
        proc.stdin.write(message.encode())
        await proc.stdin.drain()

    async def did_open(self, name: str, uri: str, language_id: str, text: str):
        """Send textDocument/didOpen notification."""
        proc = self._servers.get(name)
        if not proc:
            return
        await self._send_notification(proc, "textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": text,
            }
        })

    async def did_change(self, name: str, uri: str, text: str, version: int = 1):
        """Send textDocument/didChange notification."""
        proc = self._servers.get(name)
        if not proc:
            return
        await self._send_notification(proc, "textDocument/didChange", {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": text}],
        })

    async def get_diagnostics(self, uri: str, language_id: str) -> list[LSPDiagnostic]:
        """Get diagnostics for a file. Sends didOpen then waits briefly for diagnostics."""
        for name, proc in self._servers.items():
            try:
                await self.did_open(name, uri, language_id, "")
                # Give the server time to publish diagnostics
                await asyncio.sleep(0.3)
                # Collect any pending notifications that might be diagnostics
                diags = []
                while not self._notifications.empty():
                    try:
                        srv_name, notif = self._notifications.get_nowait()
                        if notif.get("method") == "textDocument/publishDiagnostics":
                            params = notif.get("params", {})
                            if params.get("uri") == uri:
                                for d in params.get("diagnostics", []):
                                    diags.append(LSPDiagnostic(
                                        uri=uri,
                                        range_data=d.get("range", {}),
                                        message=d.get("message", ""),
                                        severity=d.get("severity", LSPDiagnostic.SEVERITY_INFORMATION),
                                        source=d.get("source", ""),
                                    ))
                    except asyncio.QueueEmpty:
                        break
                return diags
            except Exception as e:
                log.debug("get_diagnostics failed on %s: %s", name, e)
        return []

    async def format_document(self, uri: str, document_text: str) -> str | None:
        """Format a document using the LSP server."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "textDocument/formatting", {
                    "textDocument": {"uri": uri},
                    "options": {
                        "tabSize": 4,
                        "insertSpaces": True,
                    },
                }, timeout=5.0)
                if result:
                    # Apply edits to the document text
                    lines = document_text.split("\n")
                    for edit in sorted(result, key=lambda e: e.get("range", {}).get("start", {}).get("line", 0), reverse=True):
                        rng = edit.get("range", {})
                        start_line = rng.get("start", {}).get("line", 0)
                        start_char = rng.get("start", {}).get("character", 0)
                        end_line = rng.get("end", {}).get("line", 0)
                        end_char = rng.get("end", {}).get("character", 0)
                        new_text = edit.get("newText", "")

                        if start_line == end_line:
                            line = lines[start_line]
                            lines[start_line] = line[:start_char] + new_text + line[end_char:]
                        else:
                            prefix = lines[start_line][:start_char]
                            suffix = lines[end_line][end_char:]
                            lines[start_line:end_line + 1] = (prefix + new_text + suffix).split("\n")
                    return "\n".join(lines)
            except Exception as e:
                log.debug("format_document failed on %s: %s", name, e)
        return None

    async def get_completions(self, uri: str, line: int, character: int) -> list[dict]:
        """Get completions for a position in a file."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "textDocument/completion", {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                }, timeout=5.0)
                if isinstance(result, dict):
                    items = result.get("items", [])
                elif isinstance(result, list):
                    items = result
                else:
                    items = []
                return [
                    {"label": item.get("label", ""), "kind": item.get("kind", ""), "detail": item.get("detail", "")}
                    for item in items
                ]
            except Exception as e:
                log.debug("get_completions failed on %s: %s", name, e)
        return []

    async def shutdown(self):
        """Shutdown all LSP servers."""
        for name, proc in self._servers.items():
            try:
                # Cancel reader
                reader = self._readers.pop(name, None)
                if reader:
                    reader.cancel()

                await self._send_request(proc, "shutdown", {}, timeout=3.0)
                await self._send_notification(proc, "exit", {})
                proc.terminate()
                log.info("Shutdown LSP server: %s", name)
            except Exception as e:
                log.error("Error shutting down LSP server '%s': %s", name, e)

        self._servers.clear()


class LSPTool(Tool):
    """Tool for interacting with LSP servers."""

    name = "lsp"
    description = (
        "Query language servers for diagnostics, completions, and formatting. "
        "Use 'diagnostics' to check a file for errors, 'format' to format code, "
        "or 'completions' to get suggestions at a position."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["diagnostics", "format", "completions"],
                "description": "LSP action to perform",
            },
            "file_path": {
                "type": "string",
                "description": "Path to the file",
            },
            "language": {
                "type": "string",
                "description": "Language ID (e.g., 'python', 'typescript')",
            },
            "line": {
                "type": "integer",
                "description": "Line number (0-indexed, for completions)",
            },
            "character": {
                "type": "integer",
                "description": "Character position (0-indexed, for completions)",
            },
        },
        "required": ["action", "file_path"],
    }

    def __init__(self, lsp_client: LSPClient):
        self.lsp_client = lsp_client

    async def execute(self, action: str, file_path: str, language: str | None = None,
                     line: int | None = None, character: int | None = None) -> ToolResult:
        from tools.security import validate_path

        try:
            path = Path(file_path).resolve()
            uri = validate_path(path)

            if action == "diagnostics":
                diagnostics = await self.lsp_client.get_diagnostics(uri, language or "")
                if not diagnostics:
                    return ToolResult(output="No diagnostics found.")

                result = ["**Diagnostics:**\n"]
                for diag in diagnostics:
                    severity = {
                        LSPDiagnostic.SEVERITY_ERROR: "ERROR",
                        LSPDiagnostic.SEVERITY_WARNING: "WARNING",
                        LSPDiagnostic.SEVERITY_INFORMATION: "INFO",
                        LSPDiagnostic.SEVERITY_HINT: "HINT",
                    }.get(diag.severity, "UNKNOWN")

                    ln = diag.range.get("start", {}).get("line", 0) + 1
                    col = diag.range.get("start", {}).get("character", 0) + 1
                    result.append(f"- [{severity}] Line {ln}:{col}: {diag.message}")

                return ToolResult(output="\n".join(result))

            elif action == "format":
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                formatted = await self.lsp_client.format_document(uri, text)
                if formatted:
                    return ToolResult(output=f"```{language or 'text'}\n{formatted}\n```")
                else:
                    return ToolResult(output="Formatting not available or no changes needed.")

            elif action == "completions":
                if line is None or character is None:
                    return ToolResult(output="Error: line and character are required for completions", error=True)

                completions = await self.lsp_client.get_completions(uri, line, character)
                if not completions:
                    return ToolResult(output="No completions available.")

                result = ["**Completions:**\n"]
                for comp in completions[:20]:
                    label = comp.get("label", "")
                    kind = comp.get("kind", "")
                    detail = comp.get("detail", "")
                    result.append(f"- **{label}** ({kind}): {detail}")

                return ToolResult(output="\n".join(result))

            else:
                return ToolResult(output=f"Error: unknown action '{action}'", error=True)

        except Exception as e:
            log.exception("LSP tool execution failed")
            return ToolResult(output=f"Error: {e}", error=True)
