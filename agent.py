import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import openai

from config import Config
from llm import LLMClient, TextDelta, ToolCall, Finish, LLMEvent
from prompts import build_system_prompt, build_openai_messages
from session import Session
from tools import ToolRegistry
from tokens import compact_messages, check_context_limit, truncate_tool_result

log = logging.getLogger(__name__)

# Tools that require user confirmation before execution
CONFIRM_TOOLS = {"write", "edit", "shell"}


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
        self._confirm_events: dict[str, asyncio.Event] = {}
        self._confirm_results: dict[str, bool] = {}
        # Session trust flags
        self._trust_workspace_writes = False
        self._trust_shell = False

    def cancel(self):
        self.cancel_event.set()

    def reset_trust(self):
        """Reset trust flags for new session."""
        self._trust_workspace_writes = False
        self._trust_shell = False

    def set_trust(self, trust_workspace: bool = False, trust_shell: bool = False):
        """Set trust flags from user confirmation."""
        if trust_workspace:
            self._trust_workspace_writes = True
        if trust_shell:
            self._trust_shell = True

    def _is_in_workspace(self, file_path: str) -> bool:
        """Check if a file path is within the workspace."""
        try:
            path = Path(file_path).resolve()
            workspace = self.config.workspace.resolve()
            return str(path).startswith(str(workspace))
        except Exception:
            return False

    def needs_confirmation(self, tool_name: str, arguments: dict) -> bool:
        """Check if a tool call requires user confirmation."""
        if tool_name not in CONFIRM_TOOLS:
            return False

        # Check shell trust
        if tool_name == "shell" and self._trust_shell:
            return False

        # Check workspace write trust
        if tool_name in ("write", "edit") and self._trust_workspace_writes:
            file_path = arguments.get("file_path", arguments.get("path", ""))
            if file_path and self._is_in_workspace(file_path):
                return False

        return True

    async def wait_for_confirm(self, confirm_id: str) -> bool:
        """Wait for user to approve/deny a tool execution."""
        event = asyncio.Event()
        self._confirm_events[confirm_id] = event
        await event.wait()
        result = self._confirm_results.pop(confirm_id, False)
        self._confirm_events.pop(confirm_id, None)
        return result

    def resolve_confirm(self, confirm_id: str, approved: bool, trust_workspace: bool = False, trust_shell: bool = False):
        """Resolve a pending confirmation from WebSocket."""
        if approved:
            self.set_trust(trust_workspace=trust_workspace, trust_shell=trust_shell)
        if confirm_id in self._confirm_events:
            self._confirm_results[confirm_id] = approved
            self._confirm_events[confirm_id].set()

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

            # Check context limits and compact if needed
            ctx = check_context_limit(messages, self.config.llm.model, self.config.llm.context_window)
            yield AgentEvent("context", {
                "tokens": ctx["total_tokens"],
                "usage_pct": ctx["usage_pct"],
                "severity": ctx["severity"],
            })

            if ctx["needs_compaction"]:
                log.info("Context at %s%%, compacting messages", ctx["usage_pct"])
                messages = compact_messages(messages, keep_recent=20, model=self.config.llm.model)
                yield AgentEvent("compacted", {"message": "Context window compressed to make room"})

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

                    # Check if tool requires confirmation
                    if self.needs_confirmation(tc.name, tc.arguments):
                        confirm_id = f"{tc.id}_{tc.name}"
                        yield AgentEvent("confirm_request", {
                            "id": confirm_id,
                            "tool": tc.name,
                            "arguments": tc.arguments,
                            "in_workspace": self._is_in_workspace(tc.arguments.get("file_path", tc.arguments.get("path", ""))) if tc.name in ("write", "edit") else None,
                        })
                        approved = await self.wait_for_confirm(confirm_id)

                        if not approved:
                            await self.session.add_message(
                                "tool",
                                content=f"Tool '{tc.name}' was denied by user.",
                                tool_call_id=tc.id,
                            )
                            yield AgentEvent("tool_result", {
                                "id": tc.id,
                                "name": tc.name,
                                "output": f"Denied by user",
                            })
                            continue

                    result = await self.tools.execute(tc.name, tc.arguments)
                    # Truncate large tool results before saving
                    truncated = truncate_tool_result(result, max_tokens=4000)
                    await self.session.add_message("tool", content=truncated, tool_call_id=tc.id)
                    yield AgentEvent("tool_result", {"id": tc.id, "name": tc.name, "output": truncated})

                    # Send plan update when todo tool is used
                    if tc.name == "todo":
                        todo_tool = self.tools.get("todo")
                        if todo_tool and hasattr(todo_tool, '_tasks'):
                            yield AgentEvent("plan_update", {"tasks": todo_tool._tasks})

                continue

            if accumulated_text:
                await self.session.add_message("assistant", content=accumulated_text)

            break

        yield AgentEvent("done")
