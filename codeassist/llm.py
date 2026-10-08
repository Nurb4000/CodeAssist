import asyncio
import copy
import json
import logging
import os
import random
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import openai

from .config import LLMConfig

log = logging.getLogger(__name__)

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0
JITTER_FACTOR = 0.2


class ModerationBlocked(Exception):
    """Raised when a provider's content-moderation layer rejects a request.

    Hosted providers signal this with an HTTP 400 carrying a moderation error
    code rather than a dedicated status — e.g. Qwen/DashScope returns
    ``data_inspection_failed`` for a blocked prompt or completion. Without this
    the block is indistinguishable from any other 400 and reaches the user as
    an opaque "LLM API error (HTTP 400)". Splitting the code out of the raw
    provider string lets the agent explain what was blocked instead of echoing
    a vendor message verbatim.
    """

    def __init__(self, code: str, explanation: str, *, status_code: int | None = None):
        self.code = code
        self.explanation = explanation
        self.status_code = status_code
        super().__init__(f"{code}: {explanation}")


# Keyed by the code with separators stripped, because providers are inconsistent
# about casing and separators: Qwen returns both `data_inspection_failed` in
# prose and `DataInspectionFailed` in machine-readable payloads for the same
# condition. Values are plain-language explanations written here rather than
# reused from the provider so the UI never shows a raw vendor string.
_MODERATION_CODES: dict[str, str] = {
    "datainspectionfailed": (
        "The provider's content-moderation layer flagged this request as "
        "potentially inappropriate content."
    ),
    "ipinfringementsuspect": (
        "The provider's moderation layer flagged this request as suspected "
        "intellectual-property infringement."
    ),
    "customroleblocked": (
        "This request was blocked by a custom moderation policy configured on "
        "the provider account."
    ),
    "faqruleblocked": (
        "This request was blocked by a configured FAQ-rule intervention."
    ),
}

_CODE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,}")


def classify_moderation_error(exc: BaseException) -> tuple[str, str] | None:
    """Return ``(code, explanation)`` if *exc* is a provider moderation block.

    Matches on any code token found in the error's string form or parsed body,
    so it works against both prose and JSON error payloads without coupling to
    one provider's exact envelope.
    """
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body:
        parts.append(str(body))
    for token in _CODE_TOKEN_RE.findall(" ".join(parts)):
        explanation = _MODERATION_CODES.get(re.sub(r"[^a-z0-9]", "", token.lower()))
        if explanation:
            return token, explanation
    return None


class ContextWindowExceeded(Exception):
    """Raised when the provider rejects a request because the prompt plus the
    requested completion does not fit the model's context window.

    This is a *recoverable* condition, unlike a generic API failure: the caller
    can compact the history and replay the request. It is surfaced as its own
    exception because the rejection arrives as a bare ``openai.APIError`` whose
    only distinguishing feature is the message text, and without this it reached
    the user as "Unexpected error: APIError: Context size has been exceeded"
    instead of compacting and continuing.
    """

    def __init__(self, detail: str = "", *, status_code: int | None = None):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail or "context window exceeded")


# Providers word this rejection very differently, and the local backends the
# project targets are the least standard of them, so match on a set of phrases
# rather than one envelope. Lowercased comparison.
_CONTEXT_ERROR_PHRASES = (
    "context size has been exceeded",        # llama.cpp
    "exceeds the available context size",    # llama.cpp (newer builds)
    "context window",                        # generic
    "context_length_exceeded",               # OpenAI error code
    "maximum context length",                # OpenAI / vLLM
    "context limit",                         # vLLM / Ollama
    "requested tokens exceed",               # vLLM
    "reduce the length of the messages",     # OpenAI guidance text
    "input is too long",                     # Ollama
    "prompt is too long",                    # Ollama / various
    "n_ctx",                                 # llama.cpp KV cache
    "exceeds the model's context",           # phrasing variants
)


