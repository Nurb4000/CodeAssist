import asyncio
import copy
import json
import logging
import os
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import openai

from .config import LLMConfig

log = logging.getLogger(__name__)

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0
JITTER_FACTOR = 0.2


@dataclass
class TextDelta:
    content: str


@dataclass
class ReasoningDelta:
    """Reasoning/thinking text emitted separately from the final answer.

    Some reasoning models (e.g. llama.cpp Ornith) reply via
    ``reasoning_content`` with empty ``content``; this lets the client render
    thinking in a collapsible block instead of inline with the answer.
    """

    content: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    tool_call_id: str
    content: str


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class Finish:
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)


LLMEvent = TextDelta | ReasoningDelta | ToolCall | ToolResult | Finish


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        # The OpenAI SDK rejects an empty or missing key at construction, but
        # no-auth backends (e.g. a local/on-prem llama.cpp) are often configured
        # with an empty key. Fall back to the OPENAI_API_KEY env var, then a
        # placeholder sentinel so the client still constructs and the request
        # goes through unauthenticated instead of raising "Missing credentials".
        api_key = config.api_key or os.environ.get("OPENAI_API_KEY")
        kwargs = {"api_key": api_key or "sk-no-auth"}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        # Honor the configured LLM timeout so slow local servers (llama.cpp,
        # vLLM) get more than the SDK's own generous default only when asked.
        kwargs["timeout"] = config.timeout
        self.client = openai.AsyncOpenAI(**kwargs)

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        kwargs = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.config.frequency_penalty:
            kwargs["frequency_penalty"] = self.config.frequency_penalty
        if self.config.presence_penalty:
            kwargs["presence_penalty"] = self.config.presence_penalty
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = None
        backoff = INITIAL_BACKOFF

        for attempt in range(MAX_RETRIES):
            try:
                response = await self.client.chat.completions.create(**kwargs)
                break
            except openai.APITimeoutError as e:
                log.warning("LLM timeout (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)
                if attempt < MAX_RETRIES - 1:
                    jitter = backoff * random.uniform(-JITTER_FACTOR, JITTER_FACTOR)
                    sleep_time = max(0.1, backoff + jitter)
                    log.info("Retrying in %.1fs...", sleep_time)
                    await asyncio.sleep(sleep_time)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                else:
                    raise
            except openai.APIConnectionError as e:
                log.warning("LLM connection error (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)
                if attempt < MAX_RETRIES - 1:
                    jitter = backoff * random.uniform(-JITTER_FACTOR, JITTER_FACTOR)
                    sleep_time = max(0.1, backoff + jitter)
                    log.info("Retrying in %.1fs...", sleep_time)
                    await asyncio.sleep(sleep_time)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                else:
                    raise
            except openai.RateLimitError as e:
                log.warning("LLM rate limit hit (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)
                if attempt < MAX_RETRIES - 1:
                    retry_after = float(e.headers.get("retry-after", backoff)) if hasattr(e, 'headers') else backoff
                    jitter = retry_after * random.uniform(-JITTER_FACTOR, JITTER_FACTOR)
                    sleep_time = max(0.1, retry_after + jitter)
                    log.info("Rate limited. Retrying in %.1fs...", sleep_time)
                    await asyncio.sleep(sleep_time)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                else:
                    raise
            except openai.APIError as e:
                log.error("LLM API error: %s", e)
                raise

        if response is None:
            raise RuntimeError("LLM connection failed after all retry attempts")

        current_tool_calls: dict[int, dict] = {}
        stream_backoff = INITIAL_BACKOFF

        for stream_attempt in range(MAX_RETRIES):
            try:
                async for chunk in response:
                    choice = chunk.choices[0] if chunk.choices else None

                    if choice and choice.delta:
                        # Reasoning models (e.g. llama.cpp Ornith) can emit the
                        # reply in reasoning_content with empty content. Route
                        # those deltas to a separate ReasoningDelta event so the
                        # client can render thinking in a collapsible block
                        # instead of inline with the answer (review item D2).
                        delta_text = choice.delta.content or ""
                        if delta_text:
                            yield TextDelta(delta_text)
                        else:
                            reasoning = getattr(choice.delta, "reasoning_content", None)
                            if isinstance(reasoning, str) and reasoning:
                                yield ReasoningDelta(reasoning)

                        if choice.delta.tool_calls:
                            for tc_delta in choice.delta.tool_calls:
                                idx = tc_delta.index
                                if idx not in current_tool_calls:
                                    current_tool_calls[idx] = {
                                        "id": tc_delta.id or "",
                                        "name": "",
                                        "arguments": "",
                                    }
                                if tc_delta.id:
                                    current_tool_calls[idx]["id"] = tc_delta.id
                                if tc_delta.function:
                                    if tc_delta.function.name:
                                        current_tool_calls[idx]["name"] = tc_delta.function.name
                                    if tc_delta.function.arguments:
                                        current_tool_calls[idx]["arguments"] += tc_delta.function.arguments

                    if chunk.usage:
                        yield Finish(
                            finish_reason=choice.finish_reason if choice else "stop",
                            usage=Usage(
                                prompt_tokens=chunk.usage.prompt_tokens or 0,
                                completion_tokens=chunk.usage.completion_tokens or 0,
                            ),
                        )
                break
            except (openai.APIConnectionError, openai.APITimeoutError, asyncio.TimeoutError) as e:  # noqa: UP041 — keep openai's specific timeout error, not just the builtin
                log.warning("LLM stream interrupted (attempt %d/%d): %s", stream_attempt + 1, MAX_RETRIES, e)
                if stream_attempt < MAX_RETRIES - 1:
                    jitter = stream_backoff * random.uniform(-JITTER_FACTOR, JITTER_FACTOR)
                    sleep_time = max(0.1, stream_backoff + jitter)
                    log.info("Stream reconnecting in %.1fs...", sleep_time)
                    await asyncio.sleep(sleep_time)
                    stream_backoff = min(stream_backoff * 2, MAX_BACKOFF)
                    # Re-create the response to retry streaming
                    response = await self.client.chat.completions.create(**kwargs)
                else:
                    raise

        for idx in sorted(current_tool_calls.keys()):
            tc = current_tool_calls[idx]
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {"raw": tc["arguments"]}
            yield ToolCall(id=tc["id"], name=tc["name"], arguments=args)

    def format_tools(self, tool_schemas: list[dict]) -> list[dict]:
        encoded = []
        for s in tool_schemas:
            # Per-parameter prose descriptions cost ~1.2k tokens on every request
            # but the model already infers usage from arg name/type/required/enum.
            # Drop only the prose, keep all structural schema so tool-calling
            # reliability is unchanged. Deep-copy so the cached registry schema is
            # never mutated.
            parameters = copy.deepcopy(s.get("parameters") or {})
            for prop in parameters.get("properties", {}).values():
                prop.pop("description", None)
            encoded.append({
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s["description"],
                    "parameters": parameters,
                },
            })
        return encoded
