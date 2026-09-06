"""Image Analyze Tool - Analyze screenshots and UI mockups using vision-capable LLMs."""

import base64
import logging

from tools import Tool, ToolResult

log = logging.getLogger(__name__)


class ImageAnalyzeTool(Tool):
    name = "image_analyze"
    description = (
        "Analyze images (screenshots, UI mockups, diagrams) using a vision-capable LLM. "
        "Provide a file path to an image and a question about it. The image is sent to "
        "the LLM for analysis. Supports PNG, JPEG, GIF, WebP formats."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the image file",
            },
            "question": {
                "type": "string",
                "description": "What to analyze about the image (default: 'Describe this image')",
            },
        },
        "required": ["file_path"],
    }

    ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}
    MAX_SIZE_MB = 10

    async def execute(self, file_path: str, question: str | None = None) -> ToolResult:
        try:
            from codeassist.config import load_config

            config = load_config()

            from tools.security import validate_path
            path = validate_path(file_path, config.workspace)

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

            image_data = base64.b64encode(path.read_bytes()).decode("utf-8")
            prompt = question or "Describe this image in detail."

            mime_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
                ".tiff": "image/tiff",
            }.get(path.suffix.lower(), "image/png")

            try:
                import openai

                client = openai.AsyncOpenAI(
                    api_key=config.llm.api_key,
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
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_data}",
                                },
                            },
                        ],
                    }],
                    max_tokens=1000,
                )

                result = response.choices[0].message.content
                return ToolResult(
                    output=f"**Image Analysis** ({path.name}, {size_mb:.1f}MB):\n\n{result}"
                )

            except ImportError:
                return ToolResult(
                    output="Error: openai package required for image analysis. "
                           "Install with: pip install openai",
                    error=True,
                )
            except Exception as e:
                return ToolResult(
                    output=f"Error calling LLM for image analysis: {e}",
                    error=True,
                )

        except Exception as e:
            log.exception("image_analyze failed")
            return ToolResult(output=f"Error: {e}", error=True)
