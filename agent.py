import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

import openai

from config import Config
from llm import LLMClient, TextDelta, ToolCall, Finish, LLMEvent
from prompts import build_system_prompt, build_openai_messages
from session import Session
from tools import ToolRegistry

log = logging.getLogger(__name__)


@dataclass
class AgentEvent:
    type: str
    data: dict = field(default_factory=dict)


class Agent:
    def __init__(self, config: Config, session: Session, tools: ToolRegistry):
        self.config = config
        self.session = session
        self.tools = tools
        self.llm = LLMClient(config.llm)
        self.system_prompt = build_system_prompt(config.workspace, config.llm.model)
        self.cancel_event = asyncio.Event()

    def cancel(self):
        self.cancel_event.set()

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        self.cancel_event.clear()
        await self.session.add_message("user", user_message)

        try:
            async for event in self._loop(user_message):
                if self.cancel_event.is_set():
                    yield AgentEvent("cancelled")
                    yield AgentEvent("done")
                    return
                yield event
        except openai.APIConnectionError:
            msg = f"Could not connect to LLM at {self.config.llm.base_url or 'api.openai.com'}. Is the server running?"
            log.error(msg)
            yield AgentEvent("error", {"message": msg})
            yield AgentEvent("done")
        except openai.AuthenticationError as e:
            msg = f"Authentication failed: {e.message}"
            log.error(msg)
            yield AgentEvent("error", {"message": msg})
            yield AgentEvent("done")
        except openai.APIStatusError as e:
            msg = f"LLM API error (HTTP {e.status_code}): {e.message}"
            log.error(msg)
            yield AgentEvent("error", {"message": msg})
            yield AgentEvent("done")
        except Exception as e:
            msg = f"Unexpected error: {type(e).__name__}: {e}"
            log.exception(msg)
            yield AgentEvent("error", {"message": msg})
            yield AgentEvent("done")

    async def _loop(self, user_message: str) -> AsyncIterator[AgentEvent]:
        for iteration in range(self.config.agent.max_iterations):
            if self.cancel_event.is_set():
                return

            history = await self.session.get_messages()
            messages = build_openai_messages(self.system_prompt, history)
            tool_schemas = self.tools.schemas()
            openai_tools = self.llm.format_tools(tool_schemas) if tool_schemas else None

            accumulated_text = ""
            tool_calls: list[ToolCall] = []

            async for event in self.llm.stream(messages, openai_tools):
                if self.cancel_event.is_set():
                    return
                if isinstance(event, TextDelta):
                    accumulated_text += event.content
                    yield AgentEvent("text_delta", {"content": event.content})

                elif isinstance(event, ToolCall):
                    tool_calls.append(event)
                    yield AgentEvent("tool_call", {
                        "id": event.id,
                        "name": event.name,
                        "arguments": event.arguments,
                    })

                elif isinstance(event, Finish):
                    yield AgentEvent("finish", {
                        "reason": event.finish_reason,
                        "usage": {
                            "prompt_tokens": event.usage.prompt_tokens,
                            "completion_tokens": event.usage.completion_tokens,
                        },
                    })

            if tool_calls:
                tc_dicts = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                    for tc in tool_calls
                ]
                await self.session.add_message(
                    "assistant",
                    content=accumulated_text or None,
                    tool_calls=tc_dicts,
                )

                for tc in tool_calls:
                    if self.cancel_event.is_set():
                        return
                    result = await self.tools.execute(tc.name, tc.arguments)
                    await self.session.add_message("tool", content=result, tool_call_id=tc.id)
                    yield AgentEvent("tool_result", {"id": tc.id, "name": tc.name, "output": result})

                continue

            if accumulated_text:
                await self.session.add_message("assistant", content=accumulated_text)

            break

        yield AgentEvent("done")
