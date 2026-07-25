"""Token counting and context window management."""

import json
import tiktoken

# Cache for tiktoken encodings to avoid repeated lookups
_encoding_cache: dict[str, tiktoken.Encoding] = {}


def _get_encoding(model: str = "gpt-4") -> tiktoken.Encoding:
    """Get a cached tiktoken encoding for the given model."""
    if model not in _encoding_cache:
        try:
            _encoding_cache[model] = tiktoken.encoding_for_model(model)
        except KeyError:
            _encoding_cache[model] = tiktoken.get_encoding("cl100k_base")
    return _encoding_cache[model]


def count_tokens(messages: list[dict], model: str = "gpt-4", tool_schemas: list[dict] | None = None) -> int:
    """Estimate token count for a list of messages, optionally including tool schema overhead."""
    encoding = _get_encoding(model)

    total = 0
    for msg in messages:
        total += 4  # every message format: <|start|>{role}\n{content}\n<|end|>
        for key, value in msg.items():
            if isinstance(value, str):
                total += len(encoding.encode(value))
            elif key == "tool_calls":
                # Use JSON serialization for more accurate OpenAI wire format
                total += len(encoding.encode(json.dumps(value, separators=(",", ":"))))
            elif key == "tool_call_id":
                total += len(encoding.encode(value))
    total += 2  # every reply is primed with <|start|>assistant<|message|>

    # Count tool schema overhead if provided
    if tool_schemas:
        schema_text = json.dumps(tool_schemas, separators=(",", ":"))
        total += len(encoding.encode(schema_text))

    return total


def truncate_tool_result(content: str, max_tokens: int = 4000) -> str:
    """Truncate a tool result to fit within token limits.

    Strategy: keep the start, then find and preserve error/traceback lines from the end.
    """
    if not content:
        return content

    encoding = _get_encoding("gpt-4")
    tokens = len(encoding.encode(content))
    if tokens <= max_tokens:
        return content

    # Approximate: keep ~80% to leave room for surrounding context
    target_tokens = int(max_tokens * 0.8)
    chars_per_token = len(content) / tokens
    target_chars = int(target_tokens * chars_per_token)

    lines = content.split("\n")

    # Keep lines from the start until we hit the budget
    kept_lines = []
    char_count = 0
    for line in lines:
        line_chars = len(line) + 1  # +1 for newline
        if char_count + line_chars > target_chars // 2:
            break
        kept_lines.append(line)
        char_count += line_chars

    # From the end, find error/traceback lines to preserve
    error_keywords = ("error", "exception", "traceback", "failed", "warning", "panic")
    tail_error_lines = []
    tail_char_count = 0
    tail_budget = target_chars // 2
    for line in reversed(lines):
        line_chars = len(line) + 1
        if tail_char_count + line_chars > tail_budget:
            break
        if any(kw in line.lower() for kw in error_keywords):
            tail_error_lines.insert(0, line)
            tail_char_count += line_chars

    # If no error lines found, just take the last N lines
    if not tail_error_lines:
        tail_char_count = 0
        for line in reversed(lines):
            line_chars = len(line) + 1
            if tail_char_count + line_chars > tail_budget:
                break
            tail_error_lines.insert(0, line)
            tail_char_count += line_chars

    notice = f"\n\n... ({tokens} tokens, truncated to {max_tokens}) ...\n\n"
    result = "\n".join(kept_lines) + notice + "\n".join(tail_error_lines)
    return result


def compact_messages(
    messages: list[dict],
    keep_recent: int = 20,
    model: str = "gpt-4",
    escalation_level: int = 0,
) -> list[dict]:
    """Compact old messages by dropping verbose content and summarizing.

    Strategy:
    - Keep system prompt (index 0) always
    - Keep last `keep_recent` messages as-is
    - For older messages:
      - Tool messages: replace with 1-line summary
      - Assistant messages with tool_calls: keep only function names, truncate content
      - User messages: keep as-is (usually short, high-value)
    - escalation_level 0: keep summaries of tool messages
    - escalation_level 1: drop all old tool messages entirely
    """
    if len(messages) <= keep_recent + 2:
        return messages

    system = messages[0]
    old = messages[1:-keep_recent]
    recent = messages[-keep_recent:]

    compacted = [system]

    if escalation_level == 0:
        # Level 0: Replace tool messages with summaries, truncate assistant content
        for msg in old:
            role = msg.get("role", "")
            if role == "tool":
                # Summarize tool output to 1 line
                content = msg.get("content", "") or ""
                tool_call_id = msg.get("tool_call_id", "")
                if len(content) > 200:
                    summary = content[:200].rsplit("\n", 1)[0] + "..."
                else:
                    summary = content
                compacted.append({**msg, "content": f"[Tool output: {summary}]"})
            elif role == "assistant" and msg.get("tool_calls"):
                # Keep tool call structure but truncate reasoning content
                content = (msg.get("content") or "")[:300] if msg.get("content") else msg.get("content")
                compacted.append({**msg, "content": content})
            else:
                # User messages and plain assistant messages: keep as-is
                compacted.append(msg)
    else:
        # Level 1+: Drop all old tool messages, keep only user messages and assistant summaries
        for msg in old:
            role = msg.get("role", "")
            if role == "tool":
                continue  # Drop entirely
            elif role == "assistant" and msg.get("tool_calls"):
                # Keep only function names as a brief summary
                tool_names = [tc.get("function", {}).get("name", tc.get("name", "?"))
                              for tc in msg.get("tool_calls", [])]
                compacted.append({
                    "role": "assistant",
                    "content": f"[Called: {', '.join(tool_names)}]",
                })
            else:
                compacted.append(msg)

    # Add compaction marker as a user message (more cross-provider compatible)
    compacted.append({
        "role": "user",
        "content": f"[Context compaction: {len(old)} earlier messages compacted. "
                   f"Tool outputs summarized. Conversation continues below.]",
    })
    compacted.append({
        "role": "assistant",
        "content": "Understood.",
    })

    compacted.extend(recent)
    return compacted


def check_context_limit(
    messages: list[dict],
    model: str = "gpt-4",
    context_window: int = 128000,
    tool_schemas: list[dict] | None = None,
) -> dict:
    """Check if we're approaching the context limit.

    Returns dict with:
        - total_tokens: estimated total tokens
        - headroom: tokens remaining
        - needs_compaction: True if we should compact
        - severity: "ok" | "warning" | "critical"
    """
    tokens = count_tokens(messages, model, tool_schemas=tool_schemas)
    headroom = context_window - tokens
    usage_pct = tokens / context_window

    if usage_pct > 0.9:
        severity = "critical"
    elif usage_pct > 0.75:
        severity = "warning"
    else:
        severity = "ok"

    return {
        "total_tokens": tokens,
        "headroom": headroom,
        "needs_compaction": severity != "ok",
        "severity": severity,
        "usage_pct": round(usage_pct * 100, 1),
    }
