"""Token counting and context window management."""

import json
import logging
from collections import OrderedDict
from typing import Any

import tiktoken

from .config import LLMConfig
from .llm import LLMClient
from .prompts import COMPACTION_USER_PROMPT, SUMMARY_TEMPLATE

log = logging.getLogger(__name__)

# LRU cache for tiktoken encodings to avoid repeated lookups and unbounded
# growth when models change. Max 16 entries covers all common models.
_ENCODING_CACHE_MAX = 16
_encoding_cache: OrderedDict[str, tiktoken.Encoding] = OrderedDict()


def _get_encoding(model: str = "gpt-4") -> tiktoken.Encoding:
    """Get a cached tiktoken encoding for the given model."""
    if model in _encoding_cache:
        # Move to end (most recently used)
        _encoding_cache.move_to_end(model)
        return _encoding_cache[model]

    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")

    # Evict oldest entry if cache is full
    while len(_encoding_cache) >= _ENCODING_CACHE_MAX:
        _encoding_cache.popitem(last=False)

    _encoding_cache[model] = enc
    return enc


# Estimated tokens per image_url part when counting multipart content.
# Exact cost varies by resolution/format; this is a conservative flat budget
# that still pushes the context manager toward compaction on overflow.
IMAGE_TOKEN_ESTIMATE = 1000


def count_tokens(messages: list[dict], model: str = "gpt-4", tool_schemas: list[dict] | None = None) -> int:
    """Estimate token count for a list of messages, optionally including tool schema overhead."""
    encoding = _get_encoding(model)

    total = 0
    for msg in messages:
        total += 4  # every message format: <|start|>{role}\n{content}\n<|end|>
        for key, value in msg.items():
            if key == "role":
                # The role is already covered by the per-message envelope above;
                # tokenizing it again inflated every message by a token or two.
                continue
            if key == "tool_calls":
                # Use JSON serialization for more accurate OpenAI wire format.
                # Must be checked before the list branch below — tool_calls is
                # a list, but its parts are function calls, not text/image.
                total += len(encoding.encode(json.dumps(value, separators=(",", ":"))))
            elif isinstance(value, list):
                # Multipart content (text + image_url parts)
                for part in value:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            total += len(encoding.encode(part.get("text") or ""))
                        elif part.get("type") == "image_url":
                            total += IMAGE_TOKEN_ESTIMATE
            elif isinstance(value, str):
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
    # tool_call ids whose assistant was stripped to a "[Called: ...]" summary. A
    # matching tool result can still survive in the kept `recent` slice (when the
    # recent/old boundary splits the pair); those become orphans and must be
    # dropped so the compacted transcript stays valid for the provider.
    stripped_ids: set[str] = set()

    if escalation_level == 0:
        # Level 0: Replace tool messages with summaries, truncate assistant content
        for msg in old:
            role = msg.get("role", "")
            if role == "tool":
                # Summarize tool output to 1 line
                content = msg.get("content", "") or ""
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
                stripped_ids.update(tc.get("id") for tc in msg.get("tool_calls", []) if tc.get("id"))
                compacted.append({
                    "role": "assistant",
                    "content": f"[Called: {', '.join(tool_names)}]",
                })
            else:
                compacted.append(msg)

    compacted.extend(recent)
    # Level 0 keeps both sides of every (assistant, tool) pair, so it cannot
    # create orphans; only level 1 strips tool_calls and can orphan a surviving
    # recent tool result. Drop exactly those.
    if escalation_level >= 1 and stripped_ids:
        compacted = [m for m in compacted
                     if not (m.get("role") == "tool" and m.get("tool_call_id") in stripped_ids)]
    return compacted


def check_context_limit(
    messages: list[dict],
    model: str = "gpt-4",
    context_window: int = 128000,
    tool_schemas: list[dict] | None = None,
    threshold_pct: int = 75,
    reserve_tokens: int = 0,
) -> dict:
    """Check if we're approaching the context limit.

    ``threshold_pct`` is the configured trigger point (the value of
    ``[compaction] threshold_pct``). ``reserve_tokens`` is the room the next
    response needs (the model's output budget); counting against the full
    window made a history look safe right up until the provider ran out of
    space mid-reply.

    Returns dict with:
        - total_tokens: estimated total tokens
        - headroom: tokens remaining in the usable window
        - needs_compaction: True if we should compact
        - severity: "ok" | "warning" | "critical"
        - usable_window / threshold_pct: the values the decision used
    """
    try:
        threshold_pct = float(threshold_pct)
    except (TypeError, ValueError):
        threshold_pct = 75.0
    if not 0 < threshold_pct <= 100:
        threshold_pct = 75.0
    try:
        reserve_tokens = max(0, int(reserve_tokens))
    except (TypeError, ValueError):
        reserve_tokens = 0

    usable_window = max(1, int(context_window) - reserve_tokens)
    tokens = count_tokens(messages, model, tool_schemas=tool_schemas)
    headroom = usable_window - tokens
    usage_pct = tokens / usable_window

    threshold = threshold_pct / 100.0
    # Keep the default 75/90 split: "critical" is 15 points above the trigger,
    # capped below 100% so a 99% threshold still has a critical band.
    critical_threshold = min(0.99, threshold + 0.15)
    if usage_pct > critical_threshold:
        severity = "critical"
    elif usage_pct >= threshold:
        severity = "warning"
    else:
        severity = "ok"

    return {
        "total_tokens": tokens,
        "headroom": headroom,
        "needs_compaction": severity != "ok",
        "severity": severity,
        "usage_pct": round(usage_pct * 100, 1),
        "usable_window": usable_window,
        "threshold_pct": threshold_pct,
        "reserve_tokens": reserve_tokens,
    }


