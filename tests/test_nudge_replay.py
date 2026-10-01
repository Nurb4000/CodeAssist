"""End-to-end shape of the continuation nudge, with no message-building mocked.

The earlier coverage for this stubbed ``build_openai_messages`` out to a single
user row, which is enough to assert "an assistant entry was inserted" but not to
prove the replayed transcript is a *valid* conversation. These tests drive the
real ``build_openai_messages`` over real row-shaped history, so the ordering,
the tool-call/tool-result pairing and the role alternation are all exercised.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeassist.agent import (
    CONTINUATION_NUDGE,
    CONTINUATION_NUDGE_FIRM,
    Agent,
    _restates,
)
from codeassist.llm import Finish, TextDelta, ToolCall, Usage
from codeassist.tools import ToolResult


class FakeSession:
    """Stores rows the way the SQLite session does, so history is realistic.

    ``add_message`` appends immediately (the loop creates the assistant
    placeholder before streaming) and ``update_message`` fills it in, which is
    the behaviour that makes the snapshot/replay interaction worth testing.
    """

    def __init__(self):
        self.id = "sess-1"
        self.rows: list[dict] = []
        self._seq = 0

    async def add_message(self, role, content=None, tool_calls=None,
                          tool_call_id=None, name=None, attachments=None,
                          reasoning_content=None):
        self._seq += 1
        mid = f"m{self._seq}"
        self.rows.append({
            "id": mid,
            "session_id": self.id,
            "role": role,
            "content": content,
            "tool_call_id": tool_call_id,
            "tool_calls": json.dumps(tool_calls) if tool_calls else None,
            "name": name,
            "reasoning_content": reasoning_content,
            "created_at": f"2026-01-01T00:00:{self._seq:02d}",
            "attachments": [],
        })
        return mid

    async def update_message(self, message_id, content=None, tool_calls=None,
                             reasoning_content=None):
        for row in self.rows:
            if row["id"] == message_id:
                if content is not None:
                    row["content"] = content
                if tool_calls is not None:
                    row["tool_calls"] = json.dumps(tool_calls)
                if reasoning_content is not None:
                    row["reasoning_content"] = reasoning_content
                return

    async def get_messages(self):
        return [dict(r) for r in self.rows]

    async def delete_message(self, message_id):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r["id"] != message_id]
        return before - len(self.rows)


def _roles(messages):
    """Roles, with tool results attributed to the call they answer."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append("tool")
        else:
            out.append(m["role"])
    return out


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.workspace = tmp_path
    cfg.llm.model = "gpt-4o"
    cfg.llm.context_window = 128000
    cfg.llm.max_tokens = 4096
    cfg.llm.base_url = ""
    cfg.agent.max_iterations = 20
    cfg.tools.tool_output_max_tokens = 1000000
    return cfg


@pytest.fixture
def agent(config):
    tools = MagicMock()
    tools.schemas = MagicMock(return_value=[])
    tools.get = MagicMock(return_value=None)
    tools.execute = AsyncMock(return_value=ToolResult(output="axolotl/\nZoe/", error=False))
    with patch("codeassist.agent.LLMClient"):
        agent = Agent(config, FakeSession(), tools, system_prompt="sys")
    agent.llm = MagicMock()
    agent.llm.format_tools = MagicMock(return_value=None)
    agent._trust_all = True
    agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)
    return agent


async def _run(agent, turns, patch_ctx=True):
    """Drive `turns` through the loop, returning the message list per step."""
    seen: list[list[dict]] = []
    calls = {"n": 0}

    async def fake_stream(messages, openai_tools):
        seen.append([dict(m) for m in messages])
        gen = turns[calls["n"]]
        calls["n"] += 1
        async for ev in gen():
            yield ev

    agent.llm.stream = fake_stream
    events = []
    stack = patch("codeassist.agent.check_context_limit") if patch_ctx else None
    ctx = None
    if patch_ctx:
        ctx = stack.__enter__()
        ctx.return_value = {"needs_compaction": False, "total_tokens": 10,
                            "usage_pct": 1.0, "severity": "ok"}
    try:
        with patch("codeassist.agent.effective_context_window",
                   new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution",
                   new=AsyncMock()):
            async for ev in agent.run("list the folders"):
                events.append(ev)
    finally:
        if patch_ctx:
            stack.__exit__(None, None, None)
    return seen, events


