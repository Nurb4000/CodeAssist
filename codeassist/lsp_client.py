import asyncio
import json
import logging
from pathlib import Path

from .tools import Tool, ToolResult

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
        self._specs: dict[str, dict] = {}
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
            self._specs[name] = {
                "command": command,
                "args": list(args),
                "languages": list(languages),
            }

            # Start background reader for this server
            self._readers[name] = asyncio.create_task(
                self._read_loop(name, proc)
            )

            # Send initialize request
            await self._send_request(
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
                await proc.stdout.readline()

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
        except Exception as e:  # noqa: BLE001
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
        except TimeoutError:
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
        for name in self._servers:
            try:
                await self.did_open(name, uri, language_id, "")
                # Give the server time to publish diagnostics
                await asyncio.sleep(0.3)
                # Collect any pending notifications that might be diagnostics
                diags = []
                while not self._notifications.empty():
                    try:
                        _, notif = self._notifications.get_nowait()
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
            except Exception as e:  # noqa: BLE001
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
            except Exception as e:  # noqa: BLE001
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
            except Exception as e:  # noqa: BLE001
                log.debug("get_completions failed on %s: %s", name, e)
        return []

    async def get_definition(self, uri: str, line: int, character: int) -> list[dict]:
        """Get definition locations for a symbol at position."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "textDocument/definition", {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                }, timeout=5.0)
                if isinstance(result, list):
                    return self._format_locations(result)
                elif isinstance(result, dict) and result.get("targets"):
                    return self._format_locations(result["targets"])
                elif isinstance(result, dict):
                    return self._format_locations([result])
            except Exception as e:  # noqa: BLE001
                log.debug("get_definition failed on %s: %s", name, e)
        return []

    async def get_references(self, uri: str, line: int, character: int, include_declaration: bool = True) -> list[dict]:
        """Get all references to a symbol at position."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "textDocument/references", {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                    "context": {"includeDeclaration": include_declaration},
                }, timeout=10.0)
                if isinstance(result, list):
                    return self._format_locations(result)
            except Exception as e:  # noqa: BLE001
                log.debug("get_references failed on %s: %s", name, e)
        return []

    async def get_hover(self, uri: str, line: int, character: int) -> dict:
        """Get hover information for a symbol at position."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "textDocument/hover", {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                }, timeout=5.0)
                if result and result.get("contents"):
                    contents = result["contents"]
                    if isinstance(contents, dict):
                        return {"value": contents.get("value", ""), "kind": contents.get("kind", "")}
                    elif isinstance(contents, list):
                        parts = []
                        for c in contents:
                            if isinstance(c, dict):
                                parts.append(c.get("value", ""))
                            else:
                                parts.append(str(c))
                        return {"value": "\n".join(parts), "kind": "markdown"}
                    else:
                        return {"value": str(contents), "kind": "plain"}
            except Exception as e:  # noqa: BLE001
                log.debug("get_hover failed on %s: %s", name, e)
        return {}

    async def get_document_symbols(self, uri: str) -> list[dict]:
        """Get all symbols in a document."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "textDocument/documentSymbol", {
                    "textDocument": {"uri": uri},
                }, timeout=5.0)
                if isinstance(result, list):
                    symbols = []
                    for sym in result:
                        symbols.append({
                            "name": sym.get("name", ""),
                            "kind": sym.get("kind", ""),
                            "range": sym.get("range", {}),
                            "children": sym.get("children", []),
                        })
                    return symbols
            except Exception as e:  # noqa: BLE001
                log.debug("get_document_symbols failed on %s: %s", name, e)
        return []

    async def get_workspace_symbols(self, query: str) -> list[dict]:
        """Search for symbols across the workspace."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "workspace/symbol", {
                    "query": query,
                }, timeout=10.0)
                if isinstance(result, list):
                    symbols = []
                    for sym in result:
                        loc = sym.get("location", {})
                        symbols.append({
                            "name": sym.get("name", ""),
                            "kind": sym.get("kind", ""),
                            "uri": loc.get("uri", ""),
                            "range": loc.get("range", {}),
                            "container_name": sym.get("containerName", ""),
                        })
                    return symbols[:50]
            except Exception as e:  # noqa: BLE001
                log.debug("get_workspace_symbols failed on %s: %s", name, e)
        return []

    async def rename_symbol(self, uri: str, line: int, character: int, new_name: str) -> dict | None:
        """Rename a symbol and all its references."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "textDocument/rename", {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                    "newName": new_name,
                }, timeout=10.0)
                if result:
                    return result
            except Exception as e:  # noqa: BLE001
                log.debug("rename_symbol failed on %s: %s", name, e)
        return None

    async def get_type_definition(self, uri: str, line: int, character: int) -> list[dict]:
        """Get type definition locations for a symbol at position."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "textDocument/typeDefinition", {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                }, timeout=5.0)
                if isinstance(result, list):
                    return self._format_locations(result)
                elif isinstance(result, dict):
                    return self._format_locations([result])
            except Exception as e:  # noqa: BLE001
                log.debug("get_type_definition failed on %s: %s", name, e)
        return []

    async def get_implementation(self, uri: str, line: int, character: int) -> list[dict]:
        """Get implementation locations for a symbol at position."""
        for name, proc in self._servers.items():
            try:
                result = await self._send_request(proc, "textDocument/implementation", {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character},
                }, timeout=5.0)
                if isinstance(result, list):
                    return self._format_locations(result)
                elif isinstance(result, dict):
                    return self._format_locations([result])
            except Exception as e:  # noqa: BLE001
                log.debug("get_implementation failed on %s: %s", name, e)
        return []

    def _format_locations(self, locations: list) -> list[dict]:
        """Format LSP location objects for agent consumption."""
        result = []
        for loc in locations:
            if isinstance(loc, dict):
                uri = loc.get("uri", "")
                rng = loc.get("range", {})
                result.append({
                    "uri": uri,
                    "line": rng.get("start", {}).get("line", 0),
                    "character": rng.get("start", {}).get("character", 0),
                })
        return result

    async def _stop_server(self, name: str):
        """Gracefully stop a single running LSP server and drop its state."""
        proc = self._servers.pop(name, None)
        reader = self._readers.pop(name, None)
        if reader:
            reader.cancel()
        self._specs.pop(name, None)
        if proc is not None and proc.returncode is None:
            try:
                await self._send_request(proc, "shutdown", {}, timeout=3.0)
                await self._send_notification(proc, "exit", {})
            except Exception:  # pragma: no cover - server may already be gone  # noqa: BLE001, S110
                pass
            proc.terminate()
            log.info("Stopped LSP server: %s", name)

    async def shutdown(self):
        """Shutdown all LSP servers."""
        for name in list(self._servers):
            await self._stop_server(name)

    async def reload(self, specs: dict[str, dict], workspace: Path):
        """Reconcile running LSP servers with a new spec set.

        Stops servers removed from the set; (re)starts servers whose command,
        args, or languages changed; and leaves unchanged servers running.
        """
        current = set(self._servers)
        desired = set(specs)
        for name in current - desired:
            await self._stop_server(name)
        for name, spec in specs.items():
            if name not in current or self._specs.get(name) != spec:
                await self.start_server(
                    name=name,
                    command=spec["command"],
                    args=spec["args"],
                    languages=spec["languages"],
                    workspace=workspace,
                )


class LSPTool(Tool):
    """Tool for interacting with LSP servers. Supports 9 operations."""

    name = "lsp"
    description = (
        "Query language servers for code intelligence. Read-only operation, always allowed.\n\n"
        "Actions:\n"
        "- diagnostics: Check file for errors/warnings\n"
        "- format: Format the document\n"
        "- completions: Get autocomplete suggestions at position\n"
        "- definition: Find symbol definition at position\n"
        "- references: Find all references to symbol at position\n"
        "- hover: Get hover info (docstring, type) at position\n"
        "- document_symbols: List all symbols in file\n"
        "- workspace_symbols: Search symbols across workspace\n"
        "- rename: Rename symbol and all references\n"
        "- type_definition: Find type definition at position\n"
        "- implementation: Find implementations at position"
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "diagnostics", "format", "completions", "definition",
                    "references", "hover", "document_symbols",
                    "workspace_symbols", "rename", "type_definition", "implementation"
                ],
                "description": "LSP action to perform",
            },
            "file_path": {
                "type": "string",
                "description": "Path to the file (required except for workspace_symbols)",
            },
            "language": {
                "type": "string",
                "description": "Language ID (e.g., 'python', 'typescript')",
            },
            "line": {
                "type": "integer",
                "description": "Line number (0-indexed, for position-based actions)",
            },
            "character": {
                "type": "integer",
                "description": "Character position (0-indexed, for position-based actions)",
            },
            "query": {
                "type": "string",
                "description": "Search query (for workspace_symbols)",
            },
            "new_name": {
                "type": "string",
                "description": "New symbol name (for rename action)",
            },
        },
        "required": ["action"],
    }

    def __init__(self, lsp_client: LSPClient):
        self.lsp_client = lsp_client

    async def execute(
        self,
        action: str,
        file_path: str | None = None,
        language: str | None = None,
        line: int | None = None,
        character: int | None = None,
        query: str | None = None,
        new_name: str | None = None,
    ) -> ToolResult:
        from .tools.security import validate_path

        try:
            # Check if any LSP server is available
            if not self.lsp_client._servers:
                return ToolResult(output="No LSP servers available. Configure LSP in config.toml or via /api/lsp.")

            # workspace_symbols doesn't need a file path
            if action == "workspace_symbols":
                if not query:
                    return ToolResult(output="Error: query is required for workspace_symbols", error=True)
                symbols = await self.lsp_client.get_workspace_symbols(query)
                if not symbols:
                    return ToolResult(output=f"No symbols found matching '{query}'.")
                result = [f"**Workspace Symbols for '{query}':**\n"]
                for sym in symbols[:30]:
                    uri = Path(sym["uri"]).name if sym.get("uri") else "unknown"
                    ln = sym.get("range", {}).get("start", {}).get("line", 0) + 1
                    container = f" (in {sym['container_name']})" if sym.get("container_name") else ""
                    result.append(f"- **{sym['name']}** ({sym['kind']}) at {uri}:{ln}{container}")
                return ToolResult(output="\n".join(result))

            # All other actions need file_path
            if not file_path:
                return ToolResult(output=f"Error: file_path is required for action '{action}'", error=True)

            path = Path(file_path).resolve()

            # File existence check
            if not path.exists():
                return ToolResult(output=f"Error: file does not exist: {file_path}", error=True)

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
                text = path.read_text(encoding="utf-8")
                formatted = await self.lsp_client.format_document(uri, text)
                if formatted:
                    return ToolResult(output=f"```{language or 'text'}\n{formatted}\n```")
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

            elif action == "definition":
                if line is None or character is None:
                    return ToolResult(output="Error: line and character are required for definition", error=True)
                locations = await self.lsp_client.get_definition(uri, line, character)
                if not locations:
                    return ToolResult(output="No definition found.")
                result = ["**Definition:**\n"]
                for loc in locations:
                    fname = Path(loc["uri"]).name
                    result.append(f"- {fname}:{loc['line']+1}:{loc['character']+1}")
                return ToolResult(output="\n".join(result))

            elif action == "references":
                if line is None or character is None:
                    return ToolResult(output="Error: line and character are required for references", error=True)
                locations = await self.lsp_client.get_references(uri, line, character)
                if not locations:
                    return ToolResult(output="No references found.")
                result = [f"**References ({len(locations)}):**\n"]
                for loc in locations[:30]:
                    fname = Path(loc["uri"]).name
                    result.append(f"- {fname}:{loc['line']+1}:{loc['character']+1}")
                return ToolResult(output="\n".join(result))

            elif action == "hover":
                if line is None or character is None:
                    return ToolResult(output="Error: line and character are required for hover", error=True)
                hover = await self.lsp_client.get_hover(uri, line, character)
                if not hover:
                    return ToolResult(output="No hover information available.")
                return ToolResult(output=f"**Hover Info:**\n{hover['value']}")

            elif action == "document_symbols":
                symbols = await self.lsp_client.get_document_symbols(uri)
                if not symbols:
                    return ToolResult(output="No symbols found in document.")
                result = ["**Document Symbols:**\n"]
                for sym in symbols[:50]:
                    ln = sym.get("range", {}).get("start", {}).get("line", 0) + 1
                    result.append(f"- {sym['name']} ({sym['kind']}) at line {ln}")
                return ToolResult(output="\n".join(result))

            elif action == "rename":
                if line is None or character is None or not new_name:
                    return ToolResult(output="Error: line, character, and new_name are required for rename", error=True)
                result = await self.lsp_client.rename_symbol(uri, line, character, new_name)
                if not result:
                    return ToolResult(output="Rename not available or failed.")
                changes = result.get("changes", {})
                total = sum(len(v) for v in changes.values())
                return ToolResult(output=f"Rename would change {total} locations. (Preview only — use edit tool to apply.)")

            elif action == "type_definition":
                if line is None or character is None:
                    return ToolResult(output="Error: line and character are required for type_definition", error=True)
                locations = await self.lsp_client.get_type_definition(uri, line, character)
                if not locations:
                    return ToolResult(output="No type definition found.")
                result = ["**Type Definition:**\n"]
                for loc in locations:
                    fname = Path(loc["uri"]).name
                    result.append(f"- {fname}:{loc['line']+1}:{loc['character']+1}")
                return ToolResult(output="\n".join(result))

            elif action == "implementation":
                if line is None or character is None:
                    return ToolResult(output="Error: line and character are required for implementation", error=True)
                locations = await self.lsp_client.get_implementation(uri, line, character)
                if not locations:
                    return ToolResult(output="No implementations found.")
                result = ["**Implementations:**\n"]
                for loc in locations:
                    fname = Path(loc["uri"]).name
                    result.append(f"- {fname}:{loc['line']+1}:{loc['character']+1}")
                return ToolResult(output="\n".join(result))

            else:
                return ToolResult(output=f"Error: unknown action '{action}'", error=True)

        except Exception as e:
            log.exception("LSP tool execution failed")
            return ToolResult(output=f"Error: {e}", error=True)
