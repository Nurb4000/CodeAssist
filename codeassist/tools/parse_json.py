"""Parse JSON Tool - validate, extract by path, or summarize a JSON document.

Attached JSON files live in the message store as text (never on disk), so this
tool reads them via ``self.attachments()`` -- the same mechanism
``image_analyze`` uses -- rather than looking for a ``file_path``. A ``file_path``
on disk is also supported for symmetry.
"""

import json
import logging
from collections import Counter
from pathlib import Path

from . import Tool, ToolResult, tool_error

log = logging.getLogger(__name__)

# Structured-text mimes treated as JSON-ish by both the backend mime resolver
# (server._resolve_text_mime) and this tool's attachment matcher. Kept in sync
# by hand; the set is small and stable.
_JSON_MIMES = frozenset({
    "application/json", "application/jsonc", "application/xml",
    "application/yaml", "application/toml",
})

# Hard ceiling on input text so a pathologically large document cannot blow up
# the parse in memory.
_MAX_INPUT_CHARS = 5_000_000
# How many top-level keys / summary lines to show before truncating.
_MAX_SUMMARY_KEYS = 100


class ParseJsonTool(Tool):
    name = "parse_json"
    workspace = Path(".")
    max_output_chars = 20_000

    description = (
        "Parse and validate a JSON document, extract a value by path, or summarize "
        "its structure. Use when the user attaches a JSON file and wants it "
        "validated for syntax, queried by a jq-style path (e.g. '$.config.host' or "
        "'$.store.book[0].title'), or turned into a compact summary (root type, "
        "top-level keys with types, array lengths). The attached JSON is already "
        "in the model's context as raw text, so reach for this when you need "
        "programmatic validation, an exact extracted value, or a summary of a large "
        "structure rather than reading the raw text."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "jq-style path to extract, e.g. '$.store.book[0].title'. "
                    "Omit to summarize the whole document."
                ),
            },
            "file_path": {
                "type": "string",
                "description": (
                    "Path to a JSON file on disk. Omit to parse a JSON file "
                    "attached to the current message (preferred)."
                ),
            },
        },
        "required": [],
    }

    async def execute(self, path: str | None = None, file_path: str | None = None) -> ToolResult:
        try:
            text = self._resolve_source(file_path)
            if isinstance(text, ToolResult):
                return text

            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                return tool_error(f"invalid JSON: {e.msg} (line {e.lineno}, column {e.colno})")

            if path:
                return self._extract(data, path)
            return ToolResult(output=self._summarize(data))
        except Exception as e:  # noqa: BLE001 -- unexpected; log and surface generically
            log.exception("parse_json failed")
            return tool_error(str(e))

    # -- source resolution -------------------------------------------------

    def _resolve_source(self, file_path: str | None) -> str | ToolResult:
        """Return the JSON text to parse, from disk or the current attachment."""
        if file_path:
            from .security import WorkspaceViolationError, validate_path

            try:
                resolved = validate_path(file_path, self.workspace)
            except WorkspaceViolationError as e:
                return tool_error(str(e))
            if not resolved.is_file():
                return tool_error(f"not a file: {file_path}")
            text = resolved.read_text(encoding="utf-8", errors="replace")
        else:
            attachment = self._find_json_attachment()
            if attachment is None:
                return tool_error(
                    "no JSON attachment found. Attach a .json file or pass file_path."
                )
            text = (attachment.get("data") or "").strip()

        if not text:
            return tool_error("empty JSON document")
        if len(text) > _MAX_INPUT_CHARS:
            return tool_error(
                f"JSON input too large ({len(text)} chars); maximum is {_MAX_INPUT_CHARS:,}"
            )
        return text

    def _find_json_attachment(self) -> dict | None:
        """The first JSON-ish attachment on this turn's message, or None.

        Matches the stored mime (``application/json`` etc.) or a ``.json`` /
        ``.jsonc`` file name, so it works whether or not the server preserved the
        browser-reported mime.
        """
        for att in self.attachments():
            if att.get("attachment_type") != "text":
                continue
            mime = str(att.get("mime_type", "")).lower()
            name = (att.get("file_name") or "").lower()
            if mime in _JSON_MIMES or name.endswith((".json", ".jsonc")):
                return att
        return None

    # -- extraction --------------------------------------------------------

    def _extract(self, data, path: str) -> ToolResult:
        try:
            tokens = _tokenize_path(path)
        except ValueError as e:
            return tool_error(f"invalid path {path!r}: {e}")
        try:
            result = _resolve_path(data, tokens)
        except (KeyError, IndexError, TypeError) as e:
            return tool_error(f"path {path!r} did not resolve: {e}")

        output = json.dumps(result, indent=2, ensure_ascii=False)
        if len(output) > self.max_output_chars:
            output = _truncate(output, self.max_output_chars)
        return ToolResult(output=f"Value at {path}:\n\n{output}")

    # -- summary -----------------------------------------------------------

    def _summarize(self, data) -> str:
        root_type = _json_type(data)
        char_count = len(json.dumps(data, ensure_ascii=False))
        lines = [f"Valid JSON. Root type: {root_type} ({char_count:,} characters)."]

        if root_type == "object":
            keys = list(data.keys())
            for key in keys[:_MAX_SUMMARY_KEYS]:
                value_type = _json_type(data[key])
                suffix = ""
                if value_type == "array":
                    suffix = f" [{len(data[key])} items]"
                elif value_type == "object":
                    suffix = f" ({len(data[key])} keys)"
                lines.append(f"  - {key!r}: {value_type}{suffix}")
            if len(keys) > _MAX_SUMMARY_KEYS:
                lines.append(f"  … and {len(keys) - _MAX_SUMMARY_KEYS} more key(s)")
        elif root_type == "array":
            lines.append(f"  Length: {len(data)} item(s).")
            if data:
                breakdown = Counter(_json_type(item) for item in data)
                parts = ", ".join(f"{count} {kind}" for kind, count in breakdown.most_common())
                lines.append(f"  Element types: {parts}.")
        else:
            preview = str(data)
            if len(preview) > 200:
                preview = preview[:200] + "…"
            lines.append(f"  Value: {preview}")

        return "\n".join(lines)