def _list_folders(call_id="c1"):
    async def gen():
        yield ToolCall(id=call_id, name="read", arguments={"path": "/tmp/x"})
        yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
    return gen


def _say(text):
    async def gen():
        for chunk in text:
            yield TextDelta(chunk)
        yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
    return gen


ANSWER = "Here are the folders:\n\naxolotl/\nZoe/\n\nWant me to explore any?"


@pytest.mark.asyncio
async def test_first_nudge_replays_a_valid_conversation(agent):
    """The nudged step must be a valid transcript: the tool call is still paired
    with its result, and the answer the user already saw sits between them and
    the nudge."""
    seen, _ = await _run(agent, [_list_folders(), _say([ANSWER]), _say(["Happy to dig in."])])

    nudged = seen[2]
    assert _roles(nudged) == ["system", "user", "assistant", "tool", "assistant", "user"]

    # The assistant entry that carries the tool_calls must still be intact and
    # followed by the matching tool result, or the provider rejects the request.
    assert nudged[2]["tool_calls"][0]["function"]["name"] == "read"
    assert nudged[3]["tool_call_id"] == "c1"
    assert nudged[4] == {"role": "assistant", "content": ANSWER}
    assert CONTINUATION_NUDGE in nudged[5]["content"]


@pytest.mark.asyncio
async def test_second_nudge_replays_a_valid_conversation(agent):
    """The firm nudge is the same code path and must be just as well formed, and
    must not drop the first answer."""
    seen, _ = await _run(agent, [
        _list_folders("c1"),
        _say([ANSWER]),
        _list_folders("c2"),
        _say(["Still going."]),
        _say(["Done."]),
    ])

    firm = seen[4]
    # The first nudge is deliberately absent. It is a transient instruction, never
    # persisted to the session, and the tool call in the middle of this turn made
    # the loop refetch history -- which is the desired outcome: the model is
    # reasoning about real rows again rather than about an injected reminder. What
    # must survive the refetch is the answer, since that is real conversation.
    assert _roles(firm) == [
        "system", "user", "assistant", "tool", "assistant",
        "assistant", "tool", "assistant", "user",
    ]
    assert firm[4]["content"] == ANSWER
    assert firm[7]["content"] == "Still going."
    assert firm[2]["tool_calls"][0]["function"]["name"] == "read"
    assert firm[6]["tool_call_id"] == "c2"
    assert CONTINUATION_NUDGE_FIRM in firm[8]["content"]
    # Neither answer was asked for twice.
    assert [m.get("content") for m in firm].count(ANSWER) == 1


@pytest.mark.asyncio
async def test_nudge_after_reasoning_only_step_stays_valid(agent):
    """A step can emit reasoning and no text. There is nothing to re-insert, so
    the nudge follows the tool result directly and the transcript stays valid."""
    async def reasoning_only():
        from codeassist.llm import ReasoningDelta
        yield ReasoningDelta("thinking")
        yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

    seen, _ = await _run(agent, [_list_folders(), reasoning_only, _say(["Here."])])

    nudged = seen[2]
    assert _roles(nudged) == ["system", "user", "assistant", "tool", "user"]
    assert CONTINUATION_NUDGE in nudged[-1]["content"]


@pytest.mark.asyncio
async def test_answer_reaches_the_session_history_once(agent):
    """Whatever the loop decides, the transcript the next turn rebuilds from must
    hold the answer exactly once -- duplicated rows would compound every turn."""
    await _run(agent, [_list_folders(), _say([ANSWER]), _say(["Happy to dig in."])])

    roles = [r["role"] for r in agent.session.rows]
    assert roles == ["user", "assistant", "tool", "assistant", "assistant"]
    answers = [r["content"] for r in agent.session.rows if r["content"] == ANSWER]
    assert len(answers) == 1


