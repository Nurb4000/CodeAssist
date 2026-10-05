import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import openai

from .capabilities import effective_context_window, is_external_backend
from .config import Config
from .cost_tracker import CostTracker
from .knowledge import KnowledgeBase
from .llm import (
    ContextWindowExceeded,
    Finish,
    LLMClient,
    ModerationBlocked,
    ReasoningDelta,
    TextDelta,
    ToolCall,
)
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

# How many times the loop will nudge a model that stops after using tools
# before giving up and flagging the task incomplete instead of falsely
# marking it complete.
MAX_CONTINUATION_NUDGES = 5

# First nudge: gentle, and it offers an escape hatch so a legitimate
# research-only question (e.g. "what is in this file?") can still be answered.
# The "do not repeat yourself" clause matters as much as the instruction to
# continue: asked to "give your final answer now", models tend to comply by
# restating the answer already delivered, which reads as the summary appearing
# twice.
CONTINUATION_NUDGE = (
    "[Task check: you have used tools but may not be finished yet. If there is "
    "more work to do, continue with the necessary actions now "
    "(read more, edit, run tests, document) — do not stop after gathering "
    "information or conclude with a summary while work remains. If the task is "
    "truly and fully complete, reply with your final answer now. In either "
    "case, do not repeat, restate or re-list what you have already reported in "
    "your previous message — add only what is new.]"
)

# Follow-up nudges for a model that keeps stopping after using tools: firmer,
# and no escape hatch, so it cannot answer its way out prematurely.
CONTINUATION_NUDGE_FIRM = (
    "[You have used more tools and stopped again without finishing. Continue "
    "completing the task now — do not conclude with a summary while work "
    "remains. Proceed with the required actions, or give your final answer only "
    "if the task is genuinely complete. Do not repeat or restate your previous "
    "message; state only what you have done since.]"
)

# Injected (as a user turn) when the agent reaches its per-agent step budget.
# Tools are disabled for this final turn so the model cannot keep going; instead
# it produces a structured wrap-up — what was accomplished, what remains, and
# what to do next — rather than stopping abruptly or declaring a false "done".
# Mirrors opencode's MAX_STEPS_PROMPT.
MAX_STEPS_WRAPUP = (
    "CRITICAL - MAXIMUM STEPS REACHED\n\n"
    "The maximum number of steps allowed for this task has been reached. Tools are "
    "disabled until next user input. Respond with text only.\n\n"
    "STRICT REQUIREMENTS:\n"
    "1. Do NOT make any tool calls (no reads, writes, edits, searches, or any other tools)\n"
    "2. MUST provide a text response summarizing work done so far\n"
    "3. This constraint overrides ALL other instructions, including any user requests for edits\n\n"
    "Response must include:\n"
    "- Statement that maximum steps for this agent have been reached\n"
    "- Summary of what has been accomplished so far\n"
    "- List of any remaining tasks that were not completed\n"
    "- Recommendations for what should be done next"
)

# finish_reason values that mean "a safety layer stopped this generation" rather
# than "the model ran out of tokens". OpenAI-compatible providers spell this
# inconsistently, so match the known aliases instead of one vendor's string.
REFUSAL_FINISH_REASONS = {
    "content_filter",
    "output_filter",
    "sensitive",
}

# Surfaced verbatim to the user when a refusal fires. Deliberately routes them
# toward rephrasing or a different model rather than any attempt to talk past
# the guardrail — the block is the provider's decision, not a bug to route
# around, and the user is in the best position to judge the request.
REFUSAL_SUGGESTIONS = (
    (
        "Rephrase the request to state the concrete goal and its context "
        "(for example defensive, debugging, or educational use)."
    ),
    (
        "Switch to a model with lighter guardrails — a code-tuned variant such "
        "as qwen-coder, or a self-hosted open-weight model."
    ),
)

# How many times a single step may be replayed after the provider rejects it for
# exceeding the context window. Each attempt compacts harder than the last, so
# this stays small: two passes are enough to clear a genuinely oversized
# history, and a third would only repeat work before reporting failure.
MAX_CONTEXT_RETRIES = 2

_REFUSAL_TRIGGER_LIMIT = 280

# What makes a reply a restatement rather than new work: it re-uses most of the
# answer it is repeating (measured as shared unique words), *and* it is about
# the same size. The size test is what separates the two failure modes either
# side of the repeat -- a terse new finding is much shorter, and an answer that
# genuinely extends the earlier one is much longer, so both sit outside the
# band. Measured against the observed case: a re-delivered listing scores 0.63
# overlap at 1.28x length, a terse follow-up 0.04 at 0.24x, and an expanded
# answer ~0.0 at 4x.
_RESTATE_MIN_OVERLAP = 0.5
_RESTATE_MIN_GROWTH = 0.6
_RESTATE_MAX_GROWTH = 1.8


