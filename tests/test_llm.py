"""Tests for LLM client."""
import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from codeassist.llm import LLMClient, TextDelta, ReasoningDelta, ToolCall, Finish, Usage, ToolResult
from codeassist.config import LLMConfig


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
            client = LLMClient(llm_config)
            mock_openai.assert_called_once_with(api_key="test-key")

    def test_init_without_api_key(self):
        """Test initialization without API key."""
        config = LLMConfig(provider="openai", model="gpt-4o")
        with patch("codeassist.llm.openai.AsyncOpenAI") as mock_openai:
            client = LLMClient(config)
            mock_openai.assert_called_once_with()

    def test_init_with_base_url(self, llm_config):
        """Test initialization with custom base URL."""
        llm_config.base_url = "https://custom.api.com/v1"
        with patch("codeassist.llm.openai.AsyncOpenAI") as mock_openai:
            client = LLMClient(llm_config)
            mock_openai.assert_called_once_with(api_key="test-key", base_url="https://custom.api.com/v1")


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
        chunk1.usage = None
        
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.content = " World"
        chunk2.choices[0].delta.tool_calls = None
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
        chunk1.usage = None
        
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta = MagicMock()
        chunk2.choices[0].delta.content = None
        chunk2.choices[0].delta.tool_calls = [tc_delta2]
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
        
        # Should have retried and succeeded
        assert call_count[0] == 3
        assert len(events) == 1
        assert isinstance(events[0], TextDelta)
        assert events[0].content == "Success"


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

        # Nothing to emit (no content, no reasoning, no usage) — must not crash.
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_stream_content_takes_precedence_over_reasoning(self, llm_client):
        """When a delta has real content, it is a TextDelta even if reasoning is
        also present — reasoning is only routed separately when content is empty."""
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

        assert len(events) == 1
        assert isinstance(events[0], TextDelta)
        assert events[0].content == "The answer"
