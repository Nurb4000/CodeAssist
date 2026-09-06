"""Tests for token counting, context compaction, and truncation."""
import json
import pytest
from codeassist.tokens import (
    count_tokens,
    truncate_tool_result,
    compact_messages,
    check_context_limit,
    _extract_conversation_text,
    strip_media_from_messages,
)


class TestCountTokens:
    def test_empty_messages(self):
        assert count_tokens([]) >= 2

    def test_single_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert count_tokens(msgs) > 0

    def test_multiple_messages(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        assert count_tokens(msgs) > count_tokens([msgs[0]])

    def test_message_with_tool_calls(self):
        msgs = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"file_path": "test.py"}'}}
                ],
            }
        ]
        assert count_tokens(msgs) > 0

    def test_with_tool_schemas(self):
        msgs = [{"role": "user", "content": "hello"}]
        schemas = [{"name": "read", "description": "Read a file", "parameters": {"type": "object", "properties": {}}}]
        with_schemas = count_tokens(msgs, tool_schemas=schemas)
        without = count_tokens(msgs)
        assert with_schemas > without

    def test_different_models(self):
        msgs = [{"role": "user", "content": "hello world"}]
        c1 = count_tokens(msgs, model="gpt-4")
        c2 = count_tokens(msgs, model="gpt-3.5-turbo")
        assert c1 > 0 and c2 > 0

    def test_tool_call_id_field(self):
        msgs = [{"role": "tool", "content": "result", "tool_call_id": "call_abc123"}]
        assert count_tokens(msgs) > 0

    def test_multipart_text_and_image(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this screenshot:"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
                ],
            },
        ]
        total = count_tokens(msgs)
        assert total > count_tokens([{"role": "user", "content": "Look at this screenshot:"}])


class TestTruncateToolResult:
    def test_short_content(self):
        result = truncate_tool_result("short content", max_tokens=4000)
        assert result == "short content"

    def test_empty_content(self):
        assert truncate_tool_result("") == ""
        assert truncate_tool_result(None) is None

    def test_truncates_long_content(self):
        long = "line " * 5000
        result = truncate_tool_result(long, max_tokens=100)
        assert "truncated" in result.lower()

    def test_preserves_error_lines(self):
        content = "line A\nline B\nline C\nERROR: something failed\nline D\nTraceback: boom"
        result = truncate_tool_result(content, max_tokens=30)
        assert "ERROR" in result or "Traceback" in result

    def test_all_error_keywords(self):
        keywords = ["error", "exception", "traceback", "failed", "warning", "panic"]
        for kw in keywords:
            content = f"normal line\nanother line\n{kw}: something\nmore lines"
            result = truncate_tool_result(content, max_tokens=20)
            assert kw in result.lower()


class TestCompactMessages:
    def test_no_compaction_needed(self):
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = compact_messages(msgs, keep_recent=10)
        assert len(result) == len(msgs)

    def test_level0_compaction(self):
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "user message 1"},
            {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "read"}}]},
            {"role": "tool", "content": "a" * 500, "tool_call_id": "call_1"},
            {"role": "user", "content": "user message 2"},
            {"role": "assistant", "content": "final response"},
        ]
        result = compact_messages(msgs, keep_recent=2, escalation_level=0)
        # Tool output should be summarized with [Tool output: prefix
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) > 0
        assert "[Tool output:" in tool_msgs[0]["content"]
        # Recent messages (last 2) should be preserved as-is
        recent = [m for m in result if m["role"] == "assistant" and m.get("content") == "final response"]
        assert len(recent) == 1

    def test_level1_compaction_drops_tool_messages(self):
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "user message 1"},
            {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "read"}}]},
            {"role": "tool", "content": "some output", "tool_call_id": "call_1"},
            {"role": "user", "content": "user message 2"},
            {"role": "assistant", "content": "final response"},
        ]
        result = compact_messages(msgs, keep_recent=2, escalation_level=1)
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 0
        # Assistant with tool_calls should be replaced with [Called: ...]
        assistant_msgs = [m for m in result if m["role"] == "assistant"]
        assert any("[Called:" in (m.get("content") or "") for m in assistant_msgs)

    def test_system_message_preserved(self):
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "a" * 100},
            {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "read"}}]},
            {"role": "tool", "content": "b" * 200, "tool_call_id": "call_1"},
        ]
        result = compact_messages(msgs, keep_recent=1, escalation_level=1)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are a helpful assistant."

    def test_no_compaction_markers(self):
        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "a" * 100},
            {"role": "tool", "content": "b" * 200, "tool_call_id": "call_1"},
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "done"},
        ]
        result = compact_messages(msgs, keep_recent=2, escalation_level=0)
        markers = [m for m in result if "[Context compaction:" in (m.get("content") or "")]
        assert len(markers) == 0

    def test_no_compaction_when_within_limit(self):
        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        result = compact_messages(msgs, keep_recent=20)
        assert result == msgs