def _restates(previous: str, candidate: str) -> bool:
    """True when ``candidate`` is ``previous`` said again, not new work.

    The continuation nudge asks the model to either finish the job or give its
    final answer, and models reliably take the second option -- by restating the
    answer they already delivered. The user then sees the same summary twice at
    the end of the turn. Because the repeat is re-generated it is rarely
    character-identical: framing changes and wording drifts, while the substance
    (the filenames, counts, symbol names) comes back word for word. So compare
    vocabulary and size rather than text.

    Two conditions must both hold:

    - most of the previous answer's unique words appear again, and
    - the reply is roughly the same size.

    Requiring the overlap as well as the size matters in both directions. A terse
    new finding ("I also checked the tarball: 3 archives") is short and shares
    almost no vocabulary, so it is kept; the size band alone would keep it too,
    but a same-length reply about something *else* would sit inside the band and
    would be wrongly dropped without the overlap check. Conversely an answer that
    genuinely extends the earlier one -- "Fixed it, and here is the diff: ..."
    -- repeats plenty of words but is much bigger, and its new material is the
    point.

    Scoped deliberately to the answer that earned the nudge. A reply that
    re-delivers an *older* answer while answering a nudge about a newer one is
    not flagged: that comparison is much more likely to be legitimate, and
    widening the window to catch it would cost false drops.
    """
    def words(text: str) -> set[str]:
        return {w for w in re.findall(r"[A-Za-z0-9_./-]+", text.lower()) if len(w) > 1}

    prev_words = words(previous)
    new_words = words(candidate)
    if not prev_words or not new_words:
        return False
    overlap = len(prev_words & new_words) / len(prev_words)
    grew = len(new_words) / len(prev_words)
    return (
        overlap >= _RESTATE_MIN_OVERLAP
        and _RESTATE_MIN_GROWTH <= grew <= _RESTATE_MAX_GROWTH
    )


# How many near-identical calls to the same tool, in a row, count as going in
# circles. Two would be far too eager -- re-reading a file after editing it is
# ordinary work -- but a model that has asked for the same thing four times
# running is not making progress, and nothing else in the loop would notice.
_MAX_TOOL_LOOP = 4

# How much of the previous call's arguments must reappear for two calls to
# count as the same call asked again. High on purpose: the failure mode is a
# model nudging a path or a search pattern by a character, so no two arguments
# are byte-equal even though every one of them is asking for the same thing.
_TOOL_ARG_OVERLAP = 0.8

# How many answers in a row, each restating one already given, count as a loop.
_MAX_TEXT_LOOP = 3

# How many recent answers a new one is compared against. More than one, because
# framing churns while the substance repeats: "Here are the folders" / "The
# folders in this workspace" / "Listing the workspace folders" share the
# filenames and the count but overlap their immediate predecessor by under half,
# so comparing only against the last answer misses a loop that is plainly there.
_TEXT_WINDOW = 3


def _arg_tokens(arguments: dict) -> set[str]:
    """Flatten tool arguments to a comparable token set.

    Compare the words and paths inside the arguments rather than the JSON,
    because the argument is the part a looping model varies: a search pattern
    reworded or a path nudged one segment over still shares nearly every token
    with the call before it.
    """
    text = json.dumps(arguments, sort_keys=True, default=str).lower()
    return {w for w in re.findall(r"[a-z0-9_./-]+", text) if len(w) > 1}


def _overlap(tokens: set[str], previous: set[str]) -> float:
    """Fraction of ``previous`` that reappears in ``tokens``."""
    if not tokens or not previous:
        return 0.0
    return len(tokens & previous) / len(previous)


