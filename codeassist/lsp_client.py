import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

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

            # Send initialize request
            init_response = await self._send_request(
                proc,
                "initialize",
                {
                    "processId": None,
                    "rootUri": workspace.as_uri(),
                    "capabilities": {},
                },
            )

            # Send initialized notification
            await self._send_notification(proc, "initialized", {})

            log.info("Started LSP server: %s for languages: %s", name, languages)

        except Exception as e:
            log.error("Failed to start LSP server '%s': %s", name, e)
            raise

    async def _send_request(self, proc: asyncio.subprocess.Process, method: str, params: dict) -> dict:
        """Send a request to the LSP server."""
        request_id = self._next_id
        self._next_id += 1

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        message = json.dumps(request) + "\r\n"
        proc.stdin.write(message.encode())
        await proc.stdin.drain()

        # For now, we'll use a simple synchronous approach
        # In production, you'd want proper async message handling
        return {}

    async def _send_notification(self, proc: asyncio.subprocess.Process, method: str, params: dict):
        """Send a notification to the LSP server."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        message = json.dumps(notification) + "\r\n"
        proc.stdin.write(message.encode())
        await proc.stdin.drain()

    async def get_diagnostics(self, uri: str, language_id: str) -> list[LSPDiagnostic]:
        """Get diagnostics for a file."""
        # This is a simplified implementation
        # In production, you'd need proper LSP message handling
        return []

    async def format_document(self, uri: str, document_text: str) -> str | None:
        """Format a document using the LSP server."""
        # This is a simplified implementation
        return None

    async def get_completions(self, uri: str, line: int, character: int) -> list[dict]:
        """Get completions for a position in a file."""
        # This is a simplified implementation
        return []

    async def shutdown(self):
        """Shutdown all LSP servers."""
        for name, proc in self._servers.items():
            try:
                await self._send_request(proc, "shutdown", {})
                await self._send_notification(proc, "exit", {})
                proc.terminate()
                log.info("Shutdown LSP server: %s", name)
            except Exception as e:
                log.error("Error shutting down LSP server '%s': %s", name, e)

        self._servers.clear()


class LSPTool:
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

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute(self, action: str, file_path: str, language: str | None = None,
                     line: int | None = None, character: int | None = None) -> str:
        from tools.security import validate_path

        try:
            path = Path(file_path).resolve()
            uri = validate_path(path)

            if action == "diagnostics":
                diagnostics = await self.lsp_client.get_diagnostics(uri, language or "")
                if not diagnostics:
                    return "No diagnostics found."

                result = ["**Diagnostics:**\n"]
                for diag in diagnostics:
                    severity = {
                        LSPDiagnostic.SEVERITY_ERROR: "ERROR",
                        LSPDiagnostic.SEVERITY_WARNING: "WARNING",
                        LSPDiagnostic.SEVERITY_INFORMATION: "INFO",
                        LSPDiagnostic.SEVERITY_HINT: "HINT",
                    }.get(diag.severity, "UNKNOWN")

                    line = diag.range.get("start", {}).get("line", 0) + 1
                    col = diag.range.get("start", {}).get("character", 0) + 1
                    result.append(f"- [{severity}] Line {line}:{col}: {diag.message}")

                return "\n".join(result)

            elif action == "format":
                formatted = await self.lsp_client.format_document(uri, "")
                if formatted:
                    return f"```{language or 'text'}\n{formatted}\n```"
                else:
                    return "Formatting not available or no changes needed."

            elif action == "completions":
                if line is None or character is None:
                    return "Error: line and character are required for completions"

                completions = await self.lsp_client.get_completions(uri, line, character)
                if not completions:
                    return "No completions available."

                result = ["**Completions:**\n"]
                for comp in completions[:20]:  # Limit to 20 completions
                    label = comp.get("label", "")
                    kind = comp.get("kind", "")
                    detail = comp.get("detail", "")
                    result.append(f"- **{label}** ({kind}): {detail}")

                return "\n".join(result)

            else:
                return f"Error: unknown action '{action}'"

        except Exception as e:
            log.exception("LSP tool execution failed")
            return f"Error: {e}"
