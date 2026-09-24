import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import openai

from .capabilities import effective_context_window
from .config import Config
from .cost_tracker import CostTracker
from .knowledge import KnowledgeBase
from .llm import Finish, LLMClient, ReasoningDelta, TextDelta, ToolCall
from .permissions import PermissionRuleset, permission_manager
from .prompts import build_openai_messages, build_system_prompt
from .session import Session
from .tokens import (
    check_context_limit,
    compact_messages,
    llm_compact_messages,
    strip_media_from_messages,
    truncate_tool_result,
)
from .tool_output_store import get_tool_output_store
from .tools import ToolRegistry

log = logging.getLogger(__name__)

# Legacy: tools that require user confirmation (replaced by permission_manager)
CONFIRM_TOOLS = {"write", "edit", "shell", "git"}

# Session-scoped trust flags, keyed by session id. "Trust for this session"
# survives WS reconnects (each connection builds a fresh Agent) while staying
# isolated per session and ephemeral across server restarts.
SESSION_TRUST: dict[str, dict] = {}

# Per-session sets of tool names trusted "for the rest of this session".
SESSION_TOOL_TRUST: dict[str, set[str]] = {}

# Tools whose successful execution means the run made concrete progress (a
# deliverable was produced or the workspace changed). Pure research tools
# (read/grep/glob/webfetch/...) do not count, so a model that only researches
# and then stops would otherwise be marked "complete" without doing any work.
PRODUCTIVE_TOOLS = frozenset({
    "write", "edit", "apply_patch", "shell", "git", "fossil",
    "documentation", "create_tool", "create_skill",
    "package_manager", "docker", "database", "test_runner",
})

# How many times the loop will nudge a model that keeps researching without
# producing any concrete change before giving up and flagging the task
# incomplete instead of falsely marking it done.
MAX_RESEARCH_NUDGES = 5

# First nudge: gentle, and it offers an escape hatch so a legitimate
# research-only question (e.g. "what is in this file?") can still be answered.
RESEARCH_ONLY_CONTINUATION = (
    "[Progress check: you have used tools but made no changes to the workspace yet. "
    "If the task is already fully answered, reply with your final answer now. "
    "Otherwise continue with the necessary actions (write/edit/run tests/document) "
    "to actually complete it — do not stop after research alone.]"
)

# Follow-up nudges for a model that keeps doing more research instead of
# acting: firmer, and no escape hatch, so it cannot answer its way out.
RESEARCH_ONLY_CONTINUATION_FIRM = (
    "[You are still only gathering information and have not made any changes to "
    "complete the task. Stay focused: proceed with the required actions now "
    "(write/edit/run tests/document) to finish the actual work. Do not conclude "
    "with a summary — make the changes.]"
)


@dataclass
class AgentEvent:
    type: str
    data: dict = field(default_factory=dict)