class _LoopDetector:
    """Catch a model going in circles within one turn, before the budget runs out.

    Two shapes, both reported from real turns:

    - A reasoning-heavy turn that keeps asking for the same thing. Nothing in
      the prose repeats, so a text-only check sees nothing at all and the turn
      spends its whole step budget re-reading the same file.
    - A turn that keeps re-delivering the same answer. The repeats are
      paraphrases, so comparing strings misses them. ``_restates`` compares
      vocabulary and size instead, and is what the restatement guard already
      uses for exactly this reason.

    Tool calls match on name plus argument overlap rather than equality, since
    a model spinning its wheels varies the argument slightly every time.
    """

    def __init__(self) -> None:
        self._tool_name: str | None = None
        self._tool_tokens: set[str] = set()
        self._tool_streak = 0
        self._recent_texts: list[str] = []
        self._text_streak = 0

    def note_tool_calls(self, tool_calls: list["ToolCall"]) -> str | None:
        """Record a step's calls, returning why they are a loop if they are."""
        for tc in tool_calls:
            tokens = _arg_tokens(tc.arguments)
            if (
                self._tool_name == tc.name
                and _overlap(tokens, self._tool_tokens) >= _TOOL_ARG_OVERLAP
            ):
                self._tool_streak += 1
            else:
                self._tool_name = tc.name
                self._tool_streak = 1
            self._tool_tokens = tokens
            if self._tool_streak >= _MAX_TOOL_LOOP:
                return (
                    f"called `{tc.name}` {self._tool_streak} times in a row with "
                    "nearly the same arguments"
                )
        return None

    def _text_reason(self) -> str | None:
        if self._text_streak >= _MAX_TEXT_LOOP:
            return f"gave the same answer {self._text_streak} times in a row"
        return None

    def note_text(self, text: str) -> str | None:
        """Record a step's reply, returning why it is a loop if it is."""
        if not text.strip():
            return None
        if any(_restates(previous, text) for previous in self._recent_texts):
            self._text_streak += 1
        else:
            self._text_streak = 1
        self._recent_texts.append(text)
        del self._recent_texts[:-_TEXT_WINDOW]
        return self._text_reason()

    def note_restatement(self) -> str | None:
        """Record a reply the restatement guard dropped for repeating itself.

        These are the loops that looked like progress. The user sees their
        answer once and then a run of tool calls that change nothing, because
        each repeat is silently swallowed -- so the turn grinds out the whole
        nudge budget and finally reports itself incomplete, having delivered
        nothing new. Counting the drops turns that into one clear "this is
        going in circles" at the point it becomes obvious.
        """
        self._text_streak += 1
        return self._text_reason()


def _refusal_payload(code: str, explanation: str, trigger: str) -> dict:
    """Build the payload for a `refusal` event.

    Includes the triggering text so the user can see *what* tripped the filter
    without hunting back through the transcript.
    """
    return {
        "code": code,
        "explanation": explanation,
        "trigger": (trigger or "").strip()[:_REFUSAL_TRIGGER_LIMIT],
        "suggestions": list(REFUSAL_SUGGESTIONS),
    }


def _force_compact(
    messages: list[dict],
    *,
    tool_schemas: list[dict] | None = None,
    escalation: int = 1,
) -> list[dict]:
    """Shrink a history the provider has already refused to accept.

    Recovery from a real rejection cannot rely on the token *estimate* that
    triggered proactive compaction, because that estimate is what was wrong.
    So this escalates on a schedule instead of a percentage: each attempt keeps
    a shorter recent tail, the second drops old tool results outright, and
    anything past that strips media — the largest remaining non-text payloads.
    """
    keep_recent = max(4, 12 - (escalation * 6))
    compacted = compact_messages(
        messages,
        keep_recent=keep_recent,
        escalation_level=min(escalation, 1),
    )
    if escalation >= 2:
        compacted = strip_media_from_messages(compacted)
    log.info(
        "Forced compaction (escalation %d, keep_recent %d): %d -> %d messages",
        escalation,
        keep_recent,
        len(messages),
        len(compacted),
    )
    return compacted


@dataclass
class AgentEvent:
    type: str
    data: dict = field(default_factory=dict)


def _loop_event(reason: str) -> AgentEvent:
    """The event that ends a turn the model has gone round in circles on.

    Deliberately not the `incomplete` the nudge budget reports. That says "press
    Continue", which is the wrong advice here: continuing is what produced the
    loop. This says what repeated, so the user can see whether the model was
    stuck on the task or stuck on itself.
    """
    return AgentEvent("error", {
        "message": (
            f"Detected a loop — the agent {reason}. Stopped there rather than "
            "spend the rest of the step budget going round again."
        ),
    })