# --- Restatement guard ------------------------------------------------------
#
# The transcript fix above is necessary but not sufficient. With its own answer
# visible, a model asked to "give your final answer now" still frequently
# complies by re-delivering that answer under a "the task is complete" preamble.
# Observed verbatim: the user listed the workspace, and got the listing twice,
# the second time introduced by "The task is complete. You asked me to list the
# workspace folders non-recursively, and that's done:".

LISTING = """Here's the non-recursive listing - just the top-level contents of /workspace:

Directories (top-level only):

alemba-api-findings/
axolotl/
axolotl dataset creator/
axolotl-docker/
runtime/
YuE-musicgen/
Zoe/

Files (top-level only):

YuE-musicgen-package.tar.gz  25.5 KB

So at the top level there are 7 folders and 1 file. Everything else only shows
up in the recursive view because they live inside those top-level folders."""

RESTATEMENT = """The task is complete. You asked me to list the workspace folders
non-recursively, and that's done:

Top-level directories in /workspace:

alemba-api-findings/
axolotl/
axolotl dataset creator/
axolotl-docker/
runtime/
YuE-musicgen/
Zoe/

Top-level files:

YuE-musicgen-package.tar.gz (25.5 KB)

That's 7 folders and 1 file at the top level. Everything else lives nested
inside those folders, which is why it only appears in the recursive view. No
further work remains unless you'd like me to explore any of these."""

GENUINE = "I also checked the tarball: it contains 3 archives, so nothing is missing."


def _streamed_text(events):
    return "".join(
        e.data["content"] for e in events if e.type == "text_delta"
    )


@pytest.mark.asyncio
async def test_restating_reply_is_dropped_not_shown(agent):
    """The user must not see the listing twice. Only the first answer's text is
    streamed, and the loop finishes rather than nudging again."""
    _, events = await _run(agent, [
        _list_folders("c1"),
        _say([LISTING]),
        _say([RESTATEMENT]),
        _say(["unreachable"]),
    ])

    assert _streamed_text(events) == LISTING
    types = [e.type for e in events]
    assert types.count("done") == 1
    # It was treated as a final answer, not pushed further.
    assert agent._continuation_nudges == 1
    # Only three steps were spent: tool, answer, dropped restatement.
    assert [r["role"] for r in agent.session.rows] == [
        "user", "assistant", "tool", "assistant",
    ]
    assert "unreachable" not in _streamed_text(events)


@pytest.mark.asyncio
async def test_restatement_is_not_left_in_history(agent):
    """The dropped reply must not linger as an empty assistant row that later
    turns replay — otherwise the transcript accumulates blanks."""
    _, _ = await _run(agent, [
        _list_folders("c1"),
        _say([LISTING]),
        _say([RESTATEMENT]),
    ])

    assistant_rows = [r for r in agent.session.rows if r["role"] == "assistant"]
    assert [r["content"] for r in assistant_rows] == ["", LISTING]
    # Nothing that a rebuild would send back as a blank turn.
    sent = [m for m in agent.session.rows if m["role"] == "assistant" and (m["content"] or "").strip()]
    assert [m["content"] for m in sent] == [LISTING]


@pytest.mark.asyncio
async def test_genuine_reply_to_nudge_is_kept(agent):
    """The guard must not swallow real content. A reply that adds new findings is
    shown, even though it re-uses much of the earlier answer's vocabulary."""
    _, events = await _run(agent, [
        _list_folders("c1"),
        _say([LISTING]),
        _say([GENUINE]),
    ])

    assert GENUINE in _streamed_text(events)