def is_context_length_error(exc: BaseException) -> bool:
    """Return True if *exc* is a provider rejecting the prompt for exceeding
    the context window.

    Inspects the exception string, its ``body``/``message`` attributes and the
    HTTP status, so it works for both prose and JSON error payloads without
    coupling to one provider's exact envelope.
    """
    parts = [str(exc)]
    for attr in ("body", "message", "code"):
        val = getattr(exc, attr, None)
        if val:
            parts.append(str(val))
    haystack = " ".join(parts).lower()
    return any(phrase in haystack for phrase in _CONTEXT_ERROR_PHRASES)


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


class ProviderTransform:
    """Middleware that can modify LLM requests and responses.

    Subclasses override ``transform_request`` to modify the request kwargs
    before sending, and ``transform_event`` to modify events after receiving.
    Transforms are applied in registration order.
    """

    async def transform_request(self, kwargs: dict) -> dict:
        """Modify request kwargs before sending to the LLM.

        Return the (possibly modified) kwargs dict.
        """
        return kwargs

    async def transform_event(self, event: LLMEvent) -> LLMEvent | None:
        """Modify or filter an event after receiving from the LLM.

        Return the (possibly modified) event, or None to suppress it.
        """
        return event


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
        self._transforms: list[ProviderTransform] = []

    def add_transform(self, transform: ProviderTransform):
        """Add a provider transform to the pipeline.

        Transforms are applied in registration order. Request transforms run
        before the LLM call; event transforms run after each event is received.
        """
        self._transforms.append(transform)

    def remove_transform(self, transform: ProviderTransform):
        """Remove a previously registered provider transform."""
        if transform in self._transforms:
            self._transforms.remove(transform)

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

        # Apply request transforms (pipeline runs in registration order)
        for transform in self._transforms:
            kwargs = await transform.transform_request(kwargs)

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
                # A context-window rejection is recoverable: the caller can
                # compact the history and replay. Surface it as its own
                # exception instead of burning the backoff budget (a retry with
                # the same oversized prompt can never succeed) and instead of
                # letting it reach the user as a raw APIError.
                if is_context_length_error(e):
                    log.warning("LLM request exceeded the context window: %s", e)
                    raise ContextWindowExceeded(
                        str(e), status_code=getattr(e, "status_code", None)
                    ) from e
                # A moderation block is a deterministic 400 — retrying cannot
                # change the outcome, so surface it as a distinct exception
                # instead of burning the backoff budget on it.
                moderation = classify_moderation_error(e)
                if moderation is not None:
                    code, explanation = moderation
                    log.warning("LLM request blocked by provider moderation: %s", code)
                    raise ModerationBlocked(
                        code,
                        explanation,
                        status_code=getattr(e, "status_code", None),
                    ) from e
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
                            event = await self._apply_event_transforms(TextDelta(delta_text))
                            if event is not None:
                                yield event

                        # Check reasoning_content independently — models may emit
                        # both content and reasoning_content in the same chunk.
                        reasoning = getattr(choice.delta, "reasoning_content", None)
                        if isinstance(reasoning, str) and reasoning:
                            event = await self._apply_event_transforms(ReasoningDelta(reasoning))
                            if event is not None:
                                yield event

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
                        event = await self._apply_event_transforms(
                            Finish(
                                finish_reason=choice.finish_reason if choice else "stop",
                                usage=Usage(
                                    prompt_tokens=chunk.usage.prompt_tokens or 0,
                                    completion_tokens=chunk.usage.completion_tokens or 0,
                                ),
                            )
                        )
                        if event is not None:
                            yield event
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
            event = await self._apply_event_transforms(
                ToolCall(id=tc["id"], name=tc["name"], arguments=args)
            )
            if event is not None:
                yield event

    async def _apply_event_transforms(self, event: LLMEvent) -> LLMEvent | None:
        """Run an event through the transform pipeline.

        Returns the (possibly modified) event, or None to suppress it.
        """
        for transform in self._transforms:
            event = await transform.transform_event(event)
            if event is None:
                return None
        return event

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
