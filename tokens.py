"""Token counting and context window management."""

import tiktoken


def count_tokens(messages: list[dict], model: str = "gpt-4") -> int:
    """Estimate token count for a list of messages."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    total = 0
    for msg in messages:
        total += 4  # every message format: <|start|>{role}\n{content}\n<|end|>
        for key, value in msg.items():
            if isinstance(value, str):
                total += len(encoding.encode(value))
            elif key == "tool_calls":
                total += len(encoding.encode(str(value)))
            elif key == "tool_call_id":
                total += len(encoding.encode(value))
    total += 2  # every reply is primed with <|start|>assistant<|message|>
    return total


def truncate_tool_result(content: str, max_tokens: int = 4000) -> str:
    """Truncate a tool result to fit within token limits."""
    if not content:
        return content

    try:
        encoding = tiktoken.encoding_for_model("gpt-4")
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    tokens = len(encoding.encode(content))
    if tokens <= max_tokens:
        return content

    # Truncate and add notice
    # Approximate: keep ~80% to leave room for surrounding context
    target_tokens = int(max_tokens * 0.8)
    chars_per_token = len(content) / tokens
    target_chars = int(target_tokens * chars_per_token)

    half = target_chars // 2
    truncated = content[:half] + f"\n\n... ({tokens} tokens, truncated) ...\n\n" + content[-half:]
    return truncated


def compact_messages(messages: list[dict], keep_recent: int = 20, model: str = "gpt-4") -> list[dict]:
    """Compact old messages by summarizing tool results and removing redundancy.

    Strategy:
    - Keep system prompt (index 0) always
    - Keep last `keep_recent` messages as-is
    - For older messages: truncate tool results, keep user/assistant text
    """
    if len(messages) <= keep_recent + 2:
        return messages

    system = messages[0]
    old = messages[1:-keep_recent]
    recent = messages[-keep_recent:]

    # Compact old messages: truncate large tool results
    compacted = [system]
    for msg in old:
        if msg["role"] == "tool" and msg.get("content"):
            msg = {**msg, "content": truncate_tool_result(msg["content"], max_tokens=1000)}
        elif msg["role"] == "assistant" and msg.get("tool_calls"):
            # Keep tool calls but truncate any large content
            msg = {**msg, "content": (msg.get("content") or "")[:500] if msg.get("content") else msg.get("content")}
        compacted.append(msg)

    # Add a summary marker
    compacted.append({
        "role": "user",
        "content": f"[Earlier conversation summary: {len(old)} messages compacted to save context space]",
    })
    compacted.append({
        "role": "assistant",
        "content": "Understood, I have the context from our earlier conversation. Continuing.",
    })

    compacted.extend(recent)
    return compacted


def check_context_limit(messages: list[dict], model: str = "gpt-4", context_window: int = 128000) -> dict:
    """Check if we're approaching the context limit.

    Returns dict with:
        - total_tokens: estimated total tokens
        - headroom: tokens remaining
        - needs_compaction: True if we should compact
        - severity: "ok" | "warning" | "critical"
    """
    tokens = count_tokens(messages, model)
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
