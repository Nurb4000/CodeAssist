import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import openai

from .config import Config
from .cost_tracker import CostTracker, BudgetConfig
from .knowledge import KnowledgeBase
from .llm import LLMClient, TextDelta, ToolCall, Finish, LLMEvent
from .prompts import build_system_prompt, build_openai_messages
from .session import Session
from tools import ToolRegistry
from .tokens import compact_messages, check_context_limit, truncate_tool_result, llm_compact_messages, strip_media_from_messages
from .tool_output_store import get_tool_output_store
from .permissions import permission_manager, PermissionRuleset

log = logging.getLogger(__name__)

# Legacy: tools that require user confirmation (replaced by permission_manager)
CONFIRM_TOOLS = {"write", "edit", "shell", "git"}


@dataclass
class AgentEvent:
    type: str
    data: dict = field(default_factory=dict)


class Agent:
    def __init__(self, config: Config, session: Session, tools: ToolRegistry, system_prompt: str = None, agent_ruleset: PermissionRuleset = None):
        self.config = config
        self.session = session
        self.tools = tools
        self.llm = LLMClient(config.llm)
        self.system_prompt = system_prompt or build_system_prompt(config.workspace, config.llm.model)
        self.agent_ruleset = agent_ruleset  # Agent-specific permission rules
        self.cancel_event = asyncio.Event()
        self._confirm_events: dict[str, asyncio.Event] = {}
        self._confirm_results: dict[str, bool] = {}
        # Session trust flags
        self._trust_workspace_writes = False
        self._trust_shell = False
        # Cost tracking
        self.cost_tracker = CostTracker()
        # Incremental message cache — avoids DB fetch on every iteration
        self._messages: list[dict] | None = None
        self._messages_dirty: bool = True
        # Compaction state tracking
        self._compaction_summary: str = ""
        self._compaction_count: int = 0
        # Tool output store for managed file outputs
        self._tool_output_store = get_tool_output_store(
            self.config.workspace,
            max_lines=self.config.tool_output.max_lines,
            max_bytes=self.config.tool_output.max_bytes,
            retention_days=self.config.tool_output.retention_days,
        )

    def cancel(self):
        """Cancel the current agent run."""
        self.cancel_event.set()
        # Unblock any pending confirmations so the agent can exit
        for confirm_id, event in list(self._confirm_events.items()):
            self._confirm_results[confirm_id] = False
            event.set()

    def reset_trust(self):
        """Reset trust flags for new session."""
        self._trust_workspace_writes = False
        self._trust_shell = False

    def set_trust(self, trust_workspace: bool = False, trust_shell: bool = False):
        """Set trust flags from user confirmation."""
        if trust_workspace:
            self._trust_workspace_writes = True
            log.info("Workspace writes trusted. Flag=%s", self._trust_workspace_writes)
        if trust_shell:
            self._trust_shell = True
            log.info("Shell commands trusted. Flag=%s", self._trust_shell)

    def _is_in_workspace(self, file_path: str) -> bool:
        """Check if a file path is within the workspace."""
        try:
            path = Path(file_path).resolve()
            workspace = self.config.workspace.resolve()
            path.relative_to(workspace)
            return True
        except (ValueError, OSError, RuntimeError):
            return False

    def needs_confirmation(self, tool_name: str, arguments: dict) -> bool:
        """Check if a tool call requires user confirmation.

        Uses the new permission system with pattern-based rules and saved preferences.
        Falls back to legacy trust flags for backwards compatibility.
        """
        file_path = arguments.get("file_path", arguments.get("path", ""))

        # Check shell trust (legacy)
        if tool_name == "shell" and self._trust_shell:
            return False

        # Check workspace write trust (legacy) — only skip for in-workspace paths
        if tool_name in ("write", "edit") and self._trust_workspace_writes:
            if file_path and self._is_in_workspace(file_path):
                return False

        # Use permission manager for granular checks
        try:
            action = permission_manager.check_permission(tool_name, file_path, self.agent_ruleset)
            if action == "allow":
                return False
            if action == "deny":
                return True  # Will be handled as denied below
        except Exception as e:
            log.debug("Permission check failed, falling back to legacy: %s", e)

        # Legacy fallback: tools in CONFIRM_TOOLS need confirmation
        return tool_name in CONFIRM_TOOLS

    async def get_permission_action(self, tool_name: str, arguments: dict) -> str:
        """Get the permission action for a tool call. Returns 'allow', 'deny', or 'ask'."""
        file_path = arguments.get("file_path", arguments.get("path", ""))
        return await permission_manager.check_permission(tool_name, file_path, self.agent_ruleset)

    async def wait_for_confirm(self, confirm_id: str) -> bool:
        """Wait for user to approve/deny a tool execution."""
        event = asyncio.Event()
        self._confirm_events[confirm_id] = event
        await event.wait()
        result = self._confirm_results.pop(confirm_id, False)
        self._confirm_events.pop(confirm_id, None)
        return result

    def resolve_confirm(self, confirm_id: str, approved: bool, trust_workspace: bool = False, trust_shell: bool = False, remember: bool = False):
        """Resolve a pending confirmation from WebSocket."""
        log.info("Confirmation resolved: id=%s, approved=%s, trust_workspace=%s, trust_shell=%s, remember=%s",
                 confirm_id, approved, trust_workspace, trust_shell, remember)
        if approved:
            self.set_trust(trust_workspace=trust_workspace, trust_shell=trust_shell)
        if confirm_id in self._confirm_events:
            self._confirm_results[confirm_id] = approved
            self._confirm_events[confirm_id].set()

    async def save_permission(self, tool_name: str, file_path: str, action: str):
        """Save a permission choice for future reference."""
        await permission_manager.save_permission_choice(tool_name, file_path, action)

    def resolve_question(self, question_id: str, answer: str):
        """Resolve a pending question from WebSocket."""
        log.info("Question resolved: id=%s", question_id)
        question_tool = self.tools.get("question")
        if question_tool and hasattr(question_tool, "set_answer"):
            question_tool.set_answer(question_id, answer)

    async def _create_turn_snapshot(self, phase: str) -> dict | None:
        """Create a workspace snapshot at turn boundaries. Returns snapshot info or None."""
        try:
            from codeassist.snapshot import get_snapshot_manager
            sm = get_snapshot_manager(self.config.workspace, enabled=True)
            if sm and sm.enabled:
                messages = await self.session.get_messages()
                user_msgs = [m for m in messages if m["role"] == "user"]
                turn_number = len(user_msgs)

                record = await sm.create_snapshot(self.session.id, turn_number)
                if record:
                    return {
                        "id": record.id,
                        "phase": phase,
                        "turn": turn_number,
                        "files_changed": len(record.files_changed),
                    }
        except Exception as e:
            log.debug("Snapshot creation failed: %s", e)
        return None

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        self.cancel_event.clear()
        # Reset compaction state for new user turn
        self._compaction_summary = ""
        self._compaction_count = 0

        # Create snapshot before turn starts
        snap_before = await self._create_turn_snapshot("before")
        if snap_before:
            yield AgentEvent("snapshot", snap_before)

        await self.session.add_message("user", user_message)

        try:
            async for event in self._loop(user_message):
                if self.cancel_event.is_set():
                    yield AgentEvent("cancelled")
                    yield AgentEvent("done")
                    return
                yield event
            # _loop ended normally — check if it was due to cancel
            if self.cancel_event.is_set():
                yield AgentEvent("cancelled")
                yield AgentEvent("done")
                return
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

        # Create snapshot after turn completes
        snap_after = await self._create_turn_snapshot("after")
        if snap_after:
            yield AgentEvent("snapshot", snap_after)

        # Periodic cleanup of old tool output files
        try:
            removed = await self._tool_output_store.cleanup()
            if removed:
                log.info("Cleaned up %d old tool output files", removed)
        except Exception as e:
            log.debug("Tool output cleanup failed: %s", e)

    async def _loop(self, user_message: str) -> AsyncIterator[AgentEvent]:
        recent_texts: list[str] = []
        max_repeats = 3
        hit_max_iterations = False

        # Cache tool schema tokens once (they don't change within a loop)
        tool_schemas = self.tools.schemas()
        openai_tools = self.llm.format_tools(tool_schemas) if tool_schemas else None

        compaction_cfg = self.config.compaction
        compaction_escalation = 0

        # Compaction cache: avoid re-compacting when no new messages arrived
        _cached_messages = None
        _cached_history_len = 0

        for iteration in range(self.config.agent.max_iterations):
            if self.cancel_event.is_set():
                return

            # Check budget before each iteration
            budget_ok, budget_msg = self.cost_tracker.check_budget()
            if not budget_ok:
                log.warning("Budget exceeded: %s", budget_msg)
                yield AgentEvent("error", {"message": f"Budget exceeded: {budget_msg}"})
                yield AgentEvent("done")
                return

            # Fetch messages once, then track incrementally
            if self._messages is None or self._messages_dirty:
                self._messages = await self.session.get_messages()
                self._messages_dirty = False
            history = self._messages

            # Only rebuild and re-compact when new messages have been added
            if len(history) != _cached_history_len:
                messages = build_openai_messages(self.system_prompt, history)

                # Check context limits and compact if needed
                ctx = check_context_limit(
                    messages, self.config.llm.model, self.config.llm.context_window,
                    tool_schemas=tool_schemas,
                )
                yield AgentEvent("context", {
                    "tokens": ctx["total_tokens"],
                    "usage_pct": ctx["usage_pct"],
                    "severity": ctx["severity"],
                })

                if ctx["needs_compaction"] and compaction_cfg.enabled:
                    log.info("Context at %s%%, compacting messages (mode=%s)", ctx["usage_pct"], compaction_cfg.mode)

                    if compaction_cfg.mode == "llm":
                        # LLM-based compaction (default)
                        comp_model = compaction_cfg.model or self.config.llm.model
                        messages, self._compaction_summary = await llm_compact_messages(
                            messages,
                            tail_turns=compaction_cfg.tail_turns,
                            preserve_recent_tokens=compaction_cfg.preserve_recent_tokens,
                            previous_summary=self._compaction_summary,
                            compaction_model=comp_model,
                            main_llm_config=self.config.llm,
                        )
                        self._compaction_count += 1

                        # Re-check context after LLM compaction
                        recheck = check_context_limit(
                            messages, self.config.llm.model, self.config.llm.context_window,
                            tool_schemas=tool_schemas,
                        )

                        # If still over limit, try text compaction as fallback
                        if recheck["needs_compaction"]:
                            log.info("LLM compaction insufficient (%s%%), escalating to text mode", recheck["usage_pct"])
                            messages = compact_messages(
                                messages,
                                keep_recent=compaction_cfg.keep_recent,
                                model=self.config.llm.model,
                                escalation_level=compaction_escalation,
                            )
                            compaction_escalation = 1

                            # Final overflow: strip media and retry
                            final_check = check_context_limit(
                                messages, self.config.llm.model, self.config.llm.context_window,
                                tool_schemas=tool_schemas,
                            )
                            if final_check["needs_compaction"]:
                                log.warning("Context still full after all compaction. Stripping media.")
                                messages = strip_media_from_messages(messages)

                    else:
                        # Text-based compaction (existing behavior)
                        messages = compact_messages(
                            messages,
                            keep_recent=compaction_cfg.keep_recent,
                            model=self.config.llm.model,
                            escalation_level=compaction_escalation,
                        )
                        recheck = check_context_limit(
                            messages, self.config.llm.model, self.config.llm.context_window,
                            tool_schemas=tool_schemas,
                        )
                        if recheck["needs_compaction"] and compaction_escalation == 0:
                            compaction_escalation = 1
                            messages = compact_messages(
                                messages,
                                keep_recent=compaction_cfg.keep_recent,
                                model=self.config.llm.model,
                                escalation_level=compaction_escalation,
                            )
                            log.info("Escalated compaction to level 1 (dropping old tool messages)")
                        elif not recheck["needs_compaction"]:
                            compaction_escalation = 0

                    yield AgentEvent("compacted", {
                        "message": "Context window compressed to make room",
                        "mode": compaction_cfg.mode,
                        "count": self._compaction_count,
                    })

                    # Auto-continue: if the tail ends with tool results (agent was mid-work),
                    # inject a continuation prompt so the LLM knows to keep going.
                    if messages and messages[-1].get("role") == "tool":
                        messages.append({
                            "role": "user",
                            "content": "[Context was compacted to save space. Continue with your next steps if the task is not yet complete.]",
                        })

                _cached_messages = messages
                _cached_history_len = len(history)
            else:
                # Reuse cached compacted messages — no new data to process
                messages = _cached_messages

            accumulated_text = ""
            tool_calls: list[ToolCall] = []
            stream_timed_out = False

            stream_start = time.monotonic()
            stream_timeout = 120.0  # seconds

            # Save placeholder immediately so partial responses survive crashes
            stream_msg_id = await self.session.add_message("assistant", content="")

            async for event in self.llm.stream(messages, openai_tools):
                if self.cancel_event.is_set():
                    return
                if time.monotonic() - stream_start > stream_timeout:
                    log.warning("LLM stream timed out after %.0fs", stream_timeout)
                    yield AgentEvent("error", {"message": f"LLM stream timed out after {stream_timeout:.0f}s"})
                    stream_timed_out = True
                    break
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
                    # Record usage for cost tracking
                    self.cost_tracker.record_usage(
                        model=self.config.llm.model,
                        prompt_tokens=event.usage.prompt_tokens,
                        completion_tokens=event.usage.completion_tokens,
                    )
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
                await self.session.update_message(
                    stream_msg_id,
                    content=accumulated_text or None,
                    tool_calls=tc_dicts,
                )
                self._messages_dirty = True

                # Phase 1: Handle confirmations and questions sequentially (interactive)
                confirmed_tool_calls = []
                for tc in tool_calls:
                    if self.cancel_event.is_set():
                        return
                    if tc.name == "question":
                        question_id = f"{tc.id}_question"
                        structured_questions = tc.arguments.get("questions")
                        legacy_question = tc.arguments.get("question", "")
                        yield AgentEvent("question_request", {
                            "id": question_id,
                            "question": legacy_question,
                            "questions": structured_questions,
                            "options": tc.arguments.get("options"),
                            "required": tc.arguments.get("required", False),
                        })
                        event = asyncio.Event()
                        self._confirm_events[question_id] = event
                        await event.wait()
                        answer = self._confirm_results.pop(question_id, "")
                        self._confirm_events.pop(question_id, None)
                        if not answer:
                            await self.session.add_message(
                                "tool",
                                content="Question was dismissed by user.",
                                tool_call_id=tc.id,
                            )
                        else:
                            await self.session.add_message("tool", content=str(answer), tool_call_id=tc.id)
                        self._messages_dirty = True
                        yield AgentEvent("tool_result", {"id": tc.id, "name": "question", "output": str(answer)})
                        continue

                    # Check permission action (allow/deny/ask)
                    file_path = tc.arguments.get("file_path", tc.arguments.get("path", ""))
                    perm_action = await self.get_permission_action(tc.name, tc.arguments)

                    if perm_action == "deny":
                        # Tool is explicitly denied — skip without asking
                        await self.session.add_message(
                            "tool",
                            content=f"Tool '{tc.name}' is not permitted by your permission rules.",
                            tool_call_id=tc.id,
                        )
                        self._messages_dirty = True
                        yield AgentEvent("tool_result", {
                            "id": tc.id,
                            "name": tc.name,
                            "output": "Denied by permission rules",
                        })
                        continue

                    if perm_action == "ask" or self.needs_confirmation(tc.name, tc.arguments):
                        confirm_id = f"{tc.id}_{tc.name}"
                        # Check if there's a saved permission hint
                        saved_hint = permission_manager.saved.check_saved(tc.name, file_path)
                        yield AgentEvent("confirm_request", {
                            "id": confirm_id,
                            "tool": tc.name,
                            "file_path": file_path,
                            "arguments": tc.arguments,
                            "in_workspace": self._is_in_workspace(file_path) if tc.name in ("write", "edit") else None,
                            "permission_action": perm_action,
                            "saved_permission": saved_hint,
                        })
                        approved = await self.wait_for_confirm(confirm_id)
                        if not approved:
                            await self.session.add_message(
                                "tool",
                                content=f"Tool '{tc.name}' was denied by user.",
                                tool_call_id=tc.id,
                            )
                            self._messages_dirty = True
                            yield AgentEvent("tool_result", {
                                "id": tc.id,
                                "name": tc.name,
                                "output": "Denied by user",
                            })
                            continue
                    confirmed_tool_calls.append(tc)

                # Phase 2: Execute confirmed tools in parallel
                async def _exec_tool(tc):
                    start = time.monotonic()
                    result = await self.tools.execute(tc.name, tc.arguments)
                    duration_ms = int((time.monotonic() - start) * 1000)

                    # First truncate to token limit
                    truncated = truncate_tool_result(result.output, max_tokens=self.config.tools.tool_output_max_tokens)

                    # If output is still large, save to managed file and return preview
                    filepath = await self._tool_output_store.save_if_needed(
                        result.output, tc.name, self.session.id
                    )
                    if filepath:
                        truncated = self._tool_output_store.build_preview(
                            result.output, filepath, tc.name
                        )

                    return tc, result, truncated, duration_ms

                if confirmed_tool_calls:
                    results = await asyncio.gather(*[_exec_tool(tc) for tc in confirmed_tool_calls])
                    for tc, result, truncated, duration_ms in results:
                        await self.session.add_message("tool", content=truncated, tool_call_id=tc.id)
                        self._messages_dirty = True
                        yield AgentEvent("tool_result", {"id": tc.id, "name": tc.name, "output": truncated})
                        try:
                            await KnowledgeBase.log_tool_execution(
                                session_id=self.session.id,
                                tool_name=tc.name,
                                arguments=tc.arguments,
                                result_summary=truncated[:1000] if truncated else None,
                                result_full=truncated,
                                duration_ms=duration_ms,
                                success=not result.error,
                                error_message=truncated[:500] if result.error else None,
                            )
                        except Exception:
                            log.debug("Failed to log tool execution")
                        if tc.name == "todo":
                            todo_tool = self.tools.get("todo")
                            if todo_tool and hasattr(todo_tool, "get_tasks"):
                                yield AgentEvent("plan_update", {"tasks": todo_tool.get_tasks()})

                continue

            if accumulated_text:
                await self.session.update_message(stream_msg_id, content=accumulated_text)
                self._messages_dirty = True

                # Repetition detection: break if the LLM keeps producing the same output
                normalized = accumulated_text.strip().lower()
                recent_texts.append(normalized)
                if len(recent_texts) > max_repeats:
                    recent_texts.pop(0)
                if len(recent_texts) >= max_repeats and len(set(recent_texts)) == 1:
                    log.warning("LLM repetition detected (%d identical responses), breaking loop", max_repeats)
                    yield AgentEvent("error", {"message": f"Detected repetitive output — stopped after {max_repeats} identical responses."})
                    break

            break
        else:
            hit_max_iterations = True

        if not self.cancel_event.is_set() and hit_max_iterations:
            yield AgentEvent("error", {
                "message": f"Reached maximum iterations ({self.config.agent.max_iterations}). "
                           "The task may not be fully complete. You can continue in a new message."
            })

        yield AgentEvent("done")
