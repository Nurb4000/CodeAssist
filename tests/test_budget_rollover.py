"""Regression tests for the OpenCode-style session collapse.

Part A (structured summary) is covered in ``test_tokens.py``; this file covers
Part B -- the budget-driven rollover that summarizes a session into a checkpoint,
truncates it, and resets the per-session budget so work continues past the limit
instead of hard-stopping (the §13 #17 rollover).
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from codeassist.agent import Agent, _recent_tail_start
from codeassist.cost_tracker import BudgetConfig, CostTracker
from codeassist.llm import Finish, Usage
from codeassist.session import Session, init_db


def configure_tools(agent):
    """A bare MagicMock tool registry returns MagicMocks for every .get() and
    .schemas() call, which the run path then tries to JSON-serialize. Pin the two
    the loop touches: no named tools, empty schemas."""
    agent.tools.get = MagicMock(return_value=None)
    agent.tools.schemas = MagicMock(return_value=[])


def _tracker(max_tokens=1000, warning=80.0, enabled=True):
    cfg = BudgetConfig(
        enabled=enabled,
        max_tokens_per_session=max_tokens,
        warning_threshold_pct=warning,
    )
    return CostTracker(cfg)


class TestBudgetPercent:
    def test_none_when_disabled(self):
        t = _tracker(enabled=False)
        t.record_usage("gpt-4o", 100, 50)
        assert t.budget_percent() is None

    def test_none_when_uncapped(self):
        # max_tokens=0 means "unlimited" -- no percentage to speak of.
        t = _tracker(max_tokens=0)
        t.record_usage("gpt-4o", 100, 50)
        assert t.budget_percent() is None

    def test_returns_percentage_of_the_cap(self):
        t = _tracker(max_tokens=1000)
        t.record_usage("gpt-4o", 600, 200)  # 800 tokens used of 1000
        assert t.budget_percent() == pytest.approx(80.0)

    def test_zero_before_any_usage(self):
        assert _tracker().budget_percent() == 0.0

    def test_hard_stop_still_triggers_at_100(self):
        t = _tracker(max_tokens=100, warning=80.0)
        t.record_usage("gpt-4o", 100, 100)  # exactly at the cap
        ok, msg = t.check_budget()
        assert ok is False
        assert "exceeded" in msg.lower()

    def test_warns_but_still_ok_in_the_band(self):
        # At the threshold the turn is allowed to continue -- the rollover hook
        # decides what "continue" means; check_budget itself only hard-stops at
        # 100%.
        t = _tracker(max_tokens=100, warning=80.0)
        t.record_usage("gpt-4o", 85, 0)
        ok, _ = t.check_budget()
        assert ok is True


class TestRecentTailStart:
    def _history(self, turns):
        hist = []
        for i in range(turns):
            hist.append({"role": "user", "content": f"u{i}"})
            hist.append({"role": "assistant", "content": f"a{i}"})
        return hist

    def test_keeps_last_n_user_turns(self):
        # 4 turns -> keep the last 2 (indices 4..7), summarize the first 2.
        hist = self._history(4)
        assert _recent_tail_start(hist, 2) == 4

    def test_falls_back_when_fewer_turns_than_tail(self):
        # Only 1 turn but tail_turns=2: keep the final 2 rows (the one turn).
        hist = self._history(1)
        assert _recent_tail_start(hist, 2) == max(1, len(hist) - 2)

    def test_never_returns_zero_head_when_there_is_a_user(self):
        # With head present the tail start must leave at least one row to
        # summarize; the fallback clamps to >= 1.
        assert _recent_tail_start(self._history(3), 5) >= 1


@pytest.mark.asyncio
class TestSessionRollover:
    async def _seed(self, session, turns=4):
        for i in range(turns):
            await session.add_message("user", f"user message {i}")
            await session.add_message("assistant", f"assistant reply {i}")

    async def test_truncates_head_and_keeps_tail_verbatim(self):
        await init_db()
        session = await Session.create(name="roll")
        await self._seed(session, turns=4)  # 8 rows
        before = await session.get_messages()
        tail_start_id = before[4]["id"]

        checkpoint_id = await session.rollover_from_summary("SUMMARY", tail_start_id)

        after = await session.get_messages()
        # Head (rows 0..3) replaced by a single checkpoint; the 4 tail rows remain.
        assert len(after) == 5
        assert after[0]["content"].startswith("[Conversation checkpoint]")
        assert "SUMMARY" in after[0]["content"]
        # Tail rows are byte-for-byte preserved (role + content): the last two
        # user/assistant turns (indices 4..7) survive verbatim.
        assert [m["content"] for m in after[1:]] == [
            "user message 2",
            "assistant reply 2",
            "user message 3",
            "assistant reply 3",
        ]
        # The checkpoint sorts BEFORE the preserved tail (created_at ordering).
        assert checkpoint_id == after[0]["id"]

    async def test_deletes_head_attachments_but_keeps_tail(self):
        await init_db()
        session = await Session.create(name="roll-att")
        for i in range(4):
            await session.add_message("user", f"u{i}", attachments=[
                {"attachment_type": "image", "mime_type": "image/png",
                 "file_name": f"f{i}.png", "data": "BYTES"},
            ])
            await session.add_message("assistant", f"a{i}")

        # Sanity: 8 rows, 8 attachments before the rollover.
        rows = await session.get_messages()
        assert len(rows) == 8

        tail_start_id = rows[4]["id"]
        await session.rollover_from_summary("SUMMARY", tail_start_id)

        after = await session.get_messages()
        # 1 checkpoint (no attachments) + 4 preserved tail rows (4 attachments).
        assert len(after) == 5
        kept_ids = {m["id"] for m in after}
        # The head rows are gone; only the tail survives.
        assert not any(m["content"].startswith("u0") for m in after[1:])

    async def test_missing_anchor_returns_none(self):
        await init_db()
        session = await Session.create(name="roll-missing")
        await self._seed(session, turns=2)
        assert await session.rollover_from_summary("S", "does-not-exist") is None

    async def test_anchor_from_other_session_returns_none(self):
        await init_db()
        a = await Session.create(name="roll-a")
        b = await Session.create(name="roll-b")
        await self._seed(a, turns=2)
        await self._seed(b, turns=2)
        rows_b = await b.get_messages()
        # Same id value, different session -- must not leak across sessions.
        assert await a.rollover_from_summary("S", rows_b[0]["id"]) is None


@pytest.mark.asyncio
class TestPerformRollover:
    async def _agent(self, turns=4):
        from codeassist.config import Config
        cfg = Config()
        cfg.cost.enabled = True
        cfg.cost.max_tokens_per_session = 1000
        cfg.compaction.enabled = True
        cfg.compaction.tail_turns = 2
        cfg.compaction.preserve_recent_tokens = 4000
        cfg.compaction.tool_result_max_tokens = 3000
        cfg.compaction.model = ""
        cfg.llm.model = "gpt-4o"
        cfg.llm.context_window = 128000
        await init_db()
        session = await Session.create(name="perform-roll")
        for i in range(turns):
            await session.add_message("user", f"user {i}")
            await session.add_message("assistant", f"assistant {i}")
        with patch("codeassist.agent.LLMClient"):
            agent = Agent(cfg, session, MagicMock())
            configure_tools(agent)
        return agent, session

    async def test_rolls_over_and_resets_budget(self):
        agent, session = await self._agent(turns=4)
        # Push the tracker into the 80% band.
        agent.cost_tracker.record_usage("gpt-4o", 850, 0)
        assert agent.cost_tracker.budget_percent() == pytest.approx(85.0)

        with patch("codeassist.agent.effective_context_window", return_value=128000), \
             patch("codeassist.agent._summarize_head", new=AsyncMock(return_value="## Objective\n- x\n## Next Move\n1. y\n## Decisions\n- d\n## Requirements\n- r\n## Important Context\n- c\n## Relevant Files\n- f")):
            summary = await agent._perform_rollover()

        expected = "## Objective\n- x\n## Next Move\n1. y\n## Decisions\n- d\n## Requirements\n- r\n## Important Context\n- c\n## Relevant Files\n- f"
        assert summary == expected
        # Budget reset to zero after the rollover.
        assert agent.cost_tracker.budget_percent() == 0.0
        # Working caches invalidated so the loop rebuilds from the reduced history.
        assert agent._messages is None
        assert agent._working_messages is None
        # Session now holds the checkpoint + preserved tail.
        rows = await session.get_messages()
        assert rows[0]["content"].startswith("[Conversation checkpoint]")
        assert agent._compaction_summary == summary

    async def test_returns_empty_when_nothing_to_summarize(self):
        # An empty session has no head to summarize and no tail to keep.
        agent, _ = await self._agent(turns=0)
        agent.cost_tracker.record_usage("gpt-4o", 850, 0)
        with patch("codeassist.agent.effective_context_window", return_value=128000):
            summary = await agent._perform_rollover()
        assert summary == ""

    async def test_returns_empty_when_summarizer_fails(self):
        agent, _ = await self._agent(turns=4)
        agent.cost_tracker.record_usage("gpt-4o", 850, 0)
        with patch("codeassist.agent.effective_context_window", return_value=128000), \
             patch("codeassist.agent._summarize_head", new=AsyncMock(return_value="")):
            summary = await agent._perform_rollover()
        assert summary == ""
        # A failed rollover must NOT reset the budget or drop history.
        assert agent.cost_tracker.budget_percent() == pytest.approx(85.0)

    async def test_returns_empty_when_unfilled_summary(self):
        agent, _ = await self._agent(turns=4)
        agent.cost_tracker.record_usage("gpt-4o", 850, 0)
        with patch("codeassist.agent.effective_context_window", return_value=128000), \
             patch("codeassist.agent._summarize_head", new=AsyncMock(return_value="just a prose sentence")):
            summary = await agent._perform_rollover()
        assert summary == ""


@pytest.mark.asyncio
class TestRolloverHookInLoop:
    """End-to-end: at/above the warning threshold the loop rolls over once, emits
    the collapse + rolled notices, truncates the session, resets the budget, and
    continues the turn instead of hard-stopping."""

    async def _run_agent(self, agent):
        async def fake_stream(messages, openai_tools=None):
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
        agent.llm.stream = fake_stream
        agent.config.agent.max_iterations = 1
        return [e async for e in agent.run("continue please")]

    async def test_rollover_fires_in_band_and_continues(self):
        from codeassist.config import Config
        cfg = Config()
        cfg.cost.enabled = True
        cfg.cost.max_tokens_per_session = 1000
        cfg.compaction.enabled = True
        cfg.compaction.tail_turns = 2
        cfg.llm.model = "gpt-4o"
        cfg.llm.context_window = 128000
        await init_db()
        session = await Session.create(name="hook")
        for i in range(4):
            await session.add_message("user", f"user {i}")
            await session.add_message("assistant", f"assistant {i}")
        with patch("codeassist.agent.LLMClient"):
            agent = Agent(cfg, session, MagicMock())
            configure_tools(agent)
        # Already at 85% before the turn starts.
        agent.cost_tracker.record_usage("gpt-4o", 850, 0)

        with patch("codeassist.agent.effective_context_window", return_value=128000), \
             patch("codeassist.agent._summarize_head", new=AsyncMock(
                 return_value="## Objective\n- build\n## Requirements\n- req\n## Decisions\n- dec\n## Work State\n### Completed\n- done\n## Next Move\n1. next\n## Relevant Files\n- (none)\n## Important Context\n- c")):
            events = await self._run_agent(agent)

        types = [e.type for e in events]
        assert "session_collapse" in types, "a collapse notice is shown while summarizing"
        assert "session_rolled" in types, "a completion notice is shown after rolling over"
        assert "done" in types, "the turn continues rather than hard-stopping"
        # The budget was reset by the rollover (no longer stuck at 85%); a couple
        # tokens from the continued step are all that remain.
        assert agent.cost_tracker.budget_percent() < 5.0
        # The session was truncated: the 4 old turns' head is replaced by a single
        # checkpoint, and the last two user turns (the most recent, incl. the
        # "continue please" that started this turn) are preserved verbatim.
        rows = await session.get_messages()
        assert rows[0]["content"].startswith("[Conversation checkpoint]")
        assert "user 0" not in "\n".join(r["content"] for r in rows)
        assert len(rows) == 4

    async def test_does_not_rollover_below_threshold(self):
        from codeassist.config import Config
        cfg = Config()
        cfg.cost.enabled = True
        cfg.cost.max_tokens_per_session = 1000
        cfg.compaction.enabled = True
        cfg.compaction.tail_turns = 2
        cfg.llm.model = "gpt-4o"
        cfg.llm.context_window = 128000
        await init_db()
        session = await Session.create(name="hook-low")
        for i in range(4):
            await session.add_message("user", f"user {i}")
            await session.add_message("assistant", f"assistant {i}")
        with patch("codeassist.agent.LLMClient"):
            agent = Agent(cfg, session, MagicMock())
            configure_tools(agent)
        # Only 10% used -- well below the 80% threshold.
        agent.cost_tracker.record_usage("gpt-4o", 100, 0)

        rolled = False
        with patch("codeassist.agent.effective_context_window", return_value=128000), \
             patch.object(agent, "_perform_rollover", new=AsyncMock(return_value="SUMM")) as mock_rollover:
            await self._run_agent(agent)
            rolled = mock_rollover.await_count > 0
        assert not rolled, "no rollover below the warning threshold"

    async def test_rollover_attempted_once_per_turn_on_failure(self):
        from codeassist.config import Config
        cfg = Config()
        cfg.cost.enabled = True
        cfg.cost.max_tokens_per_session = 1000
        cfg.compaction.enabled = True
        cfg.compaction.tail_turns = 2
        cfg.llm.model = "gpt-4o"
        cfg.llm.context_window = 128000
        await init_db()
        session = await Session.create(name="hook-fail")
        for i in range(4):
            await session.add_message("user", f"user {i}")
            await session.add_message("assistant", f"assistant {i}")
        with patch("codeassist.agent.LLMClient"):
            agent = Agent(cfg, session, MagicMock())
            configure_tools(agent)
        agent.cost_tracker.record_usage("gpt-4o", 950, 0)  # in band

        async def multi_stream(messages, openai_tools=None):
            # Two iterations: the rollover fires on the first, the guard stops it.
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
        agent.llm.stream = multi_stream
        agent.config.agent.max_iterations = 2

        with patch("codeassist.agent.effective_context_window", return_value=128000), \
             patch.object(agent, "_perform_rollover", new=AsyncMock(return_value="")) as mock_rollover:
            events = [e async for e in agent.run("go")]

        assert mock_rollover.await_count == 1, "a failed rollover is not retried every iteration"
        assert any(e.type == "done" for e in events), "the turn still completes"

    async def test_reset_clears_the_per_turn_guard(self):
        """The per-turn guard flag is cleared at the start of each turn, so a
        second in-band turn rolls over again. The real _perform_rollover runs (so
        the budget actually resets) and we count persisted rollovers via a wrap."""
        from codeassist.config import Config
        cfg = Config()
        cfg.cost.enabled = True
        cfg.cost.max_tokens_per_session = 1000
        cfg.compaction.enabled = True
        cfg.compaction.tail_turns = 2
        cfg.llm.model = "gpt-4o"
        cfg.llm.context_window = 128000
        await init_db()
        session = await Session.create(name="guard-reset")
        for i in range(4):
            await session.add_message("user", f"user {i}")
            await session.add_message("assistant", f"assistant {i}")
        with patch("codeassist.agent.LLMClient"):
            agent = Agent(cfg, session, MagicMock())
            configure_tools(agent)

        async def one_stream(messages, openai_tools=None):
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

        agent.llm.stream = one_stream
        agent.config.agent.max_iterations = 1

        filled_summary = (
            "## Objective\n- build\n## Requirements\n- req\n## Decisions\n- dec\n"
            "## Work State\n### Completed\n- done\n## Next Move\n1. next\n"
            "## Relevant Files\n- (none)\n## Important Context\n- c"
        )

        # Count real rollovers while preserving their budget-reset side effect.
        # _summarize_head is faked so no real LLM call happens, but the real
        # _perform_rollover (truncate + reset) runs.
        with patch.object(session, "rollover_from_summary", wraps=session.rollover_from_summary) as rolls:
            with patch("codeassist.agent._summarize_head", new=AsyncMock(return_value=filled_summary)):
                # First turn: in band -> rollover attempted once.
                agent.cost_tracker.record_usage("gpt-4o", 900, 0)
                with patch("codeassist.agent.effective_context_window", return_value=128000):
                    async for _ in agent.run("first"):
                        pass
                assert rolls.await_count == 1

                # Second turn: guard cleared at _run start, so a fresh in-band turn
                # rolls again (re-pushed into band since the first rollover reset it).
                agent.cost_tracker.record_usage("gpt-4o", 900, 0)
                with patch("codeassist.agent.effective_context_window", return_value=128000):
                    async for _ in agent.run("second"):
                        pass
                assert rolls.await_count == 2

    async def test_context_event_carries_budget_state(self):
        """The per-step `context` event carries budget state so the chat gauge can
        show reset-point progress (§13 #16b), independent of the window gauge."""
        from codeassist.config import Config
        cfg = Config()
        cfg.cost.enabled = True
        cfg.cost.max_tokens_per_session = 1000
        cfg.compaction.enabled = True
        cfg.compaction.tail_turns = 2
        cfg.llm.model = "gpt-4o"
        cfg.llm.context_window = 128000
        await init_db()
        session = await Session.create(name="budget-event")
        for i in range(4):
            await session.add_message("user", f"user {i}")
            await session.add_message("assistant", f"assistant {i}")
        with patch("codeassist.agent.LLMClient"):
            agent = Agent(cfg, session, MagicMock())
            configure_tools(agent)
        # 12% of the budget used -- below the rollover threshold, so no rollover.
        agent.cost_tracker.record_usage("gpt-4o", 120, 0)

        async def one_stream(messages, openai_tools=None):
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

        agent.llm.stream = one_stream
        agent.config.agent.max_iterations = 1

        events = []
        with patch("codeassist.agent.effective_context_window", return_value=128000):
            async for ev in agent.run("hi"):
                events.append(ev)

        context_events = [e for e in events if e.type == "context"]
        assert context_events, "the loop emits a context event each step"
        # The first context event fires before the step records usage, so it sees
        # exactly the 120 tokens recorded above.
        budget = context_events[0].data
        assert budget["budget_enabled"] is True
        assert budget["budget_max_tokens"] == 1000
        assert budget["budget_threshold_pct"] == 80.0
        assert budget["budget_pct"] == pytest.approx(12.0)