def _fit_text_to_tokens(text: str, max_tokens: int, model: str = "gpt-4") -> str:
    """Trim text to a token budget, keeping both ends of the conversation.

    The beginning usually holds the task and the end holds the most recent
    state, so dropping only the middle loses less than truncating the tail.
    """
    if max_tokens <= 0 or not text:
        return ""
    encoding = _get_encoding(model)
    ids = encoding.encode(text)
    if len(ids) <= max_tokens:
        return text

    keep_head = max(1, max_tokens // 4)
    keep_tail = max(1, max_tokens - keep_head)
    omitted = len(ids) - keep_head - keep_tail
    return (
        f"{encoding.decode(ids[:keep_head])}\n\n"
        f"... [{omitted} tokens omitted to fit the compaction model window] ...\n\n"
        f"{encoding.decode(ids[-keep_tail:])}"
    )


def _extract_conversation_text(
    messages: list[dict],
    tool_result_max_tokens: int | None = None,
) -> str:
    """Convert a list of OpenAI-format messages to plain text for summarization.

    ``tool_result_max_tokens`` caps one tool result in the summarizer input.
    When omitted, the historical 3000-character cap is kept for callers that
    only need a rough text rendering.
    """
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        if role == "system":
            continue  # Skip system prompt in summary input
        content = msg.get("content", "") or ""
        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            if tool_result_max_tokens:
                # Same truncation policy tools use for their output budget:
                # keep the start and preserve error lines from the end.
                content = truncate_tool_result(content, max_tokens=tool_result_max_tokens)
            elif len(content) > 3000:
                content = content[:3000] + "\n... [truncated]"
            parts.append(f"[Tool Result ({tool_call_id})]: {content}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                call_summary = []
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "?")
                    args = func.get("arguments", "{}")
                    try:
                        args_obj = json.loads(args) if isinstance(args, str) else args
                        args_str = json.dumps(args_obj, separators=(",", ":"))[:500]
                    except (json.JSONDecodeError, TypeError):
                        args_str = str(args)[:500]
                    call_summary.append(f"{name}({args_str})")
                prefix = f"[Called: {', '.join(call_summary)}]" if not content else f"{content}\n[Called: {', '.join(call_summary)}]"
                parts.append(f"Assistant: {prefix}")
            else:
                parts.append(f"Assistant: {content}")
        elif role == "user":
            parts.append(f"User: {_content_to_text(content)}")
    return "\n\n".join(parts)


def _content_to_text(content: Any) -> str:
    """Flatten OpenAI-format content (string or list of parts) into plain text."""
    if isinstance(content, list):
        text_parts = []
        media_count = 0
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    text_parts.append(part.get("text") or "")
                elif part.get("type") == "image_url":
                    media_count += 1
        if media_count:
            text_parts.append(f"[{media_count} image attachment(s)]")
        return "\n".join(p for p in text_parts if p)
    return content or ""


async def llm_compact_messages(
    messages: list[dict],
    tail_turns: int = 2,
    preserve_recent_tokens: int = 4000,
    previous_summary: str = "",
    compaction_model: str = "",
    main_llm_config: LLMConfig | None = None,
    tool_result_max_tokens: int | None = None,
    max_input_tokens: int | None = None,
) -> tuple[list[dict], str]:
    """Use LLM to summarize old messages, preserving recent turns intact.

    Args:
        messages: Full OpenAI-format message list (including system at index 0).
        tail_turns: Number of recent user+assistant turn pairs to keep intact.
        preserve_recent_tokens: Token budget for the preserved tail section.
        previous_summary: Previous compaction summary to merge with new content.
        compaction_model: Model to use for compaction (empty = use main model).
        main_llm_config: Fallback LLM config if compaction_model is empty.
        tool_result_max_tokens: Per-tool-result cap in the summarizer input.
        max_input_tokens: Budget for the compaction request (model window minus
            the reserved output). The head is trimmed to fit rather than
            sending an oversized prompt to the compaction model.

    Returns:
        Tuple of (compacted messages list, new summary text).
    """
    if len(messages) <= tail_turns + 2:
        return messages, previous_summary

    system_msg = messages[0]

    # Split into head (to summarize) and tail (to preserve)
    # Count turns from the end: each turn is user + assistant (+ tool results)
    tail_start = 0
    turns_found = 0
    i = len(messages) - 1
    while i > 0 and turns_found < tail_turns:
        if messages[i].get("role") == "user":
            turns_found += 1
        i -= 1
    tail_start = i + 1

    # Fallback: if not enough user turns were found (e.g. after compaction the
    # last messages are tool results or assistant-only), preserve the last
    # tail_turns messages by index regardless of role, so recent context is
    # not lost to summarization.
    if turns_found < tail_turns:
        min_tail_start = max(1, len(messages) - tail_turns)
        tail_start = max(tail_start, min_tail_start)

    # Check if tail exceeds token budget; if so, shrink it
    tail_messages = messages[tail_start:]
    tail_tokens = count_tokens(tail_messages)
    while tail_tokens > preserve_recent_tokens and tail_start < len(messages) - 1:
        tail_start += 1
        tail_messages = messages[tail_start:]
        tail_tokens = count_tokens(tail_messages)

    head_messages = messages[1:tail_start]

    if not head_messages:
        return messages, previous_summary

    # Convert head to text for summarization
    head_text = _extract_conversation_text(
        head_messages,
        tool_result_max_tokens=tool_result_max_tokens,
    )
    if not head_text.strip():
        return messages, previous_summary

    if max_input_tokens:
        # Leave room for the summary template, the previous summary and the
        # chat-format envelope. Without this a long first compaction could
        # itself overflow the compaction model and silently leave the history
        # uncompacted.
        budget_model = compaction_model or (
            main_llm_config.model if main_llm_config else "gpt-4"
        )
        budget_encoding = _get_encoding(budget_model)
        previous_tokens = len(budget_encoding.encode(previous_summary or ""))
        scaffold_tokens = len(budget_encoding.encode(SUMMARY_TEMPLATE)) + 800
        head_budget = max(256, int(max_input_tokens) - previous_tokens - scaffold_tokens)
        head_text = _fit_text_to_tokens(head_text, head_budget, model=budget_model)

    # Build compaction prompt
    user_prompt = COMPACTION_USER_PROMPT.format(
        previous_summary=previous_summary or "(No previous summary)",
        conversation=head_text,
    )

    # Create LLM client for compaction
    if compaction_model:
        compaction_llm_cfg = LLMConfig(
            provider=main_llm_config.provider if main_llm_config else "openai",
            model=compaction_model,
            api_key=main_llm_config.api_key if main_llm_config else "",
            base_url=main_llm_config.base_url if main_llm_config else "",
            temperature=0.0,
            max_tokens=4096,
        )
    elif main_llm_config:
        compaction_llm_cfg = LLMConfig(
            provider=main_llm_config.provider,
            model=main_llm_config.model,
            api_key=main_llm_config.api_key,
            base_url=main_llm_config.base_url,
            temperature=0.0,
            max_tokens=4096,
        )
    else:
        log.warning("No LLM config available for compaction, falling back to text mode")
        return messages, previous_summary

    compaction_client = LLMClient(compaction_llm_cfg)
    compaction_msgs = [
        {"role": "system", "content": SUMMARY_TEMPLATE},
        {"role": "user", "content": user_prompt},
    ]

    try:
        summary_text = ""
        async for event in compaction_client.stream(compaction_msgs):
            from .llm import Finish as F
            from .llm import TextDelta as TD
            if isinstance(event, TD):
                summary_text += event.content
            elif isinstance(event, F):
                break

        if not summary_text.strip():
            log.warning("LLM compaction returned empty summary")
            return messages, previous_summary

        # Build compacted message list
        compacted = [
            system_msg,
            {
                "role": "user",
                "content": f"[Previous conversation summary]\n{summary_text}",
            },
        ]
        compacted.extend(tail_messages)

        log.info("LLM compaction: %d head messages summarized to %d chars. Total compacted: %d messages",
                 len(head_messages), len(summary_text), len(compacted))
        return compacted, summary_text

    except Exception as e:  # noqa: BLE001
        log.error("LLM compaction failed: %s. Falling back to text compaction.", e)
        return messages, previous_summary


def strip_media_from_messages(messages: list[dict]) -> list[dict]:
    """Remove media attachments from messages to free up tokens (overflow handling).

    Strips image URLs and base64 content from user messages, replacing them with text descriptions.
    """
    stripped = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            # Multi-part content (text + images)
            text_parts = []
            media_count = 0
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part["text"])
                    elif part.get("type") == "image_url":
                        media_count += 1
                        text_parts.append("[Image removed to save context space]")
            if media_count > 0:
                log.info("Stripped %d media attachments from message", media_count)
            stripped.append({**msg, "content": "\n".join(text_parts)})
        else:
            stripped.append(msg)
    return stripped