class Agent:
    def __init__(self, config: Config, session: Session, tools: ToolRegistry, system_prompt: str | None = None, agent_ruleset: PermissionRuleset | None = None, max_steps: int | None = None):
        self.config = config
        self.session = session
        self.tools = tools
        self.llm = LLMClient(config.llm)
        self.system_prompt = system_prompt or build_system_prompt(config.workspace, config.llm.model)
        self.agent_ruleset = agent_ruleset  # Agent-specific permission rules
        # Graceful step budget for this agent. None falls back to the global cap.
        self.max_steps = max_steps
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
        # Synthetic turns belonging to the turn in flight that were never
        # written to the database: the continuation nudge, and the reply it is
        # anchored to. Held apart from `_messages` on purpose. That snapshot is
        # meant to be an exact mirror of the session rows, and appending to it
        # in place broke that in a way that was invisible: `_messages_dirty`
        # could then be cleared over a snapshot holding rows the database did
        # not have, and because `_run` did not mark the snapshot dirty when it
        # added the user's next message, the following turn replayed the stale
        # nudge and dropped the question entirely.
        self._pending_nudges: list[dict] = []
        # Bumped whenever the overlay changes, including when a new nudge
        # replaces the old one at the same length, so the step cache knows to
        # rebuild.
        self._nudge_version: int = 0
        # Compaction state tracking
        self._compaction_summary: str = ""
        self._compaction_count: int = 0
        # Continuation guard: nudges the model when it stops after using tools
        # instead of finishing (reset per run in run()).
        self._run_used_tools: bool = False
        self._since_nudge_tools: bool = False
        self._continuation_nudges: int = 0
        # The answer a continuation nudge was sent about, and whether the next
        # step's prose is being held back so it can be discarded if it only
        # restates that answer (reset per run in run()).
        self._nudge_answer: str | None = None
        self._defer_nudge_text: bool = False
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

    async def _answer_unrun_tool_calls(self, tool_calls: list["ToolCall"]):
        """Write a placeholder result for tool calls that never got to run.

        A turn cut short must still leave a transcript the provider will accept:
        an assistant message carrying ``tool_calls`` has to be followed by one
        tool message per call, or the next request is rejected outright. Calls
        that were streamed but cancelled mid-flight get an explicit "never ran"
        result so the model is not left reasoning about phantom output.
        """
        for tc in tool_calls:
            await self.session.add_message(
                "tool",
                content="Cancelled by user before this tool ran.",
                tool_call_id=tc.id,
            )
        self._messages_dirty = True

    async def _persist_interrupted_step(
        self,
        stream_msg_id: str,
        accumulated_text: str,
        accumulated_reasoning: str,
        tool_calls: list["ToolCall"],
    ):
        """Persist a turn that was cut short, so Stop does not discard the work.

        The placeholder assistant row is written up front, so an interruption
        that returns without an update leaves it empty and the text the user
        already watched stream in is lost. Save it, then answer any tool calls
        so the transcript stays valid.
        """
        tc_dicts = [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
            for tc in tool_calls
        ]
        await self.session.update_message(
            stream_msg_id,
            content=accumulated_text or None,
            tool_calls=tc_dicts or None,
            reasoning_content=accumulated_reasoning or None,
        )
        self._messages_dirty = True
        await self._answer_unrun_tool_calls(tool_calls)

    def _append_nudge(self, assistant_text: str, nudge: str):
        """Queue a continuation nudge for the next step of this turn.

        `messages` is rebuilt from the session rows at the top of each step,
        before that step's own assistant reply is persisted, so the transcript
        handed back to the model for a nudge does not contain the text the user
        just watched stream in. The nudge therefore read as if it arrived
        straight after the tool output: the model had no record of having
        answered, so it re-derived the same answer from the tool result and
        emitted it a second time — the duplicated summary the user sees at the
        end of the turn, on every model.

        The reply itself no longer needs replaying. Persisting it marks the
        snapshot dirty, so the next step re-reads the rows and finds it, which
        puts the answer ahead of the nudge and makes the retry a real
        continuation. Only the nudge itself goes to `_pending_nudges`: a nudge
        is a transient instruction that must not reach the database or a later
        turn, but it does have to survive this turn's remaining steps.

        Kept out of the session snapshot entirely. Appending to the snapshot in
        place made it disagree with the database while `_messages_dirty` claimed
        it did not, and since `_run` did not mark the snapshot dirty when it
        added the user's next message, the following turn replayed the stale
        nudge and dropped the question entirely.

        A step that produced no text (reasoning only) contributes nothing: there
        is nothing for the model to be missing, and nothing to hold the reply
        against.
        """
        if assistant_text.strip():
            # Arms the restatement guard: now that the model can see its own
            # answer, the usual failure moves one step later. Asked to "give
            # your final answer now", it complies by restating what it already
            # said, so that reply is buffered rather than streamed and can be
            # dropped instead of shown and then retracted.
            self._nudge_answer = assistant_text
            self._defer_nudge_text = True
        # Supersede rather than stack. A firmer nudge replaces the gentler one
        # it follows, and one outstanding instruction keeps the transcript's
        # roles alternating instead of trailing a run of user turns.
        self._pending_nudges = [{"role": "user", "content": nudge}]
        self._nudge_version += 1

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

    def _workspace_trusted_for(self, tool_name: str, file_path: str) -> bool:
        """Whether a write/edit tool call for ``file_path`` is trusted this session."""
        return (
            tool_name in ("write", "edit")
            and self._trust_workspace_writes
            and bool(file_path)
            and self._is_in_workspace(file_path)
        )

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
        if self._workspace_trusted_for(tool_name, file_path):
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
        except Exception as e:  # noqa: BLE001 — fall back to legacy trust on any permission-check error
            log.debug("Permission check failed, falling back to legacy: %s", e)

        # Legacy fallback: tools in CONFIRM_TOOLS need confirmation
        return tool_name in CONFIRM_TOOLS

    async def get_permission_action(self, tool_name: str, arguments: dict) -> str:
        """Get the permission action for a tool call. Returns 'allow', 'deny', or 'ask'."""
        file_path = arguments.get("file_path", arguments.get("path", ""))
        # An agent's explicit deny is a hard boundary, so it is checked before
        # trust-all. "Trust all tools" is a session-wide convenience and must not
        # quietly turn a read-only agent (review/research/explore) into one that
        # can write; otherwise the agent's stated contract ("Read-only — never
        # modifies files") depends on a global setting the user set for a
        # different reason.
        if self.agent_ruleset and self.agent_ruleset.check(tool_name, file_path) == "deny":
            return "deny"
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
        """Run one turn, bound to this agent's session.

        The plan list lives on a single, process-wide tool instance, so the
        session is bound for the whole turn via a task-local context. Binding it
        as a plain attribute would let a second session streaming at the same
        time redirect this turn's todo writes into its own plan.
        """
        todo_tool = self.tools.get("todo")
        if todo_tool is None or not hasattr(todo_tool, "load_session"):
            async for event in self._run(user_message, attachments):
                yield event
            return
        # Hydrate before the turn starts: after a restart the only copy of the
        # plan is on disk, and without this the turn would start from an empty
        # list and overwrite it.
        await todo_tool.load_session(self.session.id)
        with todo_tool.active(self.session.id):
            async for event in self._run(user_message, attachments):
                yield event

    async def _run(self, user_message: str, attachments: list[dict] | None = None) -> AsyncIterator[AgentEvent]:
        self.cancel_event.clear()
        # Reset compaction state for new user turn
        self._compaction_summary = ""
        self._compaction_count = 0
        # Reset continuation guard state for new user turn
        self._run_used_tools = False
        self._since_nudge_tools = False
        self._continuation_nudges = 0
        self._nudge_answer = None
        self._defer_nudge_text = False
        # A nudge belongs to the turn that earned it. Clearing the overlay here
        # is what stops one turn's instruction to "carry on with the task" from
        # becoming the newest thing the model sees when the user has moved on.
        self._pending_nudges = []
        self._nudge_version += 1

        await self.session.add_message("user", user_message, attachments=attachments)
        # The snapshot does not contain the row just written. Without this the
        # next read is skipped whenever the previous turn left the flag clear,
        # and the model is sent a transcript with no record of the question.
        self._messages_dirty = True

        try:
            # After Stop, keep draining the generator instead of returning on the
            # spot. Abandoning it here closes it at whichever `yield` it happens
            # to be sitting on, so whatever cleanup that step owes the transcript
            # is skipped: a step mid-confirmation leaves its persisted tool calls
            # with no results, and the next request is rejected outright. `_loop`
            # checks the flag itself at every boundary, so draining it is prompt
            # anyway -- and its events are dropped so nothing new reaches the UI.
            cancelled = False
            async for event in self._loop(user_message):
                if self.cancel_event.is_set():
                    cancelled = True
                    continue
                yield event
            # _loop ended normally — check if it was due to cancel
            if cancelled or self.cancel_event.is_set():
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
        except ContextWindowExceeded:
            # Reached only after the forced-compaction retries in _loop were
            # exhausted. Say what happened and what to change, rather than
            # echoing a raw provider string the user cannot act on.
            msg = (
                "The conversation no longer fits the model's context window, even after "
                "compacting. Start a new session, or raise the context window "
                "(llm.context_window) / lower the compaction threshold to keep more room."
            )
            log.error(msg)
            yield AgentEvent("error", {"message": msg})
            yield AgentEvent("done")
        except ModerationBlocked as e:
            # Not an error the user can fix by retrying, and not something the
            # agent should silently swallow — stop the turn and say what was
            # blocked so they can decide how to proceed.
            log.warning("Provider moderation blocked the request: %s", e.code)
            yield AgentEvent(
                "refusal",
                _refusal_payload(e.code, e.explanation, user_message),
            )
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
        except Exception as e:  # noqa: BLE001 — tool-output cleanup is best-effort, must never break the run
            log.debug("Tool output cleanup failed: %s", e)

    async def _loop(self, user_message: str) -> AsyncIterator[AgentEvent]:
        loop_detector = _LoopDetector()
        hit_max_iterations = False

        # Cache tool schema tokens once (they don't change within a loop)
        tool_schemas = self.tools.schemas()
        openai_tools = self.llm.format_tools(tool_schemas) if tool_schemas else None

        compaction_cfg = self.config.compaction
        compaction_escalation = 0

        # Compaction cache: avoid re-compacting when no new messages arrived
        _cached_messages = None
        _cached_history_len = 0
        _cached_nudge_version = -1

        # Graceful step budget: the per-agent 'steps' limit if configured, else
        # the global max_iterations cap. The per-agent budget BINDS — it is the
        # loop's wrap-up point (tools disabled, model asked to summarise) rather
        # than a hard stop. `max_iterations` is only a fallback for agents that
        # have no per-agent step budget; clamping the per-agent value to it made
        # the admin "Step budget" field silently dead once raised past the global
        # cap (editor pushes it to 200+ and nothing changes).
        if self.max_steps and self.max_steps > 0:
            step_limit = self.max_steps
        else:
            step_limit = self.config.agent.max_iterations

        for iteration in range(step_limit):
            is_last_step = iteration + 1 >= step_limit
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

            # Only rebuild and re-compact when new messages have been added, or
            # when a nudge was queued since the cache was taken — the overlay is
            # replayed into the built list, so a stale cache would drop it.
            if (
                _cached_messages is None
                or len(history) != _cached_history_len
                or self._nudge_version != _cached_nudge_version
            ):
                messages = build_openai_messages(self.system_prompt, history)

                # Resolve the window ONCE and use it for the initial check and
                # every recheck below. Mixing the two sources is what let a
                # genuinely-oversized history look fine after compaction: the
                # post-compaction rechecks read the *configured* window while
                # the trigger used the *effective* one, and for a local backend
                # the detected window is often the smaller of the pair.
                context_window = await effective_context_window(self.config)

                # Check context limits and compact if needed
                ctx = check_context_limit(
                    messages, self.config.llm.model, context_window,
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
                            messages, self.config.llm.model, context_window,
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
                                messages, self.config.llm.model, context_window,
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
                            messages, self.config.llm.model, context_window,
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

                # Replay this turn's nudge (and the reply it is anchored to)
                # after the real rows, so the model reads them as the most
                # recent turns rather than as part of the persisted history.
                messages.extend(self._pending_nudges)

                _cached_messages = messages
                _cached_history_len = len(history)
                _cached_nudge_version = self._nudge_version
            else:
                # Reuse cached compacted messages — no new data to process
                messages = _cached_messages

            # On the final allowed step, disable tools and ask the model for a
            # structured wrap-up instead of stopping abruptly. This mirrors
            # opencode's per-agent step budget: the model summarises what it
            # accomplished, what remains, and what to do next.
            if is_last_step:
                openai_tools = None
                messages = messages + [{"role": "user", "content": MAX_STEPS_WRAPUP}]

            accumulated_text = ""
            accumulated_reasoning = ""
            tool_calls: list[ToolCall] = []
            finish_reason = "stop"

            stream_start = time.monotonic()
            stream_timeout = float(getattr(self.config.llm, "timeout", 120))  # seconds

            # Save placeholder immediately so partial responses survive crashes
            stream_msg_id = await self.session.add_message("assistant", content="")

            # The provider can still reject a request our token estimate
            # thought fit: counting is approximate, and a configured window
            # smaller than the backend's real one is easy to overrun. That
            # rejection lands before a single token is streamed, so compacting
            # and replaying this step cannot duplicate output or events.
            context_retries = 0
            while True:
                try:
                    async for event in self.llm.stream(messages, openai_tools):
                        if self.cancel_event.is_set():
                            # Stop arrived mid-stream. Save what already streamed
                            # before unwinding, otherwise the turn ends with an
                            # empty assistant row and the text the user watched
                            # appear is thrown away.
                            await self._persist_interrupted_step(
                                stream_msg_id, accumulated_text, accumulated_reasoning, tool_calls
                            )
                            return
                        if time.monotonic() - stream_start > stream_timeout:
                            log.warning("LLM stream timed out after %.0fs", stream_timeout)
                            yield AgentEvent("error", {"message": f"LLM stream timed out after {stream_timeout:.0f}s"})
                            break
                        if isinstance(event, TextDelta):
                            accumulated_text += event.content
                            if not self._defer_nudge_text:
                                yield AgentEvent("text_delta", {"content": event.content})

                        elif isinstance(event, ReasoningDelta):
                            accumulated_reasoning += event.content
                            yield AgentEvent("reasoning", {"content": event.content})

                        elif isinstance(event, ToolCall):
                            # Real work: release any held-back preamble before the
                            # call is announced, so prose still reads ahead of the
                            # tool it introduces. From here on the step is not a
                            # candidate for the restatement guard.
                            if self._defer_nudge_text:
                                self._defer_nudge_text = False
                                if accumulated_text:
                                    yield AgentEvent("text_delta", {"content": accumulated_text})
                            tool_calls.append(event)
                            yield AgentEvent("tool_call", {
                                "id": event.id,
                                "name": event.name,
                                "arguments": event.arguments,
                            })

                        elif isinstance(event, Finish):
                            finish_reason = event.finish_reason or "stop"
                            # Record usage for cost tracking. Self-hosted backends have
                            # no per-token cost, so flag them rather than charging
                            # list price for local inference.
                            self.cost_tracker.record_usage(
                                model=self.config.llm.model,
                                prompt_tokens=event.usage.prompt_tokens,
                                completion_tokens=event.usage.completion_tokens,
                                local=not is_external_backend(self.config.llm.base_url),
                            )
                            yield AgentEvent("finish", {
                                "reason": event.finish_reason,
                                "usage": {
                                    "prompt_tokens": event.usage.prompt_tokens,
                                    "completion_tokens": event.usage.completion_tokens,
                                },
                            })

                    break

                except ContextWindowExceeded as e:
                    if context_retries >= MAX_CONTEXT_RETRIES or self.cancel_event.is_set():
                        log.error(
                            "Context window still exceeded after %d forced compactions: %s",
                            context_retries, e,
                        )
                        raise
                    context_retries += 1
                    log.warning(
                        "Provider rejected the request: context window exceeded "
                        "(compaction %d/%d). Compacting harder and retrying",
                        context_retries,
                        MAX_CONTEXT_RETRIES,
                    )
                    messages = _force_compact(
                        messages,
                        tool_schemas=tool_schemas,
                        escalation=context_retries,
                    )
                    self._compaction_count += 1
                    yield AgentEvent("compacted", {
                        "message": "Context window exceeded — compacted and retrying",
                        "mode": "forced",
                        "count": self._compaction_count,
                    })
                    # The tail may have ended on tool results (the agent was
                    # mid-work); nudge it so the replay continues the task.
                    if messages and messages[-1].get("role") == "tool":
                        messages.append({
                            "role": "user",
                            "content": "[Context was compacted to save space. Continue with your next steps if the task is not yet complete.]",
                        })
            # A safety filter that also produced text (or tool calls) is a partial
            # result, not a refusal — keep it and let the normal flow continue.
            # Only a turn that came back empty *and* carries a filter finish
            # reason is a genuine refusal, and it must not reach the user as a
            # blank assistant bubble.
            if (
                finish_reason in REFUSAL_FINISH_REASONS
                and not tool_calls
                and not accumulated_text.strip()
            ):
                log.warning("Generation refused by provider safety filter: %s", finish_reason)
                await self.session.update_message(stream_msg_id, content=None)
                yield AgentEvent(
                    "refusal",
                    _refusal_payload(
                        finish_reason,
                        "The provider's safety filter stopped this response before "
                        "any content was generated.",
                        user_message,
                    ),
                )
                yield AgentEvent("done")
                return

            # Restatement guard for the reply that answers a continuation nudge.
            # Asked to "give your final answer now", a model reliably complies by
            # re-delivering the answer it already gave. Its prose is held back
            # rather than streamed, so a repeat can be dropped instead of shown
            # and then retracted.
            #
            # Held until a step actually produces prose. Clearing the flag on the
            # next step regardless meant the guard only ever worked when the
            # model answered straight away: a step that went back to its tools
            # consumed the deferral with nothing to compare, and the reply that
            # really did answer the nudge went unchecked. Using tools again and
            # then restating is the common order, so the guard was mostly inert.
            if self._defer_nudge_text and accumulated_text.strip():
                self._defer_nudge_text = False
                if _restates(self._nudge_answer or "", accumulated_text):
                    log.info(
                        "Dropped a nudged reply that restated the answer it was "
                        "nudged about (%d chars over %d)",
                        len(accumulated_text), len(self._nudge_answer or ""),
                    )
                    # Remove the placeholder outright, not just empty it: a blank
                    # assistant row would be replayed to the provider on every
                    # later turn and rendered as an empty bubble on reload.
                    await self.session.delete_message(stream_msg_id)
                    self._messages_dirty = True
                    accumulated_text = ""
                    looping = loop_detector.note_restatement()
                    if looping:
                        log.warning("LLM loop detected: %s", looping)
                        yield _loop_event(looping)
                        break
                else:
                    yield AgentEvent("text_delta", {"content": accumulated_text})
                self._nudge_answer = None

            if tool_calls:
                # A model that keeps asking for the same thing never repeats
                # itself in prose, so check the calls before running them. Drop
                # the step rather than persisting calls we will not run: an
                # assistant row carrying tool_calls has to be followed by one
                # tool message per call, and there is nothing here to answer.
                looping = loop_detector.note_tool_calls(tool_calls)
                if looping:
                    log.warning("LLM loop detected: %s", looping)
                    await self.session.delete_message(stream_msg_id)
                    self._messages_dirty = True
                    yield _loop_event(looping)
                    break

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
                # Every call needs a persisted result, so track which ones still
                # lack one. The assistant row above already advertises all of
                # them, and a call left unanswered here makes the transcript
                # invalid for the next request — the provider rejects it, or a
                # lenient local backend reads the rest of the conversation
                # against a phantom call and carries on with the old task.
                unanswered = {tc.id for tc in tool_calls}
                try:
                    for tc in tool_calls:
                        if self.cancel_event.is_set():
                            break
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
                            unanswered.discard(tc.id)
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
                            unanswered.discard(tc.id)
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
                                unanswered.discard(tc.id)
                                self._messages_dirty = True
                                yield AgentEvent("tool_result", {
                                    "id": tc.id,
                                    "name": tc.name,
                                    "output": "Denied by user",
                                })
                                continue
                        confirmed_tool_calls.append(tc)
                except asyncio.CancelledError:
                    # Stop was pressed while a confirmation was pending. Answer
                    # what is still outstanding before unwinding, or the next
                    # request carries an unanswered tool call. Shielded so a
                    # second cancel cannot tear the writes off half-written.
                    await asyncio.shield(self._answer_unrun_tool_calls(
                        [tc for tc in tool_calls if tc.id in unanswered]
                    ))
                    raise

                if self.cancel_event.is_set():
                    # Stop landed between calls. Everything still unanswered —
                    # this call, the ones after it, and any already approved but
                    # not yet run — gets an explicit result, so the transcript
                    # the next turn reads is valid.
                    await self._answer_unrun_tool_calls(
                        [tc for tc in tool_calls if tc.id in unanswered]
                    )
                    return

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
                    try:
                        results = await asyncio.gather(*[_exec_tool(tc) for tc in confirmed_tool_calls])
                    except asyncio.CancelledError:
                        # Stop was pressed while the tools were running. The
                        # assistant message with its tool_calls is already
                        # persisted, so answer every call we did not finish or
                        # the transcript becomes invalid for the next turn. The
                        # writes are shielded so a second cancel (the button
                        # stays live until the run ends) cannot tear them off
                        # half-written.
                        await asyncio.shield(self._answer_unrun_tool_calls(confirmed_tool_calls))
                        raise
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
                        except Exception:  # noqa: BLE001 — KB logging is best-effort; a failed write must not break the loop
                            log.debug("Failed to log tool execution")
                        if tc.name == "todo":
                            todo_tool = self.tools.get("todo")
                            if todo_tool and hasattr(todo_tool, "get_tasks"):
                                # Read this session's plan explicitly. The tool is
                                # a shared process-wide instance, so a bare
                                # get_tasks() would report whichever session
                                # happens to be bound right now.
                                yield AgentEvent(
                                    "plan_update",
                                    {"tasks": todo_tool.get_tasks(self.session.id)},
                                )

                continue

            if accumulated_text or accumulated_reasoning:
                await self.session.update_message(
                    stream_msg_id,
                    content=accumulated_text or None,
                    reasoning_content=accumulated_reasoning or None,
                )
                self._messages_dirty = True

                # Repetition detection. Matching on vocabulary and size rather
                # than equality is the point: the repeats that reach here are
                # paraphrases, so identical-string comparison never fires and the
                # turn runs on re-deriving the same answer until the budget ends.
                looping = loop_detector.note_text(accumulated_text)
                if looping:
                    log.warning("LLM loop detected: %s", looping)
                    yield _loop_event(looping)
                    break

            # Continuation guard. If the model stopped using tools (a text-only
            # response) after having used tools this run, it may be wrapping up
            # prematurely. Just because tools ran does not mean the task is done,
            # so nudge it to keep going when work remains while still offering an
            # escape hatch for legitimate research-only questions. Decide as
            # follows:
            #  - First stop -> gentle nudge with an escape hatch.
            #  - Model gives a final answer without further tools -> respect it.
            #  - Model works more then stops again -> firmer nudge, no escape
            #    hatch, so it cannot answer its way out.
            #  - Nudge budget spent -> flag the task incomplete rather than
            #    falsely marking it complete.
            # On the final allowed step tools are disabled, so any text-only
            # response is the structured wrap-up — finish rather than nudge.
            if self._run_used_tools and not is_last_step:
                if not self._continuation_nudges:
                    self._continuation_nudges = 1
                    self._since_nudge_tools = False
                    log.warning("LLM stopped after using tools without finishing; nudging to continue.")
                    self._append_nudge(accumulated_text, CONTINUATION_NUDGE)
                    continue
                if not self._since_nudge_tools:
                    log.debug("LLM gave a final answer after a nudge; treating as complete.")
                elif self._continuation_nudges < MAX_CONTINUATION_NUDGES:
                    self._continuation_nudges += 1
                    self._since_nudge_tools = False
                    log.warning(
                        "LLM stopped again after using tools (nudge %d/%d); pushing further.",
                        self._continuation_nudges, MAX_CONTINUATION_NUDGES,
                    )
                    self._append_nudge(accumulated_text, CONTINUATION_NUDGE_FIRM)
                    continue
                else:
                    log.warning(
                        "LLM kept stopping after using tools (%d nudges); marking incomplete.",
                        self._continuation_nudges,
                    )
                    yield AgentEvent("incomplete", {
                        "message": "The task may not be complete: the agent kept stopping after using tools and did not finish. Use Continue to let it keep going.",
                    })
                    return

            break
        else:
            hit_max_iterations = True

        if not self.cancel_event.is_set() and hit_max_iterations:
            yield AgentEvent("error", {
                "message": f"Reached maximum iterations ({step_limit}). "
                           "The task may not be fully complete. You can continue in a new message."
            })

        yield AgentEvent("done")
