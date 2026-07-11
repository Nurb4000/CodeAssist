import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

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

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        await self.session.add_message("user", user_message)

        for iteration in range(self.config.agent.max_iterations):
            history = await self.session.get_messages()
            messages = build_openai_messages(self.system_prompt, history)
            tool_schemas = self.tools.schemas()
            openai_tools = self.llm.format_tools(tool_schemas) if tool_schemas else None

            accumulated_text = ""
            tool_calls: list[ToolCall] = []
            finished = False

            async for event in self.llm.stream(messages, openai_tools):
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
                    finished = True
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
                    result = await self.tools.execute(tc.name, tc.arguments)
                    await self.session.add_message("tool", content=result, tool_call_id=tc.id)
                    yield AgentEvent("tool_result", {"id": tc.id, "name": tc.name, "output": result})

                continue

            if accumulated_text:
                await self.session.add_message("assistant", content=accumulated_text)

            break

        yield AgentEvent("done")
