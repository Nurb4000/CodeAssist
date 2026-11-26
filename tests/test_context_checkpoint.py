"""Tests for persisted, non-destructive context checkpoints.

The point of a checkpoint is that a *fresh* Agent for an existing session starts
from the compacted transcript instead of replaying (and re-summarizing) the full
history. It is a derived cache, so history edits and prompt changes must make it
stale rather than silently feeding the model a transcript that no longer matches
what the user sees.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeassist.agent import Agent
from codeassist.context_checkpoint import (
    compute_config_fingerprint,
    compute_history_fingerprint,
)
from codeassist.cost_tracker import BudgetConfig
from codeassist.llm import Finish, TextDelta, ToolCall
from codeassist.session import SCHEMA_VERSION, Session, get_db, init_db
from codeassist.tools import ToolResult

FILLED_SUMMARY = """## Objective
- Finish the checkpoint work.

## Work State
### Completed
- Wrote the cache.

### Active
- Testing restore.

### Blocked
- (none)

## Next Move
1. Run the tests.
2. Push the branch.

## Relevant Files
- codeassist/agent.py

## Important Context
- Non-destructive.
"""


def _config() -> MagicMock:
    config = MagicMock()
    config.workspace = Path("/tmp")
    config.llm.model = "test-model"
    config.llm.context_window = 8000
    config.llm.max_tokens = 0
    config.llm.base_url = ""
    config.agent.max_iterations = 3
    config.cost = BudgetConfig()
    config.compaction.enabled = True
    config.compaction.mode = "text"
    config.compaction.threshold_pct = 85
    config.compaction.tail_turns = 1
    config.compaction.keep_recent = 2
    config.compaction.preserve_recent_tokens = 500
    config.compaction.tool_result_max_tokens = 500
    config.compaction.model = ""
    return config


def _agent(config: MagicMock, session: Session, prompt: str = "Test prompt") -> Agent:
    tools = MagicMock()
    tools.schemas = MagicMock(return_value=[])
    tools.get = MagicMock(return_value=None)
    with patch("codeassist.agent.LLMClient"):
        return Agent(config, session, tools, system_prompt=prompt)


async def _seed(session: Session, turns: int = 3) -> list[dict]:
    for i in range(turns):
        await session.add_message("user", f"question {i}")
        await session.add_message("assistant", f"answer {i}")
    return await session.get_messages()


def _fingerprints(config: MagicMock, history: list[dict], covered: int, prompt: str = "Test prompt"):
    return (
        compute_history_fingerprint(history[:covered]),
        compute_config_fingerprint(prompt, config),
    )


@pytest.mark.asyncio
async def test_migration_creates_checkpoint_table():
    await init_db()
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='session_context_checkpoints'"
        )
        assert await cursor.fetchone() is not None
        cursor = await db.execute("SELECT value FROM schema_info WHERE key = 'version'")
        assert int((await cursor.fetchone())[0]) == SCHEMA_VERSION


@pytest.mark.asyncio
async def test_save_load_replace_and_invalidate():
    await init_db()
    session = await Session.create()
    assert await session.load_context_checkpoint() is None

    await session.save_context_checkpoint(
        messages=[{"role": "system", "content": "Test prompt"}],
        summary_text="first",
        covered_count=1,
        watermark_id="m1",
        history_fingerprint="h1",
        config_fingerprint="c1",
        tokens=42,
    )
    row = await session.load_context_checkpoint()
    assert row["summary_text"] == "first"
    assert row["tokens"] == 42
    assert row["created_at"] and row["updated_at"]

    await session.save_context_checkpoint(
        messages=[{"role": "system", "content": "Test prompt"}],
        summary_text="second",
        covered_count=2,
        watermark_id="m2",
        history_fingerprint="h2",
        config_fingerprint="c2",
    )
    row = await session.load_context_checkpoint()
    assert row["summary_text"] == "second"
    assert row["covered_count"] == 2

    await session.invalidate_context_checkpoint()
    assert await session.load_context_checkpoint() is None


def test_history_fingerprint_tracks_content_and_attachments():
    a = [{"id": "m1", "role": "user", "content": "hello"}]
    b = [{"id": "m1", "role": "user", "content": "hello"}]
    assert compute_history_fingerprint(a) == compute_history_fingerprint(b)

    edited = [{"id": "m1", "role": "user", "content": "goodbye"}]
    assert compute_history_fingerprint(edited) != compute_history_fingerprint(a)

    attached = [{**a[0], "attachments": [{"data": "image"}]}]
    assert compute_history_fingerprint(attached) != compute_history_fingerprint(a)

    reordered = [{"id": "m2", "role": "user", "content": "second"}, a[0]]
    assert compute_history_fingerprint(reordered) != compute_history_fingerprint(a)


@pytest.mark.asyncio
async def test_fresh_agent_restores_compacted_transcript():
    await init_db()
    session = await Session.create()
    history = await _seed(session)
    config = _config()
    covered = 2
    hist_fp, cfg_fp = _fingerprints(config, history, covered)
    transcript = [
        {"role": "system", "content": "Test prompt"},
        {"role": "user", "content": "[Previous conversation summary]\nSaved summary"},
    ]
    await session.save_context_checkpoint(
        messages=transcript,
        summary_text="Saved summary",
        covered_count=covered,
        watermark_id=history[covered - 1]["id"],
        history_fingerprint=hist_fp,
        config_fingerprint=cfg_fp,
    )

    fresh = _agent(config, session)
    assert fresh._working_messages is None
    await fresh._restore_context_checkpoint(history)

    assert fresh._working_messages == transcript
    assert fresh._working_history_len == covered
    assert fresh._compaction_summary == "Saved summary"


@pytest.mark.asyncio
async def test_stale_checkpoint_is_discarded_when_history_changes():
    await init_db()
    session = await Session.create()
    history = await _seed(session)
    config = _config()
    covered = 2
    hist_fp, cfg_fp = _fingerprints(config, history, covered)
    await session.save_context_checkpoint(
        messages=[{"role": "system", "content": "stale"}],
        summary_text="old",
        covered_count=covered,
        watermark_id=history[covered - 1]["id"],
        history_fingerprint=hist_fp,
        config_fingerprint=cfg_fp,
    )

    # An undo/rollback/edit changes a row the checkpoint covered.
    await session.update_message(history[0]["id"], content="edited")
    changed = await session.get_messages()

    fresh = _agent(config, session)
    await fresh._restore_context_checkpoint(changed)

    assert fresh._working_messages is None
    assert await session.load_context_checkpoint() is None, "stale cache must be removed"


@pytest.mark.asyncio
async def test_stale_checkpoint_is_discarded_when_prompt_changes():
    await init_db()
    session = await Session.create()
    history = await _seed(session)
    config = _config()
    covered = 2
    hist_fp, _ = _fingerprints(config, history, covered)
    await session.save_context_checkpoint(
        messages=[{"role": "system", "content": "old prompt"}],
        summary_text="old",
        covered_count=covered,
        watermark_id=history[covered - 1]["id"],
        history_fingerprint=hist_fp,
        config_fingerprint=compute_config_fingerprint("old prompt", config),
    )

    fresh = _agent(config, session, prompt="new prompt")
    await fresh._restore_context_checkpoint(history)

    assert fresh._working_messages is None
    assert await session.load_context_checkpoint() is None


@pytest.mark.asyncio
async def test_manual_checkpoint_is_non_destructive():
    await init_db()
    session = await Session.create()
    history = await _seed(session, turns=4)
    agent = _agent(_config(), session)

    with (
        patch("codeassist.agent._summarize_head", new=AsyncMock(return_value=FILLED_SUMMARY)),
        patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=8000)),
    ):
        result = await agent.checkpoint_now()

    assert result["saved"] is True
    assert (await session.get_messages()) == history, "raw messages must be untouched"
    row = await session.load_context_checkpoint()
    assert row is not None
    assert row["covered_count"] == len(history)
    assert agent._working_messages[1]["content"].startswith("[Previous conversation summary]")
    assert agent._compaction_summary == FILLED_SUMMARY


@pytest.mark.asyncio
async def test_manual_compact_handles_raw_db_tool_calls():
    """Raw DB rows store ``tool_calls`` as a JSON string. Manual compaction
    summarizes those rows directly, so it must parse them instead of raising
    ``AttributeError: 'str' object has no attribute 'get'``.
    """
    await init_db()
    session = await Session.create()
    for i in range(3):
        await session.add_message("user", f"question {i}")
        await session.add_message(
            "assistant", "working",
            tool_calls=[{
                "id": f"c{i}",
                "type": "function",
                "function": {"name": "read", "arguments": '{"file_path":"a.py"}'},
            }],
        )
        await session.add_message("tool", f"result {i}", tool_call_id=f"c{i}")

    captured: list[str] = []

    async def fake_summary(head_text, **kwargs):
        captured.append(head_text)
        return FILLED_SUMMARY

    agent = _agent(_config(), session)
    with (
        patch("codeassist.agent._summarize_head", new=fake_summary),
        patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=8000)),
    ):
        result = await agent.checkpoint_now()

    assert result["saved"] is True
    assert captured, "summarizer should have received the head text"
    assert "read(" in captured[0], "the tool call should be rendered, not skipped"
    assert await session.load_context_checkpoint() is not None


@pytest.mark.asyncio
async def test_manual_compact_on_fresh_agent_reuses_previous_summary():
    """A reconnect followed by a manual compact must build on the saved summary.

    Without the restore-on-compact hook, a fresh Agent would start with an empty
    ``_compaction_summary`` and summarize the whole head from scratch, losing the
    progress accumulated over earlier checkpoints.
    """
    await init_db()
    session = await Session.create()
    await _seed(session, turns=6)
    config = _config()

    seen_previous: list[str] = []

    async def fake_summary(head_text, **kwargs):
        seen_previous.append(kwargs.get("previous_summary") or "")
        return FILLED_SUMMARY

    with (
        patch("codeassist.agent._summarize_head", new=fake_summary),
        patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=8000)),
    ):
        assert (await _agent(config, session).checkpoint_now())["saved"] is True

        # Simulate a reconnect: brand-new agent, same session.
        fresh = _agent(config, session)
        assert (await fresh.checkpoint_now())["saved"] is True

    assert seen_previous[0] == ""
    assert seen_previous[1] == FILLED_SUMMARY


@pytest.mark.asyncio
async def test_auto_compaction_persists_checkpoint():
    await init_db()
    session = await Session.create()
    for i in range(6):
        await session.add_message("user", f"q{i}")
        await session.add_message(
            "assistant", "working",
            tool_calls=[{
                "id": f"old{i}", "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }],
        )
        await session.add_message(
            "tool", f"OLD-{i}-START " + ("z" * 600) + f" OLD-{i}-END",
            tool_call_id=f"old{i}",
        )

    config = _config()
    config.compaction.mode = "text"
    config.compaction.threshold_pct = 50
    config.compaction.keep_recent = 2
    agent = _agent(config, session)
    agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
    agent._trust_all = True
    agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

    calls: list[list[dict]] = []

    async def fake_stream(messages, openai_tools):
        calls.append(list(messages))
        if len(calls) == 1:
            yield ToolCall(id="c1", name="read", arguments={"file_path": "a.py"})
        else:
            yield TextDelta("All done.")
        yield Finish("stop", usage=None)

    agent.llm.stream = fake_stream
    with patch(
        "codeassist.agent.effective_context_window",
        new=AsyncMock(return_value=2000),
    ):
        events = [event async for event in agent.run("go")]

    assert any(e.type == "compacted" for e in events)
    row = await session.load_context_checkpoint()
    assert row is not None, "auto compaction must leave a reusable checkpoint"
    assert row["covered_count"] > 0
    assert row["messages_json"]