# -- path handling ---------------------------------------------------------


def _tokenize_path(path: str) -> list[tuple[str, object]]:
    """Parse a jq-style path into ``(kind, value)`` tokens.

    ``kind`` is ``"key"``, ``"index"``, or ``"star"`` (for ``[*]``). Accepts a
    leading ``$``, bare leading keys (``store.book``), dotted keys
    (``.store.book``), bracketed keys (``["book"]`` / ``[book]``), integer
    indices (``[0]``, negative allowed), and ``[*]`` to map over an array.
    Raises ``ValueError`` on malformed input.
    """
    s = path.strip()
    if s.startswith("$"):
        s = s[1:]
    # Allow a leading bare key ("store.book" -> ".store.book").
    if s and s[0] not in ".[":
        s = "." + s

    tokens: list[tuple[str, object]] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == ".":
            i += 1
            start = i
            while i < n and s[i] not in ".[":
                i += 1
            key = s[start:i]
            if not key:
                raise ValueError("empty key")
            tokens.append(("key", key))
        elif c == "[":
            end = s.find("]", i)
            if end == -1:
                raise ValueError("unterminated '['")
            inner = s[i + 1:end].strip()
            if inner == "*":
                tokens.append(("star", None))
            elif len(inner) >= 2 and inner[0] in "\"'" and inner[-1] == inner[0]:
                tokens.append(("key", inner[1:-1]))
            elif inner.lstrip("-").isdigit():
                tokens.append(("index", int(inner)))
            else:
                raise ValueError(f"invalid index/key: {inner!r}")
            i = end + 1
        else:
            raise ValueError(f"unexpected character {c!r} (use '.key' or '[n]")

    if not tokens:
        raise ValueError("empty path")
    return tokens


def _resolve_path(node, tokens):
    """Walk ``tokens`` from ``node``, returning the resolved value(s)."""
    if not tokens:
        return node
    kind, val = tokens[0]
    rest = tokens[1:]
    if kind == "key":
        if isinstance(node, dict) and val in node:
            return _resolve_path(node[val], rest)
        raise KeyError(val)
    if kind == "index":
        if isinstance(node, list) and -len(node) <= val < len(node):
            return _resolve_path(node[val], rest)
        raise IndexError(val)
    if kind == "star":
        if not isinstance(node, list):
            raise TypeError("'*' can only index into an array")
        return list(node) if not rest else [_resolve_path(el, rest) for el in node]
    raise ValueError(f"unknown token {kind!r}")


def _json_type(value) -> str:
    """JSON-aware type name (distinguishes boolean from number, like jq)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _truncate(text: str, limit: int) -> str:
    half = limit // 2
    return text[:half] + f"\n… (truncated, {len(text) - limit} more chars) …\n" + text[-half:]