class TestCheckContextLimit:
    def test_returns_ok_when_under_threshold(self):
        msgs = [{"role": "user", "content": "hello"}]
        result = check_context_limit(msgs, context_window=128000)
        assert result["severity"] == "ok"
        assert result["needs_compaction"] is False
        assert result["total_tokens"] > 0
        assert result["usage_pct"] < 10

    def test_warning_at_75_percent(self):
        big_content = "hello world\n" * 5000
        msgs = [{"role": "user", "content": big_content}]
        result = check_context_limit(msgs, context_window=1000)
        assert result["severity"] in ("warning", "critical")

    def test_critical_at_90_percent(self):
        huge_content = "x\n" * 20000
        msgs = [{"role": "user", "content": huge_content}]
        result = check_context_limit(msgs, context_window=500)
        assert result["severity"] == "critical"

    def test_returns_usage_pct(self):
        msgs = [{"role": "user", "content": "hello hello hello"}]
        result = check_context_limit(msgs, context_window=1000)
        assert result["usage_pct"] >= 0
        assert result["usage_pct"] <= 100

    def test_respects_tool_schemas(self):
        msgs = [{"role": "user", "content": "hello"}]
        schemas = [{"name": "read", "description": "x" * 1000, "parameters": {"type": "object", "properties": {}}}]
        without = check_context_limit(msgs)
        with_s = check_context_limit(msgs, tool_schemas=schemas)
        assert with_s["total_tokens"] >= without["total_tokens"]


class TestExtractConversationText:
    def test_skips_system_messages(self):
        msgs = [
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": "Hello"},
        ]
        text = _extract_conversation_text(msgs)
        assert "system" not in text.lower() or "You are" not in text
        assert "User: Hello" in text

    def test_includes_user_and_assistant(self):
        msgs = [
            {"role": "user", "content": "Read this file"},
            {"role": "assistant", "content": "Sure, let me read it."},
        ]
        text = _extract_conversation_text(msgs)
        assert "User: Read this file" in text
        assert "Assistant: Sure, let me read it." in text

    def test_summarizes_tool_calls(self):
        msgs = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"file_path": "test.py"}'}}
            ]},
        ]
        text = _extract_conversation_text(msgs)
        assert "Called:" in text
        assert "read" in text

    def test_includes_tool_results(self):
        msgs = [
            {"role": "tool", "content": "File contents here", "tool_call_id": "call_1"},
        ]
        text = _extract_conversation_text(msgs)
        assert "Tool Result" in text
        assert "File contents here" in text

    def test_truncates_long_tool_output(self):
        msgs = [
            {"role": "tool", "content": "x" * 5000, "tool_call_id": "call_1"},
        ]
        text = _extract_conversation_text(msgs)
        assert "[truncated]" in text


class TestStripMedia:
    def test_no_media_unchanged(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = strip_media_from_messages(msgs)
        assert len(result) == 2
        assert result[0]["content"] == "Hello"

    def test_strips_image_url(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this screenshot:"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
                ],
            },
        ]
        result = strip_media_from_messages(msgs)
        content = result[0]["content"]
        assert isinstance(content, str)
        assert "screenshot" in content
        assert "Image removed" in content
        assert "base64" not in content

    def test_preserves_text_parts(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Part 1"},
                    {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
                    {"type": "text", "text": "Part 2"},
                ],
            },
        ]
        result = strip_media_from_messages(msgs)
        content = result[0]["content"]
        assert "Part 1" in content
        assert "Part 2" in content


class TestExtractMultipart:
    def test_flattens_image_attachments_to_marker(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What does this diagram show?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,xyz"}},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            },
        ]
        text = _extract_conversation_text(msgs)
        assert "User: What does this diagram show?" in text
        assert "2 image attachment(s)" in text
        assert "base64" not in text

    def test_text_only_multipart(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "Only text here"}]}]
        text = _extract_conversation_text(msgs)
        assert "Only text here" in text
