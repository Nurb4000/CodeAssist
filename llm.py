import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

import openai

from config import LLMConfig

log = logging.getLogger(__name__)


@dataclass
class TextDelta:
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


LLMEvent = TextDelta | ToolCall | ToolResult | Finish


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        kwargs = {"api_key": config.api_key} if config.api_key else {}
        if config.base_url:
            kwargs["base_url"] = config.base_url
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
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await self.client.chat.completions.create(**kwargs)
        except openai.APIError as e:
            log.error("LLM API error: %s", e)
            raise

        current_tool_calls: dict[int, dict] = {}
        accumulated_text = ""

        async for chunk in response:
            choice = chunk.choices[0] if chunk.choices else None

            if choice and choice.delta:
                if choice.delta.content:
                    accumulated_text += choice.delta.content
                    yield TextDelta(choice.delta.content)

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

        for idx in sorted(current_tool_calls.keys()):
            tc = current_tool_calls[idx]
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {"raw": tc["arguments"]}
            yield ToolCall(id=tc["id"], name=tc["name"], arguments=args)

    def format_tools(self, tool_schemas: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s["description"],
                    "parameters": s["parameters"],
                },
            }
            for s in tool_schemas
        ]
