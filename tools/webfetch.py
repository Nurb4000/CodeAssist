import httpx
from pathlib import Path
from tools import Tool, ToolResult
from tools.security import validate_url


class WebFetchTool(Tool):
    name = "webfetch"
    description = "Fetch content from a URL. Returns text or markdown content."
    workspace = Path(".")
    max_chars: int = 30000

    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
            "format": {"type": "string", "enum": ["text", "markdown"], "default": "markdown", "description": "Output format"},
        },
        "required": ["url"],
    }

    async def execute(self, url: str, format: str = "markdown") -> ToolResult:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # Block SSRF to private/internal networks
        if not validate_url(url):
            return ToolResult(
                output=f"Error: URL '{url}' targets a private or internal network and is not allowed",
                error=True
            )

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "CodeAssist/1.0"})
                response.raise_for_status()
        except httpx.TimeoutException:
            return ToolResult(output=f"Error: request timed out for {url}", error=True)
        except httpx.HTTPStatusError as e:
            return ToolResult(output=f"Error: HTTP {e.response.status_code} for {url}", error=True)
        except Exception as e:
            return ToolResult(output=f"Error fetching {url}: {e}", error=True)

        content_type = response.headers.get("content-type", "")

        if "text/html" in content_type:
            text = self._html_to_text(response.text)
        else:
            text = response.text

        if len(text) > self.max_chars:
            half = self.max_chars // 2
            text = text[:half] + "\n\n... (truncated) ...\n\n" + text[-half:]

        return ToolResult(output=text)

    def _html_to_text(self, html: str) -> str:
        try:
            import markdown
            from html.parser import HTMLParser

            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.result = []
                    self.skip = False

                def handle_starttag(self, tag, attrs):
                    if tag in ("script", "style", "noscript"):
                        self.skip = True

                def handle_endtag(self, tag):
                    if tag in ("script", "style", "noscript"):
                        self.skip = False
                    if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
                        self.result.append("\n")

                def handle_data(self, data):
                    if not self.skip:
                        self.result.append(data)

            extractor = TextExtractor()
            extractor.feed(html)
            return "".join(extractor.result).strip()
        except ImportError:
            lines = []
            for line in html.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith(("<script", "<style", "<!")):
                    lines.append(stripped)
            return "\n".join(lines)