@pytest.mark.asyncio
async def test_reply_that_keeps_working_is_kept(agent):
    """When the nudged step goes back to tools, the held-back preamble is
    released before the tool call is announced, so prose still reads ahead of the
    tool it introduces rather than appearing after it."""
    async def preamble_then_tool():
        yield TextDelta("Checking the tarball contents too.")
        yield ToolCall(id="c2", name="read", arguments={"path": "/tmp/x"})
        yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

    _, events = await _run(agent, [
        _list_folders("c1"),
        _say([LISTING]),
        preamble_then_tool,
        _say(["All done."]),
    ])

    order = [e.type for e in events]
    prose_at = order.index("text_delta")
    tool_at = next(i for i, e in enumerate(events)
                   if e.type == "tool_call" and e.data["id"] == "c2")
    assert prose_at < tool_at, "preamble must be streamed before the tool call"
    assert "Checking the tarball contents too." in _streamed_text(events)


SHORT_ANSWER = "The parser fix is in scripts/parse.py. All 412 tests pass."
SHORT_RESTATEMENT = (
    "That's done - the parser fix went into scripts/parse.py, and the suite is "
    "green: 412 tests pass."
)


@pytest.mark.asyncio
async def test_guard_state_is_released_after_the_nudged_step(agent):
    """The guard must not leak across turns. A reply that answers a nudge ends the
    turn, so the flags are always cleared by the time run() returns."""
    _, _ = await _run(agent, [
        _list_folders("c1"),
        _say([LISTING]),
        _say([RESTATEMENT]),
    ])

    assert agent._defer_nudge_text is False
    assert agent._nudge_answer is None


@pytest.mark.asyncio
async def test_firm_nudge_is_guarded_too(agent):
    """The second, firmer nudge is the same code path, so a reply that re-delivers
    the answer it was nudged about must be dropped there as well."""
    _, events = await _run(agent, [
        _list_folders("c1"),
        _say([SHORT_ANSWER]),
        _list_folders("c2"),
        _say([SHORT_ANSWER]),
        _say([SHORT_RESTATEMENT]),
        _say(["unreachable"]),
    ])

    assert agent._continuation_nudges == 2
    assert _streamed_text(events).count("scripts/parse.py") == 2  # both real answers
    assert SHORT_RESTATEMENT not in _streamed_text(events)


class TestRestateDetector:
    """Unit coverage for the threshold logic, away from the loop."""

    def test_flags_a_reworded_repeat(self):
        assert _restates(LISTING, RESTATEMENT)

    def test_ignores_a_genuine_follow_up(self):
        assert not _restates(LISTING, GENUINE)

    def test_ignores_a_materially_longer_answer(self):
        """Repeating the vocabulary is fine when the reply is much bigger -- that
        is an answer that extends the previous one, and its new part is the point."""
        extended = RESTATEMENT + "\n\n" + ("\n".join(f"- extra finding {i}" for i in range(60)))
        assert not _restates(LISTING, extended)

    def test_ignores_a_terse_follow_up(self):
        """The other side of the size band: a short new remark is not a restatement,
        however much of the earlier answer's vocabulary it re-uses."""
        assert not _restates(SHORT_ANSWER, "Also fixed the timeout in test_ci.py.")

    def test_ignores_a_same_length_reply_about_something_else(self):
        """The size band alone would drop this. The overlap requirement is what
        saves it -- it is the same length as the answer but shares nothing."""
        other = (
            "The test suite is green after the parser fix. 412 tests pass, 1 was "
            "skipped because it needs network access. I also corrected the timeout "
            "in the integration test, which was masking a slow mock server response."
        )
        assert not _restates(SHORT_ANSWER, other)

    def test_ignores_an_unrelated_reply(self):
        assert not _restates(LISTING, "The build is green; 412 tests pass.")

    def test_empty_input_is_never_a_restatement(self):
        assert not _restates("", "anything at all")
        assert not _restates(LISTING, "")

    def test_short_prior_is_not_matched_by_a_single_word(self):
        """One shared word out of a two-word answer must not count as a repeat."""
        assert not _restates("Done.", "Done, and I checked the manifest too.")
