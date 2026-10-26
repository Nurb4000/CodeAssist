"""Tests for LLM client."""
import json
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from codeassist.config import LLMConfig
from codeassist.llm import (
    Finish,
    LLMClient,
    ModerationBlocked,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    Usage,
    classify_moderation_error,
)


@pytest.fixture
def llm_config():
    """Create a test LLM config."""
    return LLMConfig(
        provider="openai",
        model="gpt-4o",
        api_key="test-key",
        temperature=0.0,
        max_tokens=4096,
    )


@pytest.fixture
def llm_client(llm_config):
    """Create an LLM client with mocked OpenAI."""
    with patch("codeassist.llm.openai.AsyncOpenAI") as mock_openai:
        mock_openai.return_value = MagicMock()
        client = LLMClient(llm_config)
        client.client = mock_openai.return_value
        return client


class TestLLMClientInit:
    """Test LLM client initialization."""

    def test_init_with_api_key(self, llm_config):
        """Test initialization with API key."""
        with patch("codeassist.llm.openai.AsyncOpenAI") as mock_openai:
            LLMClient(llm_config)
            mock_openai.assert_called_once_with(api_key="test-key", timeout=900)

    def test_init_without_api_key_uses_placeholder(self, monkeypatch):
        """A no-auth backend (empty key, no env var) must still construct. The
        OpenAI SDK rejects a missing key, so we fall back to a placeholder
        sentinel that unauthenticated servers (e.g. llama.cpp) ignore."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = LLMConfig(provider="openai", model="gpt-4o")  # api_key == ""
        with patch("codeassist.llm.openai.AsyncOpenAI") as mock_openai:
            LLMClient(config)
            mock_openai.assert_called_once_with(api_key="sk-no-auth", timeout=900)

    def test_init_prefers_env_var_over_placeholder(self, monkeypatch):
        """When no key is configured but OPENAI_API_KEY is set, use it rather
        than the placeholder sentinel."""
        monkeypatch.setenv("OPENAI_API_KEY", "from-env")
        config = LLMConfig(provider="openai", model="gpt-4o")
        with patch("codeassist.llm.openai.AsyncOpenAI") as mock_openai:
            LLMClient(config)
            mock_openai.assert_called_once_with(api_key="from-env", timeout=900)

    def test_init_with_base_url(self, llm_config):
        """Test initialization with custom base URL."""
        llm_config.base_url = "https://custom.api.com/v1"
        with patch("codeassist.llm.openai.AsyncOpenAI") as mock_openai:
            LLMClient(llm_config)
            mock_openai.assert_called_once_with(
                api_key="test-key", base_url="https://custom.api.com/v1", timeout=900
            )

    def test_init_with_custom_timeout(self):
        """A configured timeout is passed through to the SDK client."""
        config = LLMConfig(provider="openai", model="gpt-4o", api_key="test-key", timeout=720)
        with patch("codeassist.llm.openai.AsyncOpenAI") as mock_openai:
            LLMClient(config)
            mock_openai.assert_called_once_with(api_key="test-key", timeout=720)


class TestFormatTools:
    """Test format_tools method."""

    def test_format_empty_tools(self, llm_client):
        """Test formatting empty tool list."""
        result = llm_client.format_tools([])
        assert result == []

    def test_format_single_tool(self, llm_client):
        """Test formatting a single tool."""
        tools = [
            {
                "name": "read",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ]
        
        result = llm_client.format_tools(tools)
        
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "read"
        assert result[0]["function"]["description"] == "Read a file"
        assert "parameters" in result[0]["function"]

    def test_format_multiple_tools(self, llm_client):
        """Test formatting multiple tools."""
        tools = [
            {"name": "read", "description": "Read", "parameters": {}},
            {"name": "write", "description": "Write", "parameters": {}},
        ]
        
        result = llm_client.format_tools(tools)
        
        assert len(result) == 2
        assert result[0]["function"]["name"] == "read"
        assert result[1]["function"]["name"] == "write"

    def test_format_strips_param_descriptions_but_keeps_structure(self, llm_client):
        """Per-parameter prose is trimmed from the emitted schema while all
        structural info (type/required/enum/nested props) is preserved, and the
        input schema is never mutated."""
        tools = [
            {
                "name": "read",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path to the file"},
                        "mode": {"type": "string", "enum": ["r", "rb"], "description": "Open mode"},
                    },
                    "required": ["path"],
                },
            }
        ]
        before = json.dumps(tools, sort_keys=True)

        result = llm_client.format_tools(tools)

        prop = result[0]["function"]["parameters"]["properties"]
        assert "description" not in prop["path"]
        assert "description" not in prop["mode"]
        # Structural info survives.
        assert prop["path"]["type"] == "string"
        assert prop["mode"]["enum"] == ["r", "rb"]
        assert result[0]["function"]["parameters"]["required"] == ["path"]
        # Input was deep-copied, not mutated in place.
        assert json.dumps(tools, sort_keys=True) == before
        assert "description" in tools[0]["parameters"]["properties"]["path"]


class TestStream:
    """Test stream method."""

    @pytest.mark.asyncio
    async def test_stream_text_response(self, llm_client):
        """Test streaming a text response."""
        # Create mock chunks
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta = MagicMock()
        chunk1.choices[0].delta.content = "Hello"
        chunk1.choices[0].delta.tool_calls = None
        chunk1.choices[0].finish_reason = None  # intermediate chunk: no finish yet
        chunk1.usage = None
        
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.content = " World"
        chunk2.choices[0].delta.tool_calls = None
        chunk2.choices[0].finish_reason = None  # intermediate chunk: no finish yet
        chunk2.usage = None
        
        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta = MagicMock()
        chunk3.choices[0].delta.content = None
        chunk3.choices[0].delta.tool_calls = None
        chunk3.choices[0].finish_reason = "stop"
        chunk3.usage = MagicMock()
        chunk3.usage.prompt_tokens = 10
        chunk3.usage.completion_tokens = 5
        
        async def mock_create(*args, **kwargs):
            async def stream_response():
                yield chunk1
                yield chunk2
                yield chunk3
            return stream_response()
        
        llm_client.client.chat.completions.create = mock_create
        
        events = []
        async for event in llm_client.stream([{"role": "user", "content": "Hi"}]):
            events.append(event)
        
        # Should have text deltas and a finish event
        assert len(events) == 3
        assert isinstance(events[0], TextDelta)
        assert events[0].content == "Hello"
        assert isinstance(events[1], TextDelta)
        assert events[1].content == " World"
        assert isinstance(events[2], Finish)
        assert events[2].finish_reason == "stop"
        assert events[2].usage.prompt_tokens == 10
        assert events[2].usage.completion_tokens == 5

    @pytest.mark.asyncio
    async def test_stream_finish_emitted_when_usage_omitted(self, llm_client):
        """A provider that reports finish_reason but omits usage must still emit
        a Finish event. Gating Finish on chunk.usage dropped it entirely for such
        chunks (local backends and content-filter blocks omit usage), so cost
        tracking and refusal/safety-filter handling never ran and the
        finish_reason was lost."""
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta = MagicMock()
        chunk1.choices[0].delta.content = "Blocked"
        chunk1.choices[0].delta.tool_calls = None
        chunk1.choices[0].finish_reason = None
        chunk1.usage = None

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.content = None
        chunk2.choices[0].delta.tool_calls = None
        chunk2.choices[0].finish_reason = "content_filter"
        chunk2.usage = None

        async def mock_create(*args, **kwargs):
            async def stream_response():
                yield chunk1
                yield chunk2
            return stream_response()

        llm_client.client.chat.completions.create = mock_create

        events = []
        async for event in llm_client.stream([{"role": "user", "content": "Hi"}]):
            events.append(event)

        assert len(events) == 2
        assert isinstance(events[0], TextDelta)
        assert isinstance(events[1], Finish)
        assert events[1].finish_reason == "content_filter"
        assert events[1].usage.prompt_tokens == 0
        assert events[1].usage.completion_tokens == 0

    @pytest.mark.asyncio
    async def test_stream_tool_call(self, llm_client):
        """Test streaming a tool call."""
        # Create mock tool call delta
        tc_delta1 = MagicMock()
        tc_delta1.index = 0
        tc_delta1.id = "call_123"
        tc_delta1.function = MagicMock()
        tc_delta1.function.name = "read"
        tc_delta1.function.arguments = '{"path": "'
        
        tc_delta2 = MagicMock()
        tc_delta2.index = 0
        tc_delta2.id = None
        tc_delta2.function = MagicMock()
        tc_delta2.function.name = None
        tc_delta2.function.arguments = 'test.py"}'
        
        # Create mock chunks with tool call
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta = MagicMock()
        chunk1.choices[0].delta.content = None
        chunk1.choices[0].delta.tool_calls = [tc_delta1]
        chunk1.choices[0].finish_reason = None  # intermediate chunk: no finish yet
        chunk1.usage = None
        
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.content = None
        chunk2.choices[0].delta.tool_calls = [tc_delta2]
        chunk2.choices[0].finish_reason = None  # intermediate chunk: no finish yet
        chunk2.usage = None
        
        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta = MagicMock()
        chunk3.choices[0].delta.content = None
        chunk3.choices[0].delta.tool_calls = None
        chunk3.choices[0].finish_reason = "tool_calls"
        chunk3.usage = MagicMock()
        chunk3.usage.prompt_tokens = 20
        chunk3.usage.completion_tokens = 10
        
        async def mock_create(*args, **kwargs):
            async def stream_response():
                yield chunk1
                yield chunk2
                yield chunk3
            return stream_response()
        
        llm_client.client.chat.completions.create = mock_create
        
        events = []
        async for event in llm_client.stream([{"role": "user", "content": "Read test.py"}], tools=[]):
            events.append(event)
        
        # Should have a finish event and a tool call event (finish comes first from stream)
        assert len(events) == 2
        assert isinstance(events[0], Finish)
        assert events[0].finish_reason == "tool_calls"
        assert isinstance(events[1], ToolCall)
        assert events[1].id == "call_123"
        assert events[1].name == "read"
        assert events[1].arguments == {"path": "test.py"}

    @pytest.mark.asyncio
    async def test_stream_invalid_json_arguments(self, llm_client):
        """Test handling of invalid JSON in tool arguments."""
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta = MagicMock()
        chunk1.choices[0].delta.content = None
        chunk1.choices[0].delta.tool_calls = [MagicMock()]
        chunk1.choices[0].delta.tool_calls[0].index = 0
        chunk1.choices[0].delta.tool_calls[0].id = "call_456"
        chunk1.choices[0].delta.tool_calls[0].function = MagicMock()
        chunk1.choices[0].delta.tool_calls[0].function.name = "test"
        chunk1.choices[0].delta.tool_calls[0].function.arguments = "invalid json"
        chunk1.choices[0].finish_reason = None  # intermediate chunk: no finish yet
        chunk1.usage = None
        
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.content = None
        chunk2.choices[0].delta.tool_calls = None
        chunk2.choices[0].finish_reason = "tool_calls"
        chunk2.usage = MagicMock()
        chunk2.usage.prompt_tokens = 5
        chunk2.usage.completion_tokens = 3
        
        async def mock_create(*args, **kwargs):
            async def stream_response():
                yield chunk1
                yield chunk2
            return stream_response()
        
        llm_client.client.chat.completions.create = mock_create
        
        events = []
        async for event in llm_client.stream([{"role": "user", "content": "Test"}], tools=[]):
            events.append(event)
        
        # Should have finish and tool call with raw arguments
        assert len(events) == 2
        assert isinstance(events[0], Finish)
        assert isinstance(events[1], ToolCall)
        assert events[1].arguments == {"raw": "invalid json"}

    @pytest.mark.asyncio
    async def test_stream_connection_error_retry(self, llm_client):
        """Test retry on connection error."""
        # First two calls fail, third succeeds
        call_count = [0]
        
        async def mock_create(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise openai.APIConnectionError(
                    request=MagicMock(),
                    message="Connection error",
                )
            
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = "Success"
            chunk.choices[0].finish_reason = "stop"
            chunk.usage = None
            
            async def stream_response():
                yield chunk
            
            return stream_response()
        
        llm_client.client.chat.completions.create = mock_create
        
        events = []
        async for event in llm_client.stream([{"role": "user", "content": "Test"}]):
            events.append(event)
        
        # Should have retried and succeeded. The final chunk reports a
        # finish_reason but omits usage (§5): a Finish is now emitted so cost
        # tracking and finish handling run even without usage.
        assert call_count[0] == 3
        assert len(events) == 2
        assert isinstance(events[0], TextDelta)
        assert events[0].content == "Success"
        assert isinstance(events[1], Finish)
        assert events[1].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_rate_limit_honors_numeric_retry_after(self, llm_client):
        """A numeric Retry-After header (seconds) is honored as the retry delay,
        not silently ignored in favor of the fixed backoff (§5)."""
        call_count = [0]
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

        def _rate_limited(delay):
            resp = httpx.Response(429, headers={"retry-after": str(delay)}, request=req)
            return openai.RateLimitError("rate limited", response=resp, body=None)

        async def mock_create(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise _rate_limited(5)
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = "OK"
            chunk.choices[0].finish_reason = "stop"
            chunk.usage = None

            async def stream_response():
                yield chunk

            return stream_response()

        llm_client.client.chat.completions.create = mock_create

        with patch("codeassist.llm.asyncio.sleep") as mock_sleep:
            async for _ in llm_client.stream([{"role": "user", "content": "Hi"}]):
                pass

        assert call_count[0] == 3
        slept = mock_sleep.call_args_list[0].args[0]
        # Honored Retry-After (~5s, with jitter) vs the ignored case (~1s backoff).
        assert 3.0 < slept < 7.0, f"expected the provider's ~5s Retry-After to be honored, slept {slept}"

    @pytest.mark.asyncio
    async def test_rate_limit_httpdate_retry_after_does_not_crash(self, llm_client):
        """An HTTP-date Retry-After ("Wed, 21 Oct 2026 ...") must not crash the
        handler: float() raises ValueError on a date. Fall back to the backoff
        budget and retry (§5)."""
        call_count = [0]
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

        def _rate_limited():
            resp = httpx.Response(
                429, headers={"retry-after": "Wed, 21 Oct 2026 07:22:00 GMT"}, request=req
            )
            return openai.RateLimitError("rate limited", response=resp, body=None)

        async def mock_create(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise _rate_limited()
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = "OK"
            chunk.choices[0].finish_reason = "stop"
            chunk.usage = None

            async def stream_response():
                yield chunk

            return stream_response()

        llm_client.client.chat.completions.create = mock_create

        with patch("codeassist.llm.asyncio.sleep"):
            events = []
            async for event in llm_client.stream([{"role": "user", "content": "Hi"}]):
                events.append(event)

        # No ValueError propagated; the retry succeeded after 3 attempts.
        assert call_count[0] == 3
        texts = [e.content for e in events if isinstance(e, TextDelta)]
        assert texts == ["OK"]

    @pytest.mark.asyncio
    async def test_rate_limit_caps_hostile_retry_after(self, llm_client):
        """A hostile Retry-After header (seconds) must be capped at the backoff
        ceiling so it can't stall the turn for hours across retries (§5)."""
        from codeassist.llm import JITTER_FACTOR, MAX_BACKOFF

        call_count = [0]
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

        def _rate_limited():
            resp = httpx.Response(429, headers={"retry-after": "3600"}, request=req)
            return openai.RateLimitError("rate limited", response=resp, body=None)

        async def mock_create(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise _rate_limited()
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = "OK"
            chunk.choices[0].finish_reason = "stop"
            chunk.usage = None

            async def stream_response():
                yield chunk

            return stream_response()

        llm_client.client.chat.completions.create = mock_create

        with patch("codeassist.llm.asyncio.sleep") as mock_sleep:
            async for _ in llm_client.stream([{"role": "user", "content": "Hi"}]):
                pass

        slept = mock_sleep.call_args_list[0].args[0]
        assert slept <= MAX_BACKOFF * (1 + JITTER_FACTOR), f"Retry-After not capped: slept {slept:.1f}s"

    @pytest.mark.asyncio
    async def test_mid_stream_failure_after_text_is_not_retried(self, llm_client):
        """Once text has been yielded, a reconnect would duplicate it.

        The retry loop re-streams the request from the start, so a mid-stream
        failure after output was already handed to the consumer must surface the
        error instead of replaying (review item §1.7)."""
        call_count = [0]

        def _text_chunk(text):
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = text
            chunk.choices[0].delta.tool_calls = None
            chunk.usage = None
            return chunk

        async def mock_create(*args, **kwargs):
            call_count[0] += 1

            async def stream_response():
                yield _text_chunk("Hello")
                raise openai.APIConnectionError(
                    request=MagicMock(), message="dropped mid-stream"
                )

            return stream_response()

        llm_client.client.chat.completions.create = mock_create

        events = []
        with pytest.raises(openai.APIConnectionError):
            async for event in llm_client.stream([{"role": "user", "content": "Hi"}]):
                events.append(event)

        # Only the first attempt ran (no duplication), and exactly one TextDelta
        # reached the consumer.
        assert call_count[0] == 1
        assert [e.content for e in events if isinstance(e, TextDelta)] == ["Hello"]

    @pytest.mark.asyncio
    async def test_mid_stream_failure_before_output_is_retried_cleanly(self, llm_client):
        """A failure with no output yet is safely retried with a fresh buffer.

        Tool-call fragments from the aborted attempt must NOT be concatenated
        with the replayed ones (review item §1.7)."""
        call_count = [0]

        def _tool_chunk(args_fragment):
            tc = MagicMock()
            tc.index = 0
            tc.id = "call_1"
            tc.function = MagicMock()
            tc.function.name = "read"
            tc.function.arguments = args_fragment
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = None
            chunk.choices[0].delta.tool_calls = [tc]
            chunk.choices[0].finish_reason = None  # intermediate chunk, no finish yet
            chunk.usage = None
            return chunk

        def _finish_chunk():
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = None
            chunk.choices[0].delta.tool_calls = None
            chunk.choices[0].finish_reason = "tool_calls"
            chunk.usage = MagicMock()
            chunk.usage.prompt_tokens = 20
            chunk.usage.completion_tokens = 10
            return chunk

        async def mock_create(*args, **kwargs):
            call_count[0] += 1
            attempt = call_count[0]

            async def stream_response():
                yield _tool_chunk('{"path": "')
                if attempt == 1:
                    # Abort after a partial tool-call fragment, before anything
                    # was emitted (tool calls emit only after a clean finish).
                    raise openai.APIConnectionError(
                        request=MagicMock(), message="dropped mid-stream"
                    )
                yield _tool_chunk('test.py"}')
                yield _finish_chunk()

            return stream_response()

        llm_client.client.chat.completions.create = mock_create

        events = []
        async for event in llm_client.stream([{"role": "user", "content": "go"}], tools=[]):
            events.append(event)

        assert call_count[0] == 2
        tool_calls = [e for e in events if isinstance(e, ToolCall)]
        assert len(tool_calls) == 1
        # Clean arguments — not doubled by the aborted attempt's fragment.
        assert tool_calls[0].arguments == {"path": "test.py"}


class TestLLMEvents:
    """Test LLM event dataclasses."""

    def test_text_delta(self):
        """Test TextDelta creation."""
        delta = TextDelta(content="Hello")
        assert delta.content == "Hello"

    def test_tool_call(self):
        """Test ToolCall creation."""
        tc = ToolCall(id="123", name="read", arguments={"path": "test.py"})
        assert tc.id == "123"
        assert tc.name == "read"
        assert tc.arguments == {"path": "test.py"}

    def test_finish(self):
        """Test Finish creation."""
        usage = Usage(prompt_tokens=10, completion_tokens=5)
        finish = Finish(finish_reason="stop", usage=usage)
        assert finish.finish_reason == "stop"
        assert finish.usage.prompt_tokens == 10

    def test_usage_defaults(self):
        """Test Usage default values."""
        usage = Usage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0

    @pytest.mark.asyncio
    async def test_stream_reasoning_content_emitted_separately(self, llm_client):
        """When content is empty, a reasoning model's delta.reasoning_content
        is surfaced as a ReasoningDelta (not folded into the answer text) so the
        client can render it in a collapsible block (review item D2)."""
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = None
        chunk.choices[0].delta.reasoning_content = "Deep thought then answer"
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = MagicMock()
        chunk.usage.prompt_tokens = 10
        chunk.usage.completion_tokens = 20

        async def mock_create(*args, **kwargs):
            async def stream_response():
                yield chunk
            return stream_response()

        llm_client.client.chat.completions.create = mock_create

        events = []
        async for event in llm_client.stream([{"role": "user", "content": "think"}]):
            events.append(event)

        assert len(events) == 2
        assert isinstance(events[0], ReasoningDelta)
        assert events[0].content == "Deep thought then answer"
        assert isinstance(events[1], Finish)
        assert events[1].usage.completion_tokens == 20

    @pytest.mark.asyncio
    async def test_stream_reasoning_attr_absent_yields_no_text(self, llm_client):
        """A delta with neither content nor reasoning must not crash or emit text."""
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = None
        del chunk.choices[0].delta.reasoning_content  # attribute absent
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = None

        async def mock_create(*args, **kwargs):
            async def stream_response():
                yield chunk
            return stream_response()

        llm_client.client.chat.completions.create = mock_create

        events = []
        async for event in llm_client.stream([{"role": "user", "content": "hi"}]):
            events.append(event)

        # No text or reasoning is emitted for the empty delta (must not crash).
        # A finish_reason is still reported even without usage (§5), so a Finish
        # is now emitted — cost tracking and finish handling run.
        assert [e for e in events if isinstance(e, (TextDelta, ReasoningDelta))] == []
        assert len(events) == 1
        assert isinstance(events[0], Finish)
        assert events[0].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_stream_content_and_reasoning_both_emitted(self, llm_client):
        """When a delta has both content and reasoning_content, both are yielded
        as separate events — TextDelta for content, ReasoningDelta for reasoning."""
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = "The answer"
        chunk.choices[0].delta.reasoning_content = "hidden chain of thought"
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = None

        async def mock_create(*args, **kwargs):
            async def stream_response():
                yield chunk
            return stream_response()

        llm_client.client.chat.completions.create = mock_create

        events = []
        async for event in llm_client.stream([{"role": "user", "content": "go"}]):
            events.append(event)

        # Content and reasoning are yielded as separate events; the finish_reason
        # (with usage omitted, §5) now also emits a Finish.
        assert len(events) == 3
        assert isinstance(events[0], TextDelta)
        assert events[0].content == "The answer"
        assert isinstance(events[1], ReasoningDelta)
        assert events[1].content == "hidden chain of thought"
        assert isinstance(events[2], Finish)
        assert events[2].finish_reason == "stop"


class TestModerationClassification:
    """Detection of provider content-moderation blocks."""

    def test_snake_case_code(self):
        """DashScope-style snake_case code is recognised."""
        exc = openai.BadRequestError(
            "Input or output data may contain inappropriate content.",
            response=MagicMock(status_code=400),
            body={"code": "data_inspection_failed"},
        )
        result = classify_moderation_error(exc)
        assert result is not None
        code, explanation = result
        assert code == "data_inspection_failed"
        assert "moderation" in explanation.lower()

    def test_camel_case_code_in_prose(self):
        """Qwen returns CamelCase in some payloads; both spellings must match."""
        exc = openai.BadRequestError(
            "Error code: DataInspectionFailed",
            response=MagicMock(status_code=400),
            body=None,
        )
        result = classify_moderation_error(exc)
        assert result is not None
        code, explanation = result
        assert code == "DataInspectionFailed"
        assert explanation

    @pytest.mark.parametrize(
        "raw,expected_fragment",
        [
            ("ip_infringement_suspect", "intellectual-property"),
            ("IPInfringementSuspect", "intellectual-property"),
            ("custom_role_blocked", "custom moderation policy"),
            ("CustomRoleBlocked", "custom moderation policy"),
            ("faq_rule_blocked", "FAQ-rule"),
            ("FAQRuleBlocked", "FAQ-rule"),
        ],
    )
    def test_all_known_codes(self, raw, expected_fragment):
        exc = openai.BadRequestError(raw, response=MagicMock(status_code=400), body=None)
        result = classify_moderation_error(exc)
        assert result is not None
        assert expected_fragment in result[1]

    def test_unrelated_bad_request_is_not_moderation(self):
        """An ordinary 400 must not be mislabelled as a safety block."""
        exc = openai.BadRequestError(
            "max_tokens must be greater than 0",
            response=MagicMock(status_code=400),
            body={"code": "invalid_parameter"},
        )
        assert classify_moderation_error(exc) is None

    def test_non_api_exception(self):
        assert classify_moderation_error(ValueError("boom")) is None

    def test_modulation_blocked_carries_context(self):
        err = ModerationBlocked("data_inspection_failed", "blocked", status_code=400)
        assert err.code == "data_inspection_failed"
        assert err.status_code == 400
        assert "data_inspection_failed" in str(err)


class TestModerationBlockedOnStream:
    """A moderation 400 surfaces as ModerationBlocked, not a generic APIError."""

    @pytest.mark.asyncio
    async def test_raises_moderation_blocked(self, llm_client):
        async def mock_create(*args, **kwargs):
            raise openai.BadRequestError(
                "Input data may contain inappropriate content.",
                response=MagicMock(status_code=400),
                body={"code": "data_inspection_failed"},
            )

        llm_client.client.chat.completions.create = mock_create

        with pytest.raises(ModerationBlocked) as exc_info:
            async for _ in llm_client.stream([{"role": "user", "content": "go"}]):
                pass

        assert exc_info.value.code == "data_inspection_failed"

    @pytest.mark.asyncio
    async def test_does_not_retry_moderation_block(self, llm_client):
        """A 400 is deterministic — retrying only wastes the backoff budget."""
        attempts = 0

        async def mock_create(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            raise openai.BadRequestError(
                "blocked",
                response=MagicMock(status_code=400),
                body={"code": "data_inspection_failed"},
            )

        llm_client.client.chat.completions.create = mock_create

        with pytest.raises(ModerationBlocked):
            async for _ in llm_client.stream([{"role": "user", "content": "go"}]):
                pass

        assert attempts == 1