class Agent:
    def __init__(self, config: Config, session: Session, tools: ToolRegistry, system_prompt: str | None = None, agent_ruleset: PermissionRuleset | None = None):
        self.config = config
        self.session = session
        self.tools = tools
        self.llm = LLMClient(config.llm)
        self.system_prompt = system_prompt or build_system_prompt(config.workspace, config.llm.model)
        self.agent_ruleset = agent_ruleset  # Agent-specific permission rules
        self.cancel_event = asyncio.Event()
        self._confirm_events: dict[str, asyncio.Event] = {}
        self._confirm_results: dict[str, bool] = {}
        self._confirm_tools: dict[str, str] = {}
        self._confirm_requests: dict[str, dict] = {}
        # Session trust flags (seeded from the session-scoped store so a
        # reconnected WS for the same session keeps its trust).
        stored_trust = SESSION_TRUST.get(self.session.id, {})
        self._trust_workspace_writes = stored_trust.get("workspace", False)
        self._trust_shell = stored_trust.get("shell", False)
        self._trust_all = stored_trust.get("all", False)
        # Cost tracking
        self.cost_tracker = CostTracker()
        # Incremental message cache — avoids DB fetch on every iteration
        self._messages: list[dict] | None = None
        self._messages_dirty: bool = True
        # Compaction state tracking
        self._compaction_summary: str = ""
        self._compaction_count: int = 0
        # Research-only termination guard (reset per run in run())
        self._run_progress_made: bool = False
        self._run_used_tools: bool = False
        self._since_nudge_tools: bool = False
        self._research_only_nudges: int = 0
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
        self._trust_all = False
        SESSION_TRUST.pop(self.session.id, None)
        SESSION_TOOL_TRUST.pop(self.session.id, None)

    def set_trust(self, trust_workspace: bool = False, trust_shell: bool = False, trust_all: bool = False):
        """Set trust flags from user confirmation (persisted per session id)."""
        if trust_workspace:
            self._trust_workspace_writes = True
            log.info("Workspace writes trusted. Flag=%s", self._trust_workspace_writes)
        if trust_shell:
            self._trust_shell = True
            log.info("Shell commands trusted. Flag=%s", self._trust_shell)
        if trust_all:
            self._trust_all = True
            log.info("All tools trusted for this session. Flag=%s", self._trust_all)
        entry = {
            "workspace": self._trust_workspace_writes,
            "shell": self._trust_shell,
        }
        if self._trust_all:
            entry["all"] = True
        SESSION_TRUST[self.session.id] = entry

    def _trust_all_active(self) -> bool:
        """Whether trust-all is enabled for this agent.

        Combines the per-session "trust all tools" flag (set from the confirm
        dialog) with the admin-level config option (Settings > Permissions),
        which is either this-session (ephemeral) or always (persisted).
        """
        if self._trust_all:
            return True
        perms = getattr(self.config, "permissions", None)
        return getattr(perms, "trust_all", "ask") in ("session", "always")

    def _is_in_workspace(self, file_path: str) -> bool:
        """Check if a file path is within the workspace."""
        try:
            path = Path(file_path).resolve()
            workspace = self.config.workspace.resolve()
            path.relative_to(workspace)
            return True
        except (ValueError, OSError, RuntimeError):
            return False

    async def needs_confirmation(self, tool_name: str, arguments: dict) -> bool:
        """Check if a tool call requires user confirmation.

        Uses the new permission system with pattern-based rules and saved preferences.
        Falls back to legacy trust flags for backwards compatibility.
        """
        file_path = arguments.get("file_path", arguments.get("path", ""))

        # Trust-all (session or permanent): skip confirmation for everything.
        if self._trust_all_active():
            return False

        # Check shell trust (legacy)
        if tool_name == "shell" and self._trust_shell:
            return False

        # Check workspace write trust (legacy) — only skip for in-workspace paths
        if tool_name in ("write", "edit") and self._trust_workspace_writes:
            if file_path and self._is_in_workspace(file_path):
                return False

        # Per-tool session trust ("trust this tool for the rest of this session")
        if tool_name in SESSION_TOOL_TRUST.get(self.session.id, set()):
            return False

        # Use permission manager for granular checks
        try:
            action = await permission_manager.check_permission(tool_name, file_path, self.agent_ruleset)
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
        if self._trust_all_active():
            return "allow"
        return await permission_manager.check_permission(tool_name, file_path, self.agent_ruleset)

    async def wait_for_confirm(self, confirm_id: str) -> bool:
        """Wait for user to approve/deny a tool execution."""
        event = asyncio.Event()
        self._confirm_events[confirm_id] = event
        await event.wait()
        result = self._confirm_results.pop(confirm_id, False)
        self._confirm_events.pop(confirm_id, None)
        return result

    def get_confirm_context(self, confirm_id: str) -> dict | None:
        """Return (and clear) the stored tool/file_path context bound to a confirm id.

        Used by the WS confirm_response handler to persist a remembered permission
        from server-side data rather than client-echoed values.
        """
        return self._confirm_requests.pop(confirm_id, None)

    def resolve_confirm(self, confirm_id: str, approved: bool, trust_workspace: bool = False, trust_shell: bool = False, trust_tool: bool = False, remember: bool = False, trust_all: bool = False):
        """Resolve a pending confirmation from WebSocket."""
        log.info("Confirmation resolved: id=%s, approved=%s, trust_workspace=%s, trust_shell=%s, trust_tool=%s, remember=%s, trust_all=%s",
                 confirm_id, approved, trust_workspace, trust_shell, trust_tool, remember, trust_all)
        tool_name = self._confirm_tools.pop(confirm_id, None)
        if approved:
            self.set_trust(trust_workspace=trust_workspace, trust_shell=trust_shell, trust_all=trust_all)
            if trust_tool and tool_name:
                SESSION_TOOL_TRUST.setdefault(self.session.id, set()).add(tool_name)
                log.info("Tool '%s' trusted for session %s", tool_name, self.session.id)
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

    async def run(self, user_message: str, attachments: list[dict] | None = None) -> AsyncIterator[AgentEvent]:
        self.cancel_event.clear()
        # Reset compaction state for new user turn
        self._compaction_summary = ""
        self._compaction_count = 0
        # Reset research-only guard state for new user turn
        self._run_progress_made = False
        self._run_used_tools = False
        self._since_nudge_tools = False
        self._research_only_nudges = 0

        await self.session.add_message("user", user_message, attachments=attachments)

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
                    messages, self.config.llm.model, await effective_context_window(self.config),
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
            accumulated_reasoning = ""
            tool_calls: list[ToolCall] = []

            stream_start = time.monotonic()
            stream_timeout = float(getattr(self.config.llm, "timeout", 120))  # seconds

            # Save placeholder immediately so partial responses survive crashes
            stream_msg_id = await self.session.add_message("assistant", content="")

            async for event in self.llm.stream(messages, openai_tools):
                if self.cancel_event.is_set():
                    return
                if time.monotonic() - stream_start > stream_timeout:
                    log.warning("LLM stream timed out after %.0fs", stream_timeout)
                    yield AgentEvent("error", {"message": f"LLM stream timed out after {stream_timeout:.0f}s"})
                    break
                if isinstance(event, TextDelta):
                    accumulated_text += event.content
                    yield AgentEvent("text_delta", {"content": event.content})

                elif isinstance(event, ReasoningDelta):
                    accumulated_reasoning += event.content
                    yield AgentEvent("reasoning", {"content": event.content})

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
                self._run_used_tools = True
                self._since_nudge_tools = True
                tc_dicts = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                    for tc in tool_calls
                ]
                await self.session.update_message(
                    stream_msg_id,
                    content=accumulated_text or None,
                    tool_calls=tc_dicts,
                    reasoning_content=accumulated_reasoning or None,
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

                    if perm_action == "ask" or await self.needs_confirmation(tc.name, tc.arguments):
                        confirm_id = f"{tc.id}_{tc.name}"
                        # Check if there's a saved permission hint
                        saved_hint = permission_manager.saved.check_saved(tc.name, file_path)
                        # Bind the request context server-side so a later "remember"
                        # decision is never derived from client-echoed values.
                        self._confirm_requests[confirm_id] = {
                            "tool": tc.name,
                            "file_path": file_path,
                            "arguments": tc.arguments,
                        }
                        yield AgentEvent("confirm_request", {
                            "id": confirm_id,
                            "tool": tc.name,
                            "file_path": file_path,
                            "arguments": tc.arguments,
                            "in_workspace": self._is_in_workspace(file_path) if tc.name in ("write", "edit") else None,
                            "permission_action": perm_action,
                            "saved_permission": saved_hint,
                        })
                        self._confirm_tools[confirm_id] = tc.name
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
                        if tc.name in PRODUCTIVE_TOOLS and not result.error:
                            self._run_progress_made = True
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

            if accumulated_text or accumulated_reasoning:
                await self.session.update_message(
                    stream_msg_id,
                    content=accumulated_text or None,
                    reasoning_content=accumulated_reasoning or None,
                )
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

            # Research-only termination guard. If the model stopped using tools
            # but has produced no concrete change yet, decide whether to keep
            # working or finish:
            #  - Real progress was made this run -> the model is done, finish.
            #  - First research-stop -> gentle nudge with an escape hatch so a
            #    legitimate research-only question can still be answered.
            #  - Already nudged and the model answered without doing more work
            #    -> respect that; it cannot be forced to act.
            #  - Already nudged and the model kept researching instead -> firmer
            #    nudge. Once the nudge budget is spent, flag the task incomplete
            #    rather than falsely marking it complete.
            if not self._run_progress_made and self._run_used_tools:
                if not self._research_only_nudges:
                    self._research_only_nudges = 1
                    self._since_nudge_tools = False
                    log.warning("LLM stopped after research with no changes; nudging to continue.")
                    messages.append({"role": "user", "content": RESEARCH_ONLY_CONTINUATION})
                    _cached_messages = messages
                    _cached_history_len = len(history)
                    self._messages_dirty = False
                    continue
                if not self._since_nudge_tools:
                    log.debug("LLM answered after a nudge without further work; treating as complete.")
                elif self._research_only_nudges < MAX_RESEARCH_NUDGES:
                    self._research_only_nudges += 1
                    self._since_nudge_tools = False
                    log.warning(
                        "LLM still scattered after research (nudge %d/%d); pushing further.",
                        self._research_only_nudges, MAX_RESEARCH_NUDGES,
                    )
                    messages.append({"role": "user", "content": RESEARCH_ONLY_CONTINUATION_FIRM})
                    _cached_messages = messages
                    _cached_history_len = len(history)
                    self._messages_dirty = False
                    continue
                else:
                    log.warning(
                        "LLM stuck in research loop after %d nudges; marking incomplete.",
                        self._research_only_nudges,
                    )
                    yield AgentEvent("incomplete", {
                        "message": "The task may not be complete: the agent kept researching without making changes and did not finish. Use Continue to let it keep going.",
                    })
                    return

            break
        else:
            hit_max_iterations = True

        if not self.cancel_event.is_set() and hit_max_iterations:
            yield AgentEvent("error", {
                "message": f"Reached maximum iterations ({self.config.agent.max_iterations}). "
                           "The task may not be fully complete. You can continue in a new message."
            })

        yield AgentEvent("done")
