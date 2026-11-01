"""Image Analyze Tool - Analyze screenshots and UI mockups using vision-capable LLMs."""

import base64
import logging
import os
from pathlib import Path

from . import Tool, ToolResult, live_config

log = logging.getLogger(__name__)

# Base64-encode in chunks (a multiple of 3 bytes so each chunk encodes without
# padding) rather than reading the whole file and encoding it at once: the
# one-shot form held the raw bytes and the base64 bytes in memory together, so
# a large image transiently cost several times its own size.
_B64_CHUNK_SIZE = 3 * 1024 * 1024


def _encode_image_base64(path: Path, chunk_size: int = _B64_CHUNK_SIZE) -> str:
    """Stream a file to a base64 string, one chunk at a time."""
    parts: list[str] = []
    with path.open("rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            parts.append(base64.b64encode(block).decode("ascii"))
    return "".join(parts)


class ImageAnalyzeTool(Tool):
    name = "image_analyze"
    workspace = Path(".")
    description = (
        "Analyze images (screenshots, UI mockups, diagrams) using a vision-capable LLM. "
        "Provide a file_path to an image on disk, or omit it to analyze an image attached "
        "to the current message (attachments live in the message store, not on disk). "
        "The image is sent to the LLM for analysis. Supports PNG, JPEG, GIF, WebP."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the image file on disk. Omit to analyze an attached image.",
            },
            "question": {
                "type": "string",
                "description": "What to analyze about the image (default: 'Describe this image')",
            },
        },
        "required": [],
    }

    ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}  # noqa: RUF012
    MAX_SIZE_MB = 10

    async def execute(self, file_path: str | None = None, question: str | None = None) -> ToolResult:
        """Analyze an image from disk or from one attached to the current message.

        A ``file_path`` is analyzed as before (validated against the workspace,
        encoded, sent to the vision LLM). When it is omitted the most recent
        *image* attachment on this turn's message is used instead -- attachments
        live in the message store as a base64 data URL, never on disk, so this is
        how a model analyzes an image the user attached rather than a file it
        wrote. The two paths share the LLM call below.
        """
        try:
            resolved = self._resolve_image(file_path)
            if isinstance(resolved, ToolResult):
                return resolved
            image_url, label, size_mb = resolved

            prompt = question or "Describe this image in detail."
            try:
                import openai

                config = live_config()
                # Mirror LLMClient: the SDK requires a non-empty key even for
                # no-auth backends (e.g. a local llama.cpp), so fall back to the
                # env var then a placeholder sentinel instead of raising
                # "Missing credentials" on an empty api_key.
                api_key = config.llm.api_key or os.environ.get("OPENAI_API_KEY") or "sk-no-auth"
                client = openai.AsyncOpenAI(
                    api_key=api_key,
                    base_url=config.llm.base_url or None,
                )

                response = await client.chat.completions.create(
                    model=config.llm.model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": image_url},
                            },
                        ],
                    }],
                    max_tokens=1000,
                )

                result = response.choices[0].message.content
                return ToolResult(output=f"**Image Analysis** ({label}: {size_mb:.1f}MB):\n\n{result}")

            except ImportError:
                return ToolResult(
                    output="Error: openai package required for image analysis. "
                           "Install with: pip install openai",
                    error=True,
                )
            except Exception as e:  # noqa: BLE001
                return ToolResult(
                    output=f"Error calling LLM for image analysis: {e}",
                    error=True,
                )

        except Exception as e:
            log.exception("image_analyze failed")
            return ToolResult(output=f"Error: {e}", error=True)

    def _resolve_image(self, file_path: str | None):
        """Return ``(image_url, label, size_mb)`` for a disk path or attached image.

        A ``ToolResult`` error is returned when neither an on-disk file nor a
        usable attachment could be found, so the caller can surface it directly.
        """
        if file_path:
            from .security import validate_path
            path = validate_path(file_path, self.workspace)
            if not path.exists():
                return ToolResult(output=f"Error: File '{file_path}' does not exist", error=True)
            if path.suffix.lower() not in self.ALLOWED_EXTENSIONS:
                return ToolResult(
                    output=f"Error: Unsupported image format '{path.suffix}'. "
                           f"Supported: {', '.join(sorted(self.ALLOWED_EXTENSIONS))}",
                    error=True,
                )
            size_mb = path.stat().st_size / (1024 * 1024)
            if size_mb > self.MAX_SIZE_MB:
                return ToolResult(
                    output=f"Error: Image too large ({size_mb:.1f}MB). Maximum: {self.MAX_SIZE_MB}MB",
                    error=True,
                )
            mime_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
                ".tiff": "image/tiff",
            }.get(path.suffix.lower(), "image/png")
            return (
                f"data:{mime_type};base64,{_encode_image_base64(path)}",
                path.name,
                size_mb,
            )

        # No path: fall back to an image attached to this turn's message.
        attachment = self._first_image_attachment()
        if attachment is None:
            return ToolResult(
                output=("Error: no file_path given and no image attached to this message. "
                        "Provide a path to an image on disk, or attach an image."),
                error=True,
            )
        data = (attachment.get("data") or "").strip()
        if not data:
            return ToolResult(output="Error: attached image has no data to analyze", error=True)
        if not data.startswith("data:"):
            # Stored as raw base64 (no MIME header); re-wrap it so the model's
            # image_url is a valid data URL.
            mime = attachment.get("mime_type") or "image/png"
            data = f"data:{mime};base64,{data}"
        # Rough raw-size estimate from the base64 length (4 b64 chars -> 3 bytes).
        b64 = data.split(",", 1)[1] if "," in data else data
        size_mb = len(b64) * 3 / 4 / (1024 * 1024)
        if size_mb > self.MAX_SIZE_MB:
            return ToolResult(
                output=f"Error: attached image too large ({size_mb:.1f}MB). Maximum: {self.MAX_SIZE_MB}MB",
                error=True,
            )
        return (data, attachment.get("file_name") or "attached image", size_mb)

    def _first_image_attachment(self) -> dict | None:
        """The first image attachment on this turn's message, or None.

        Matches on the declared type and falls back to the MIME prefix so an
        attachment stored without a canonical ``attachment_type`` still resolves.
        """
        for att in self.attachments():
            if att.get("attachment_type") == "image" or str(att.get("mime_type", "")).startswith("image/"):
                return att
        return None
