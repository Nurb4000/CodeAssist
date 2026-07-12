import json
import logging
from typing import Any

import httpx

from tools import Tool, ToolResult
from tools.security import validate_url

log = logging.getLogger(__name__)


class HTTPTool(Tool):
    name = "http"
    description = (
        "Make HTTP requests (GET, POST, PUT, DELETE, PATCH). "
        "Use this to interact with REST APIs, fetch web content, "
        "or test endpoints."
    )
    parameters = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "description": "HTTP method to use",
            },
            "url": {
                "type": "string",
                "description": "Request URL",
            },
            "headers": {
                "type": "object",
                "description": "HTTP headers as key-value pairs",
            },
            "params": {
                "type": "object",
                "description": "Query parameters",
            },
            "body": {
                "type": "string",
                "description": "Request body (for POST, PUT, PATCH)",
            },
            "json": {
                "type": "object",
                "description": "JSON body to send (alternative to body)",
            },
            "timeout": {
                "type": "number",
                "description": "Request timeout in seconds (default: 30)",
            },
        },
        "required": ["method", "url"],
    }

    def __init__(self):
        self.max_response_chars = 50000

    async def execute(self, method: str, url: str, headers: dict | None = None,
                     params: dict | None = None, body: str | None = None,
                     json_data: dict | None = None, timeout: float = 30.0) -> ToolResult:
        try:
            method = method.upper()

            # Validate URL
            if not url.startswith(("http://", "https://")):
                return ToolResult(
                    output=f"Error: URL must start with http:// or https://",
                    error=True
                )

            # Block SSRF to private/internal networks
            if not validate_url(url):
                return ToolResult(
                    output=f"Error: URL '{url}' targets a private or internal network and is not allowed",
                    error=True
                )

            # Build request kwargs
            kwargs: dict[str, Any] = {
                "timeout": timeout,
            }

            if headers:
                kwargs["headers"] = headers

            if params:
                kwargs["params"] = params

            if body:
                kwargs["content"] = body
            elif json_data:
                kwargs["json"] = json_data

            # Make request
            async with httpx.AsyncClient() as client:
                response = await client.request(method, url, **kwargs)

            # Format response
            output = self._format_response(response)
            return ToolResult(output=output)

        except httpx.TimeoutException:
            return ToolResult(output=f"Error: Request timed out after {timeout}s", error=True)
        except httpx.ConnectError as e:
            return ToolResult(output=f"Error: Could not connect to {url}: {e}", error=True)
        except httpx.HTTPStatusError as e:
            return ToolResult(
                output=f"Error: HTTP {e.response.status_code} - {e.response.text[:1000]}",
                error=True
            )
        except Exception as e:
            log.exception("HTTP request failed")
            return ToolResult(output=f"HTTP request error: {e}", error=True)

    def _format_response(self, response: httpx.Response) -> str:
        """Format HTTP response for display."""
        lines = [
            f"**HTTP {response.status_code} {response.reason_phrase}**",
            f"URL: {response.url}",
            f"Method: {response.request.method}",
        ]

        # Add response headers
        if response.headers:
            lines.append("\n**Response Headers:**")
            for key, value in list(response.headers.items())[:10]:  # Limit headers
                lines.append(f"  {key}: {value}")

        # Add response body
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                json_data = response.json()
                lines.append("\n**Response Body (JSON):**")
                lines.append(json.dumps(json_data, indent=2)[:self.max_response_chars])
            except json.JSONDecodeError:
                lines.append(f"\n**Response Body:**\n{response.text[:self.max_response_chars]}")
        else:
            lines.append(f"\n**Response Body:**\n{response.text[:self.max_response_chars]}")

        return "\n".join(lines)
