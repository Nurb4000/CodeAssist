"""Tests for Agent class."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeassist.agent import (
    CONFIRM_TOOLS,
    CONTINUATION_NUDGE,
    MAX_CONTINUATION_NUDGES,
    SESSION_TRUST,
    Agent,
    _loop_event,
    _LoopDetector,
    _restates,
)
from codeassist.llm import (
    ContextWindowExceeded,
    Finish,
    LLMClient,
    ModerationBlocked,
    TextDelta,
    ToolCall,
    Usage,
    is_context_length_error,
)
from codeassist.session import Session
from codeassist.tools import ToolRegistry, ToolResult


@pytest.fixture(autouse=True)
def _isolate_session_trust():
    """Session trust is keyed by session_id (process-lifetime); these tests reuse
    the shared id "test-session", so clear the store to keep each test fresh."""
    SESSION_TRUST.clear()
    yield
    SESSION_TRUST.clear()


@pytest.fixture
def mock_config(tmp_path):
    """Create a mock config."""
    config = MagicMock()
    config.workspace = tmp_path
    config.llm.model = "gpt-4o"
    config.llm.context_window = 128000
    config.llm.max_tokens = 4096
    # Keep string-typed LLMConfig fields realistic; the agent passes base_url
    # through capabilities.is_external_backend, which expects a str.
    config.llm.base_url = ""
    config.agent.max_iterations = 3
    config.tools.tool_output_max_tokens = 4000
    return config


@pytest.fixture
def mock_session():
    """Create a mock session."""
    session = MagicMock(spec=Session)
    session.id = "test-session"
    session.add_message = AsyncMock()
    session.get_messages = AsyncMock(return_value=[])
    return session


@pytest.fixture
def mock_tools():
    """Create a mock tool registry."""
    tools = MagicMock(spec=ToolRegistry)
    tools.schemas = MagicMock(return_value=[])
    tools.execute = AsyncMock(return_value="OK")
    tools.get = MagicMock(return_value=None)
    return tools


@pytest.fixture
def agent(mock_config, mock_session, mock_tools):
    """Create an agent with mocked dependencies."""
    with patch("codeassist.agent.LLMClient") as mock_llm_client:
        mock_llm = MagicMock()
        mock_llm.stream = AsyncMock()
        mock_llm.format_tools = MagicMock(return_value=None)
        mock_llm_client.return_value = mock_llm
        
        agent = Agent(mock_config, mock_session, mock_tools, system_prompt="Test prompt")
        agent.llm = mock_llm
        return agent


class TestAgentInit:
    """Test agent initialization."""

    def test_agent_creation(self, agent):
        """Test creating an agent."""
        assert agent.system_prompt == "Test prompt"
        assert agent.config is not None
        assert agent.session is not None
        assert agent.tools is not None

    def test_agent_default_system_prompt(self, mock_config, mock_session, mock_tools):
        """Test agent with default system prompt."""
        with patch("codeassist.agent.LLMClient") as mock_llm_client:
            mock_llm = MagicMock()
            mock_llm.stream = AsyncMock()
            mock_llm.format_tools = MagicMock(return_value=None)
            mock_llm_client.return_value = mock_llm
            
            agent = Agent(mock_config, mock_session, mock_tools)
            assert agent.system_prompt is not None
            assert len(agent.system_prompt) > 0


class TestAgentCancel:
    """Test agent cancellation."""

    def test_cancel_sets_event(self, agent):
        """Test that cancel sets the cancel event."""
        assert not agent.cancel_event.is_set()
        agent.cancel()
        assert agent.cancel_event.is_set()

    def test_cancel_resolves_pending_confirmations(self, agent):
        """Test that cancel resolves all pending confirmations."""
        # Create some pending confirmations
        event1 = asyncio.Event()
        event2 = asyncio.Event()
        agent._confirm_events["id1"] = event1
        agent._confirm_results["id1"] = None
        agent._confirm_events["id2"] = event2
        agent._confirm_results["id2"] = None
        
        agent.cancel()
        
        # All events should be set
        assert event1.is_set()
        assert event2.is_set()
        
        # All results should be False (denied)
        assert agent._confirm_results["id1"] == False
        assert agent._confirm_results["id2"] == False


class TestAgentTrust:
    """Test agent trust flags."""

    def test_reset_trust(self, agent):
        """Test resetting trust flags."""
        agent.set_trust(trust_workspace=True, trust_shell=True)
        assert agent._trust_workspace_writes == True
        assert agent._trust_shell == True
        
        agent.reset_trust()
        assert agent._trust_workspace_writes == False
        assert agent._trust_shell == False

    def test_set_trust_workspace(self, agent):
        """Test setting workspace trust."""
        agent.set_trust(trust_workspace=True)
        assert agent._trust_workspace_writes == True

    def test_set_trust_shell(self, agent):
        """Test setting shell trust."""
        agent.set_trust(trust_shell=True)
        assert agent._trust_shell == True


class TestAgentConfirmation:
    """Test agent confirmation logic."""

    @pytest.mark.asyncio
    async def test_needs_confirmation_no_tool(self, agent):
        """Test that non-confirm tools don't need confirmation."""
        assert not await agent.needs_confirmation("read", {})

    @pytest.mark.asyncio
    async def test_needs_confirmation_confirm_tools(self, agent):
        """Test that confirm tools need confirmation by default."""
        for tool in CONFIRM_TOOLS:
            assert await agent.needs_confirmation(tool, {})

    @pytest.mark.asyncio
    async def test_needs_confirmation_shell_trusted(self, agent):
        """Test that shell doesn't need confirmation when trusted."""
        agent.set_trust(trust_shell=True)
        assert not await agent.needs_confirmation("shell", {})

    @pytest.mark.asyncio
    async def test_needs_confirmation_write_in_workspace(self, mock_config, agent):
        """Test that write doesn't need confirmation for in-workspace files."""
        agent.set_trust(trust_workspace=True)
        file_path = str(mock_config.workspace / "test.py")
        assert not await agent.needs_confirmation("write", {"file_path": file_path})

    @pytest.mark.asyncio
    async def test_needs_confirmation_write_outside_workspace(self, agent):
        """Test that write still needs confirmation for outside-workspace files."""
        agent.set_trust(trust_workspace=True)
        assert await agent.needs_confirmation("write", {"file_path": "/tmp/test.py"})

    def test_is_in_workspace(self, mock_config, agent):
        """Test _is_in_workspace helper."""
        # Create a file in the workspace
        test_file = mock_config.workspace / "test.py"
        test_file.write_text("print('hello')")
        
        assert agent._is_in_workspace(str(test_file)) == True
        assert agent._is_in_workspace("/tmp/test.py") == False

    def test_resolve_confirm_approve(self, agent):
        """Test approving a confirmation."""
        # Create a pending confirmation
        event = asyncio.Event()
        agent._confirm_events["test_id"] = event
        agent._confirm_results["test_id"] = None
        
        # Approve it
        agent.resolve_confirm("test_id", approved=True)
        
        # Event should be set
        assert event.is_set()
        # Result should be True
        assert agent._confirm_results["test_id"] == True

    def test_resolve_confirm_deny(self, agent):
        """Test denying a confirmation."""
        # Create a pending confirmation
        event = asyncio.Event()
        agent._confirm_events["test_id"] = event
        agent._confirm_results["test_id"] = None
        
        # Deny it
        agent.resolve_confirm("test_id", approved=False)
        
        # Event should be set
        assert event.is_set()
        # Result should be False
        assert agent._confirm_results["test_id"] == False

    def test_resolve_confirm_with_trust(self, agent):
        """Test approving with trust flags."""
        # Create a pending confirmation
        event = asyncio.Event()
        agent._confirm_events["test_id"] = event
        agent._confirm_results["test_id"] = None
        
        # Approve with trust
        agent.resolve_confirm("test_id", approved=True, trust_workspace=True, trust_shell=True)
        
        # Trust flags should be set
        assert agent._trust_workspace_writes == True
        assert agent._trust_shell == True

    @pytest.mark.asyncio
    async def test_wait_for_confirm_approved(self, agent):
        """Test waiting for confirmation that gets approved."""
        # Create a pending confirmation
        event = asyncio.Event()
        agent._confirm_events["test_id"] = event
        agent._confirm_results["test_id"] = None
        
        # Approve in a task
        async def approve_later():
            await asyncio.sleep(0.01)
            agent.resolve_confirm("test_id", approved=True)
        
        asyncio.create_task(approve_later())
        
        # Wait for confirmation
        result = await agent.wait_for_confirm("test_id")
        assert result == True

    @pytest.mark.asyncio
    async def test_wait_for_confirm_denied(self, agent):
        """Test waiting for confirmation that gets denied."""
        # Create a pending confirmation
        event = asyncio.Event()
        agent._confirm_events["test_id"] = event
        agent._confirm_results["test_id"] = None
        
        # Deny in a task
        async def deny_later():
            await asyncio.sleep(0.01)
            agent.resolve_confirm("test_id", approved=False)
        
        asyncio.create_task(deny_later())
        
        # Wait for confirmation
        result = await agent.wait_for_confirm("test_id")
        assert result == False


class TestAgentRun:
    """Test agent run loop."""

    @pytest.mark.asyncio
    async def test_run_saves_user_message(self, agent, mock_session):
        """Test that run saves user message to session."""
        # Mock LLM to return empty finish
        from codeassist.llm import Finish
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        finish_event = Finish("stop", usage=mock_usage)
        
        agent.llm.stream = AsyncMock(return_value=iter([finish_event]))
        
        events = []
        async for event in agent.run("Hello"):
            events.append(event)
        
        # Should have saved user message
        mock_session.add_message.assert_called()
        user_msg_call = mock_session.add_message.call_args_list[0]
        assert user_msg_call[0][0] == "user"
        assert user_msg_call[0][1] == "Hello"

    @pytest.mark.asyncio
    async def test_run_with_cancel(self, agent, mock_session):
        """Test that run respects cancellation."""
        # Create an async generator that yields nothing but can be cancelled
        async def empty_stream(*args, **kwargs):
            agent.cancel_event.set()
            return
            yield  # Make this a generator
        
        agent.llm.stream = MagicMock(return_value=empty_stream())
        
        events = []
        async for event in agent.run("Hello"):
            events.append(event)
            if event.type == "done":
                break

        # Should have done event (cancellation may or may not produce cancelled event)
        types = [e.type for e in events]
        assert "done" in types

    @pytest.mark.asyncio
    async def test_run_emits_and_persists_reasoning(self, agent, mock_session):
        """Reasoning model output is emitted as a 'reasoning' event and
        persisted in session.update_message.reasoning_content (schema v10)."""
        from codeassist.llm import Finish, ReasoningDelta, TextDelta, Usage

        async def fake_stream(messages, openai_tools):
            yield ReasoningDelta("Let me think step by step.")
            yield TextDelta("Here is the answer.")
            yield Finish("stop", usage=Usage(prompt_tokens=10, completion_tokens=5))

        agent.llm.stream = fake_stream

        events = []
        async for event in agent.run("Hello"):
            events.append(event)

        # A 'reasoning' WS event must carry the reasoning text.
        reasoning_events = [e for e in events if e.type == "reasoning"]
        assert len(reasoning_events) == 1
        assert reasoning_events[0].data["content"] == "Let me think step by step."

        # The assistant message must persist reasoning_content separately.
        assert mock_session.update_message.await_count >= 1
        last = mock_session.update_message.call_args_list[-1]
        assert last.kwargs["reasoning_content"] == "Let me think step by step."
        # Content is stored separately (not folded into the answer).
        assert last.kwargs["content"] == "Here is the answer."

    @pytest.mark.asyncio
    async def test_run_pure_reasoning_turn_persists(self, agent, mock_session):
        """A turn with only reasoning_content (no answer text) still persists and
        updates the message rather than dropping it."""
        from codeassist.llm import Finish, ReasoningDelta, Usage

        async def fake_stream(messages, openai_tools):
            yield ReasoningDelta("Pure thinking, no answer.")
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

        agent.llm.stream = fake_stream

        events = []
        async for event in agent.run("Hello"):
            events.append(event)

        assert any(e.type == "reasoning" for e in events)
        last = mock_session.update_message.call_args_list[-1]
        assert last.kwargs["reasoning_content"] == "Pure thinking, no answer."
        # content=None means the answer column is left untouched/empty.
        assert last.kwargs["content"] is None


class TestAgentContinuationNudge:
    """The loop must not mark a turn complete when the model stops after using
    tools but before the task is actually done. It should nudge the model to
    continue, then stop once the model gives a final answer or the nudge budget
    is exhausted. The nudge fires even after productive tools have run (progress
    does not imply the task is complete)."""

    @pytest.mark.asyncio
    async def test_nudges_after_progress_now_fires(self, agent, mock_session):
        """A premature stop after productive tools used to be treated as done
        because the nudge was gated on no progress. It must now be nudged to
        continue, and the run completes once the model finishes."""
        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "implement X"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=[{"role": "user", "content": "implement X"}])
            agent.config.agent.max_iterations = 20
            agent.config.tools.tool_output_max_tokens = 1000000  # avoid MagicMock compare in truncate_tool_result
            agent._trust_all = True  # skip confirm dialogs for the tools used
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            async def turn_work():
                yield ToolCall(id="c1", name="shell", arguments={"command": "pytest"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_premature():
                yield TextDelta("All tests pass. Now let me probe edge cases.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_probe():
                yield ToolCall(id="c2", name="shell", arguments={"command": "python repro.py"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_final():
                yield TextDelta("Found 3 FORTH semantics bugs.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_confirm():
                yield TextDelta("Task complete.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            turns = [turn_work(), turn_premature(), turn_probe(), turn_final(), turn_confirm()]
            calls = {"n": 0}

            async def fake_stream(messages, openai_tools):
                g = turns[calls["n"]]
                calls["n"] += 1
                async for ev in g:
                    yield ev

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("implement X"):
                events.append(event)

            # work -> premature(nudge#1) -> probe -> final(nudge#2) -> confirm(done)
            assert calls["n"] == 5
            assert agent._continuation_nudges == 2
            assert agent._run_used_tools is True
            types = [e.type for e in events]
            assert "done" in types
            assert "incomplete" not in types
            assert "error" not in types

    @pytest.mark.asyncio
    async def test_stops_after_nudge_budget_exhausted(self, agent, mock_session):
        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "implement X"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=[{"role": "user", "content": "implement X"}])
            agent.config.agent.max_iterations = 20
            agent.config.tools.tool_output_max_tokens = 1000000
            agent._trust_all = True  # skip confirm dialogs for grep
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            def make_research(i):
                async def gen():
                    # A different pattern each time. Repeating one call with the
                    # same arguments is what the loop guard is for, and it would
                    # stop the run long before the nudge budget is spent --
                    # which is a different test, covered in TestAgentLoopGuard.
                    yield ToolCall(id=f"c{i}", name="grep", arguments={"pattern": f"foo{i}"})
                    yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
                return gen()

            summaries = [
                "Grep found three callers; next I am tracing the first one.",
                "The trace ends in the parser; I am checking its error paths.",
                "Two of the three callers swallow the exception silently.",
                "Now I am looking at what the tests already cover.",
                "The existing suite misses the malformed-input case.",
                "I have a reproduction; confirming it against the last caller.",
            ]

            def make_summary(i):
                async def gen():
                    # A materially different answer each time. The loop guard
                    # compares substance rather than wording, so answers that
                    # differ only in a number still read as one answer repeated
                    # -- which is the case it is meant to catch, and a different
                    # test. See TestAgentLoopGuard.
                    yield TextDelta(summaries[i % len(summaries)])
                    yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
                return gen()

            # Alternate research/summary repeatedly. Each summary is preceded by
            # a research turn (tools used), so the model keeps getting pushed
            # until the nudge budget (MAX_CONTINUATION_NUDGES) is spent, at which
            # point the run is flagged incomplete rather than "done".
            turns = []
            for i in range(MAX_CONTINUATION_NUDGES + 1):
                turns.append(make_research(i))
                turns.append(make_summary(i))
            calls = {"n": 0}

            async def fake_stream(messages, openai_tools):
                g = turns[calls["n"]]
                calls["n"] += 1
                async for ev in g:
                    yield ev

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("implement X"):
                events.append(event)

            # research/summary x6 = 12 stream calls; the last summary exhausts the
            # 5-nudge budget and emits "incomplete" instead of finishing.
            assert calls["n"] == 12
            assert agent._continuation_nudges == MAX_CONTINUATION_NUDGES
            assert agent._run_used_tools is True
            types = [e.type for e in events]
            assert "incomplete" in types
            assert "done" not in types

    @pytest.mark.asyncio
    async def test_accepts_plain_answer_after_first_nudge(self, agent, mock_session):
        """A model that gives a final answer (no further tools) after the first,
        gentle nudge is respected — the loop finishes normally rather than being
        forced to keep working. This is what lets a legitimate research-only
        question end."""
        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "implement X"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=[{"role": "user", "content": "implement X"}])
            agent.config.agent.max_iterations = 20
            agent.config.tools.tool_output_max_tokens = 1000000
            agent._trust_all = True
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            async def turn_research():
                yield ToolCall(id="c1", name="read", arguments={"path": "/tmp/x"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_summary():
                yield TextDelta("That's all I found.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_answer():
                yield TextDelta("The file contains a TODO list.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            turns = [turn_research(), turn_summary(), turn_answer()]
            calls = {"n": 0}

            async def fake_stream(messages, openai_tools):
                g = turns[calls["n"]]
                calls["n"] += 1
                async for ev in g:
                    yield ev

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("implement X"):
                events.append(event)

            # research -> summary(nudge#1, escape hatch) -> answer(final, done).
            assert calls["n"] == 3
            assert agent._continuation_nudges == 1
            assert agent._since_nudge_tools is False
            types = [e.type for e in events]
            assert "done" in types
            assert "incomplete" not in types
            assert "incomplete" not in types

    @pytest.mark.asyncio
    async def test_nudge_never_enters_the_session_snapshot(self, agent, mock_session):
        """A nudge is queued for the next step, never written to the session.

        The ordering it has to produce -- the answer ahead of the nudge, in one
        valid transcript -- is covered end to end in test_nudge_replay.py, which
        drives the real `build_openai_messages` over real session rows. This
        test stubs that function out, so all it can honestly pin is where the
        nudge is allowed to live.
        """
        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            history = [{"role": "user", "content": "list the folders"}]
            mock_build.return_value = [{"role": "user", "content": "list the folders"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=history)
            agent.config.agent.max_iterations = 20
            agent.config.tools.tool_output_max_tokens = 1000000
            agent._trust_all = True
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="axolotl/\nZoe/", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            answer = "Here are the folders:\naxolotl/\nZoe/\nWant me to explore any?"

            async def turn_listing():
                yield ToolCall(id="c1", name="read", arguments={"path": "/tmp/x"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_answer():
                yield TextDelta(answer)
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_done():
                yield TextDelta("Happy to dig into any of them.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            turns = [turn_listing(), turn_answer(), turn_done()]
            calls = {"n": 0}
            seen: list[list[dict]] = []

            async def fake_stream(messages, openai_tools):
                seen.append([dict(m) for m in messages])
                g = turns[calls["n"]]
                calls["n"] += 1
                async for ev in g:
                    yield ev

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("list the folders"):
                events.append(event)

            # The nudge reaches the model as the last turn of the next step.
            nudged = seen[2]
            assert nudged[-1]["role"] == "user"
            assert CONTINUATION_NUDGE in nudged[-1]["content"]

            # It is queued, not persisted. Writing it into the snapshot left it
            # holding rows the database did not have while `_messages_dirty`
            # claimed it did not; a turn that ended that way made the next turn
            # replay the stale nudge and drop the user's question.
            assert not any(
                "Task check" in (m.get("content") or "") for m in history
            ), "the transient nudge leaked into the session snapshot"
            assert len(agent._pending_nudges) == 1
            assert [e.type for e in events].count("done") == 1


class TestAgentStepLimit:
    """When an agent reaches its per-agent step budget, tools are disabled and
    the model is asked for a structured wrap-up (what was done, what remains,
    what to do next) instead of stopping abruptly or declaring a false 'done'.
    This mirrors opencode's per-agent 'steps' limit."""

    @pytest.mark.asyncio
    async def test_last_step_disables_tools_and_injects_wrapup(self, agent, mock_session):
        """On the final allowed step the loop passes tools=None and appends the
        maximum-steps wrap-up prompt; the model's summary becomes the final
        output and the run ends with 'done'."""
        agent.max_steps = 2  # wrap up on the 2nd turn
        agent.config.agent.max_iterations = 10

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "implement X"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=[{"role": "user", "content": "implement X"}])
            agent.config.tools.tool_output_max_tokens = 1000000
            agent._trust_all = True
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            async def turn_work():
                yield ToolCall(id="c1", name="shell", arguments={"command": "pytest"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_wrapup():
                yield TextDelta("Summary: 2 of 3 tasks done. Remaining: probe edge cases.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            turns = [turn_work(), turn_wrapup()]
            calls = {"n": 0}
            seen = {}

            async def fake_stream(messages, openai_tools):
                g = turns[calls["n"]]
                seen["openai_tools"] = openai_tools
                users = [m for m in messages if m.get("role") == "user"]
                seen["last_user"] = users[-1]["content"] if users else None
                calls["n"] += 1
                async for ev in g:
                    yield ev

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("implement X"):
                events.append(event)

            assert calls["n"] == 2
            # Tools disabled on the wrap-up turn and the prompt injected.
            assert seen["openai_tools"] is None
            assert "MAXIMUM STEPS REACHED" in (seen["last_user"] or "")
            types = [e.type for e in events]
            assert "done" in types
            assert "error" not in types
            assert "incomplete" not in types

    @pytest.mark.asyncio
    async def test_steps_fall_back_to_max_iterations_when_unset(self, agent, mock_session):
        """With no per-agent step budget the wrap-up fires on the last global
        iteration, still disabling tools."""
        agent.max_steps = None
        agent.config.agent.max_iterations = 2

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "implement X"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=[{"role": "user", "content": "implement X"}])
            agent.config.tools.tool_output_max_tokens = 1000000
            agent._trust_all = True
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            async def turn_work():
                yield ToolCall(id="c1", name="shell", arguments={"command": "pytest"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_wrapup():
                yield TextDelta("Final summary after hitting the cap.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            turns = [turn_work(), turn_wrapup()]
            calls = {"n": 0}
            last_tools = {}

            async def fake_stream(messages, openai_tools):
                g = turns[calls["n"]]
                last_tools["v"] = openai_tools
                calls["n"] += 1
                async for ev in g:
                    yield ev

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("implement X"):
                events.append(event)

            assert calls["n"] == 2
            assert last_tools["v"] is None
            types = [e.type for e in events]
            assert "done" in types
            assert "error" not in types

    @pytest.mark.asyncio
    async def test_steps_take_precedence_over_max_iterations(self, agent, mock_session):
        """A per-agent step budget binds even when it exceeds the global
        `agent.max_iterations` fallback. Raising the admin "Step budget" far
        above the global cap must actually extend the run (it used to be
        silently clamped by min(steps, max_iterations))."""
        agent.max_steps = 2
        agent.config.agent.max_iterations = 1

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "implement X"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=[{"role": "user", "content": "implement X"}])
            agent.config.tools.tool_output_max_tokens = 1000000
            agent._trust_all = True
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            async def turn_work():
                yield ToolCall(id="c1", name="shell", arguments={"command": "echo"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_wrapup():
                yield TextDelta("Summary on the final step.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            turns = [turn_work(), turn_wrapup()]
            calls = {"n": 0}
            tool_states = []

            async def fake_stream(messages, openai_tools):
                tool_states.append(openai_tools)
                g = turns[calls["n"]]
                calls["n"] += 1
                async for ev in g:
                    yield ev

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("implement X"):
                events.append(event)

            # The per-agent budget (2) ran, NOT the global cap (1).
            assert calls["n"] == 2
            assert tool_states[-1] is None
            types = [e.type for e in events]
            assert "done" in types

    @pytest.mark.asyncio
    async def test_stop_wraps_up_at_binding_step_budget(self, agent, mock_session):
        """A model that never stops working is wrapped up at the per-agent step
        budget, even when that budget is far above the global max_iterations
        fallback. Previously min(steps, max_iterations) cut these runs short at
        the global cap, which is why raising the admin "Step budget" had no
        visible effect once it passed the cap."""
        agent.max_steps = 7
        agent.config.agent.max_iterations = 3

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "implement X"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=[{"role": "user", "content": "implement X"}])
            agent.config.tools.tool_output_max_tokens = 1000000
            agent._trust_all = True
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            def turn_work(i):
                async def gen():
                    # Distinct command each time: repeating one call with the same
                    # arguments is a loop, and the loop guard would end the run
                    # before the step budget was reached.
                    yield ToolCall(
                        id=f"c{i}", name="shell",
                        arguments={"command": f"pytest tests/test_step_{i}.py"},
                    )
                    yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
                return gen()

            async def turn_wrapup():
                yield TextDelta("Reached the step budget; here is what remains.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            # 6 working turns then the forced wrap-up on the 7th (last) step.
            # Note: one turn_work() per slot — list-mult would share a single
            # generator object and silently consume it once.
            turns = [turn_work(i) for i in range(6)] + [turn_wrapup()]
            calls = {"n": 0}
            tool_states = []

            async def fake_stream(messages, openai_tools):
                tool_states.append(openai_tools)
                g = turns[calls["n"]]
                calls["n"] += 1
                async for ev in g:
                    yield ev

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("implement X"):
                events.append(event)

            # Exactly 7 turns ran (the per-agent budget), not the 3 global cap.
            assert calls["n"] == 7
            assert len(tool_states) == 7
            assert tool_states[-1] is None  # tools disabled on the wrap-up turn
            types = [e.type for e in events]
            assert "done" in types
            assert "error" not in types

    @pytest.mark.asyncio
    async def test_stops_at_step_budget_when_model_keeps_working(self, agent, mock_session):
        """A model that keeps calling tools every turn (wandering) must still
        terminate at its per-agent step budget, not run out to the global
        max_iterations cap. This is what makes 'never finishes / high GPU' stop."""
        agent.max_steps = 3  # build-style budget; global cap is much higher
        agent.config.agent.max_iterations = 100

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "implement X"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=[{"role": "user", "content": "implement X"}])
            agent.config.tools.tool_output_max_tokens = 1000000
            agent._trust_all = True
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            async def turn_work():
                yield ToolCall(id="c1", name="shell", arguments={"command": "echo"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_wrapup():
                yield TextDelta("Reached the step budget; here is what remains.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            # Two working turns then the forced wrap-up on the 3rd (last) step.
            turns = [turn_work(), turn_work(), turn_wrapup()]
            calls = {"n": 0}
            tool_states = []

            async def fake_stream(messages, openai_tools):
                tool_states.append(openai_tools)
                g = turns[calls["n"]]
                calls["n"] += 1
                async for ev in g:
                    yield ev

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("implement X"):
                events.append(event)

            # Exactly 3 turns ran (the budget), not the 100 global cap. The final
            # turn had tools disabled so the model could only summarise.
            assert calls["n"] == 3
            assert len(tool_states) == 3
            assert tool_states[-1] is None
            types = [e.type for e in events]
            assert "done" in types
            assert "error" not in types


class TestAgentRefusal:
    """A provider safety block stops the turn and explains itself."""

    @pytest.mark.asyncio
    async def test_moderation_blocked_yields_refusal(self, agent, mock_session):
        """A moderation 400 becomes a `refusal` event, not a raw API error."""
        agent.llm.stream = MagicMock(
            side_effect=ModerationBlocked(
                "data_inspection_failed",
                "The provider's content-moderation layer flagged this request as "
                "potentially inappropriate content.",
                status_code=400,
            )
        )

        events = []
        async for event in agent.run("write a keylogger"):
            events.append(event)

        types = [e.type for e in events]
        assert "refusal" in types
        # The turn must close out cleanly rather than hanging open.
        assert types[-1] == "done"

        refusal = next(e for e in events if e.type == "refusal")
        assert refusal.data["code"] == "data_inspection_failed"
        assert "moderation" in refusal.data["explanation"].lower()
        # The user must be able to see what tripped the filter.
        assert refusal.data["trigger"] == "write a keylogger"
        assert refusal.data["suggestions"]

    @pytest.mark.asyncio
    async def test_refusal_is_not_reported_as_error(self, agent):
        """Refusals are a policy decision, not a malfunction — keep them distinct."""
        agent.llm.stream = MagicMock(
            side_effect=ModerationBlocked("custom_role_blocked", "blocked by policy")
        )

        events = []
        async for event in agent.run("do the thing"):
            events.append(event)

        assert [e.type for e in events].count("error") == 0

    @pytest.mark.asyncio
    async def test_refusal_trigger_is_truncated(self, agent):
        """A very long prompt must not be echoed wholesale into the UI."""
        long_prompt = "x" * 5000
        agent.llm.stream = MagicMock(
            side_effect=ModerationBlocked("data_inspection_failed", "blocked")
        )

        events = []
        async for event in agent.run(long_prompt):
            events.append(event)

        refusal = next(e for e in events if e.type == "refusal")
        assert len(refusal.data["trigger"]) <= 280

    @pytest.mark.asyncio
    async def test_content_filter_finish_reason(self, agent):
        """An empty turn with a filter finish_reason is a refusal."""
        async def fake_stream(messages, openai_tools):
            yield Finish("content_filter", usage=Usage())

        agent.llm.stream = fake_stream

        events = []
        async for event in agent.run("something"):
            events.append(event)

        types = [e.type for e in events]
        assert "refusal" in types
        assert types[-1] == "done"
        refusal = next(e for e in events if e.type == "refusal")
        assert refusal.data["code"] == "content_filter"
        assert refusal.data["trigger"] == "something"

    @pytest.mark.asyncio
    async def test_filter_finish_reason_with_text_is_not_a_refusal(self, agent):
        """Partial output that was then filtered is a result, not a refusal."""
        async def fake_stream(messages, openai_tools):
            yield TextDelta("Here is the safe part.")
            yield Finish("content_filter", usage=Usage())

        agent.llm.stream = fake_stream

        events = []
        async for event in agent.run("something"):
            events.append(event)

        assert "refusal" not in [e.type for e in events]

    @pytest.mark.asyncio
    async def test_normal_finish_is_not_a_refusal(self, agent):
        """An ordinary turn must not trip the refusal path."""
        async def fake_stream(messages, openai_tools):
            yield TextDelta("All good.")
            yield Finish("stop", usage=Usage())

        agent.llm.stream = fake_stream

        events = []
        async for event in agent.run("hello"):
            events.append(event)

        assert "refusal" not in [e.type for e in events]


class TestAgentRulesetEnforcement:
    """The agent's own permission map must be enforced by the loop.

    Regression: ``Agent`` was constructed without ``agent_ruleset`` from both
    ``server.py`` and ``subagent.py``, so every per-agent deny/allow list was
    inert and the read-only review agent could write code.
    """

    @staticmethod
    def _review_agent(mock_config, mock_session, mock_tools):
        from codeassist.agents import AgentPermissions, Permission

        with patch("codeassist.agent.LLMClient") as mock_llm_client:
            mock_llm = MagicMock()
            mock_llm.stream = AsyncMock()
            mock_llm.format_tools = MagicMock(return_value=None)
            mock_llm_client.return_value = mock_llm
            agent = Agent(
                mock_config,
                mock_session,
                mock_tools,
                system_prompt="review",
                agent_ruleset=AgentPermissions([
                    Permission("read", "allow"),
                    Permission("edit", "deny"),
                    Permission("write", "deny"),
                ]).to_ruleset(),
            )
            agent.llm = mock_llm
            return agent

    @pytest.mark.asyncio
    async def test_deny_without_trust_all(self, mock_config, mock_session, mock_tools):
        agent = self._review_agent(mock_config, mock_session, mock_tools)
        assert await agent.get_permission_action("edit", {"file_path": "a.py"}) == "deny"
        assert await agent.get_permission_action("write", {"file_path": "a.py"}) == "deny"

    @pytest.mark.asyncio
    async def test_deny_survives_trust_all(self, mock_config, mock_session, mock_tools):
        """"Trust all tools" is a session-wide convenience; it must not turn a
        read-only agent into one that can write."""
        agent = self._review_agent(mock_config, mock_session, mock_tools)
        agent._trust_all = True
        assert await agent.get_permission_action("edit", {"file_path": "a.py"}) == "deny"
        assert await agent.get_permission_action("write", {"file_path": "a.py"}) == "deny"

    @pytest.mark.asyncio
    async def test_trust_all_still_allows_otherwise(self, mock_config, mock_session, mock_tools):
        """The fix must not break the feature for non-restricted tools."""
        agent = self._review_agent(mock_config, mock_session, mock_tools)
        agent._trust_all = True
        assert await agent.get_permission_action("read", {"file_path": "a.py"}) == "allow"

    @pytest.mark.asyncio
    async def test_no_ruleset_preserves_trust_all(self, agent):
        """An agent constructed with no ruleset (default callers) is unaffected."""
        agent._trust_all = True
        assert await agent.get_permission_action("shell", {}) == "allow"


class TestBuiltinAgentPermissions:
    """Read-only agents must deny every workspace-mutating tool by name.

    An unlisted tool falls through to the "ask" default, so anything omitted
    from these maps was reachable behind a single confirmation click.
    """

    DENY_ALL = (
        "write", "edit", "apply_patch", "shell", "git", "git_snapshot",
        "process", "package_manager", "create_skill", "create_tool",
        "docker", "database",
    )
    # review reviews a change set with `git diff`, but git can also checkout /
    # reset / clean, so it prompts instead of hard-denying.
    REVIEW_GIT = "ask"

    @staticmethod
    def _ruleset(key):
        import asyncio
        import sqlite3

        from codeassist.agents import AgentManager

        mgr = AgentManager()

        async def run():
            try:
                await asyncio.wait_for(mgr.initialize(), timeout=10)
            except (sqlite3.Error, OSError):
                # Built-in agents are seeded in code, so a missing/unmigrated
                # test database does not affect the permission maps under test.
                pass
            return mgr._agents[key].permissions.to_ruleset()

        return asyncio.run(run())

    @staticmethod
    async def _check(tool, ruleset):
        from codeassist.permissions import PermissionManager

        return await PermissionManager().check_permission(tool, "", ruleset)

    @pytest.mark.parametrize("key", ["review", "research", "explore"])
    def test_mutating_tools_denied(self, key):
        import asyncio

        ruleset = self._ruleset(key)
        for tool in self.DENY_ALL:
            expected = self.REVIEW_GIT if (key == "review" and tool == "git") else "deny"
            action = asyncio.run(self._check(tool, ruleset))
            assert action == expected, f"{key}: {tool} -> {action}, expected {expected}"

    @pytest.mark.parametrize("key", ["review", "research", "explore"])
    def test_no_mutating_tool_is_silently_allowed(self, key):
        """The blunt safety net: nothing mutating may be allowed outright."""
        ruleset = self._ruleset(key)
        for tool in self.DENY_ALL:
            assert ruleset.check(tool) != "allow", f"{key} must not allow {tool}"

    @pytest.mark.parametrize("key", ["review", "research", "explore"])
    def test_reading_still_allowed(self, key):
        ruleset = self._ruleset(key)
        for tool in ["read", "grep", "glob", "webfetch"]:
            assert ruleset.check(tool) == "allow", f"{key} should still allow {tool}"


class TestAgentLoopGuard:
    """A model going in circles has to be caught inside the turn, not at the
    budget.

    The guard that was here compared normalized strings for exact equality and
    only ever looked at steps that produced prose. Both holes matched real
    reports. A reasoning-heavy turn that keeps asking for the same thing never
    repeats itself in prose, so nothing was sampled and the turn spent its whole
    step budget re-reading the same file. And the repeats that did reach the
    guard were paraphrases -- "Here are the folders:" / "The folders in this
    workspace are:" -- so equality never fired either. `_restates` already
    existed for exactly that comparison, and is what the guard uses now.
    """

    @staticmethod
    def _agent_with(agent, turns):
        agent.config.agent.max_iterations = 30
        agent.config.tools.tool_output_max_tokens = 1000000
        agent._trust_all = True
        agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
        agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)
        calls = {"n": 0}

        async def fake_stream(messages, openai_tools):
            g = turns[min(calls["n"], len(turns) - 1)]
            calls["n"] += 1
            async for ev in g():
                yield ev

        agent.llm.stream = fake_stream
        return calls

    @staticmethod
    def _call(call_id, name, args):
        async def gen():
            yield ToolCall(id=call_id, name=name, arguments=args)
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
        return gen

    @staticmethod
    def _say(chunks):
        async def gen():
            for chunk in chunks:
                yield TextDelta(chunk)
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
        return gen

    @pytest.mark.asyncio
    async def test_repeated_tool_calls_are_caught(self, agent, mock_session):
        """The same call, asked again and again, is a loop."""
        turns = [self._call(f"c{i}", "read", {"path": "/tmp/src/module.py"})
                 for i in range(8)]
        calls = self._agent_with(agent, turns)

        with patch("codeassist.agent.check_context_limit") as ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            ctx.return_value = {"needs_compaction": False, "total_tokens": 10,
                                "usage_pct": 1.0, "severity": "ok"}
            events = await _drain(agent.run("find the bug"))

        assert calls["n"] < 8, f"ran {calls['n']} steps with no detection"
        looped = [e for e in events if e.type == "incomplete" and "Stopped early" in str(e.data)]
        assert looped, f"a repeated call was never flagged; events={[(e.type, e.data) for e in events]}"
        assert "read" in str(looped[0].data)

    @pytest.mark.asyncio
    async def test_a_looped_call_is_not_left_in_the_transcript(self, agent, mock_session):
        """The step is dropped rather than persisted.

        An assistant row carrying tool_calls has to be followed by one tool
        message per call. The guard stops before the calls run, so there is
        nothing to answer and the row must go rather than be written and
        abandoned.
        """
        turns = [self._call(f"c{i}", "read", {"path": "/tmp/src/module.py"})
                 for i in range(8)]
        self._agent_with(agent, turns)

        with patch("codeassist.agent.check_context_limit") as ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            ctx.return_value = {"needs_compaction": False, "total_tokens": 10,
                                "usage_pct": 1.0, "severity": "ok"}
            await _drain(agent.run("find the bug"))

        assert mock_session.delete_message.await_count >= 1, (
            "the looped step was persisted instead of dropped"
        )
        called, answered = set(), set()
        for call in mock_session.add_message.call_args_list:
            if call.args and call.args[0] == "assistant" and call.kwargs.get("tool_calls"):
                called |= {tc["id"] for tc in call.kwargs["tool_calls"]}
            if call.args and call.args[0] == "tool":
                answered.add(call.kwargs.get("tool_call_id"))
        for call in mock_session.update_message.call_args_list:
            if call.kwargs.get("tool_calls"):
                called |= {tc["id"] for tc in call.kwargs["tool_calls"]}
        assert called <= answered, (
            f"tool calls {called - answered} were persisted with no result to "
            "answer them, which makes the next request invalid"
        )

    @pytest.mark.asyncio
    async def test_paraphrased_answers_are_caught(self, agent, mock_session):
        """Three answers that say the same thing in different words.

        Each follows a tool call, so the continuation guard keeps handing the
        model another step instead of ending the turn -- which is exactly how a
        reworded repeat ran all the way to the step budget before.
        """
        answers = [
            "Here are the folders in the workspace:\n\naxolotl/\nZoe/\n\nWant me to look closer?",
            "The folders in this workspace are:\n\naxolotl/\nZoe/\n\nShall I explore any of them?",
            "Listing the workspace folders:\n\naxolotl/\nZoe/\n\nLet me know if you want detail.",
        ]
        turns = []
        for i, answer in enumerate(answers):
            turns.append(self._call(f"c{i}", "glob", {"pattern": f"*/{i}"}))
            turns.append(self._say([answer]))
        turns.append(self._call("cz", "glob", {"pattern": "*/done"}))
        turns.append(self._say(["Something different entirely."]))
        calls = self._agent_with(agent, turns)

        with patch("codeassist.agent.check_context_limit") as ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            ctx.return_value = {"needs_compaction": False, "total_tokens": 10,
                                "usage_pct": 1.0, "severity": "ok"}
            events = await _drain(agent.run("list the folders"))

        looped = [e for e in events if e.type == "incomplete" and "Stopped early" in str(e.data)]
        assert looped, f"three paraphrased answers were not flagged; events={[e.type for e in events]}"
        assert "same answer" in str(looped[0].data)
        assert calls["n"] < 8, f"kept going for {calls['n']} steps after the loop was visible"

    @pytest.mark.asyncio
    async def test_a_genuine_answer_is_not_flagged(self, agent, mock_session):
        """Distinct answers that share some vocabulary are ordinary work.

        A model listing files, then reporting what it found, then reporting a
        fix, re-uses words throughout. Comparing vocabulary alone would swallow
        every one of those, so the guard also requires each answer to be about
        the same size as the one before it.
        """
        turns = []
        for i, answer in enumerate([
            "The workspace has two folders: axolotl and Zoe.",
            "axolotl holds the parser; Zoe holds the fixtures.",
            "Fixed the timeout in test_ci.py and bumped the default to 30.",
            "Also updated the README to match the new flag.",
        ]):
            turns.append(self._call(f"c{i}", "glob", {"pattern": f"*/{i}"}))
            turns.append(self._say([answer]))
        self._agent_with(agent, turns)

        with patch("codeassist.agent.check_context_limit") as ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            ctx.return_value = {"needs_compaction": False, "total_tokens": 10,
                                "usage_pct": 1.0, "severity": "ok"}
            events = await _drain(agent.run("survey the workspace"))

        assert not [e for e in events if e.type == "error"], (
            f"ordinary progress was flagged: {[str(e.data) for e in events if e.type == 'error']}"
        )


class TestContextWindowRecovery:
    """A provider that refuses a request for exceeding the context window must be
    recovered from, not reported.

    The reported symptom was a bare "Unexpected error: APIError: Context size
    has been exceeded" on a turn that visibly *should* have compacted. The
    rejection arrives as a plain openai.APIError, and nothing in the agent
    recognised it, so it fell through to the catch-all handler.
    """

    @pytest.mark.asyncio
    async def test_context_rejection_compacts_then_retries(self, agent, mock_session):
        """One rejection -> forced compaction -> the step is replayed and the
        turn completes normally instead of erroring."""
        agent._trust_all = True

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            # A history with enough tool output that compaction has something to
            # reclaim -- which is the realistic case for an oversize rejection.
            history = [{"role": "user", "content": "keep going"}]
            for i in range(12):
                history += [
                    {"role": "assistant", "content": f"step {i}",
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "grep", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": f"c{i}",
                     "content": "x" * 4000},
                ]
            mock_build.return_value = history
            # Proactive estimate says everything is fine -- which is exactly why
            # the provider rejection has to be handled, not the estimate.
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            mock_session.get_messages = AsyncMock(return_value=history)

            seen = []

            async def fake_stream(messages, openai_tools):
                seen.append([dict(m) for m in messages])
                if len(seen) == 1:
                    raise ContextWindowExceeded("Context size has been exceeded")
                yield TextDelta("Recovered after compacting.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            agent.llm.stream = fake_stream

            events = []
            async for event in agent.run("keep going"):
                events.append(event)

        types = [e.type for e in events]
        assert "error" not in types, f"must not surface an error, got {types}"
        assert len(seen) == 2, "the step must be replayed after compacting"
        assert len(seen[1]) < len(seen[0]), "the retry must carry a smaller history"
        # The user is told what happened rather than left in the dark.
        compacted = [e for e in events if e.type == "compacted"]
        assert compacted, "a compacted event must be emitted"
        assert "retrying" in compacted[0].data["message"].lower()


class TestContextWindowErrorDetection:
    """The rejection is worded differently by every backend, so match on phrases."""

    @pytest.mark.parametrize("message", [
        "Context size has been exceeded",          # llama.cpp
        "the request exceeds the available context size",
        "This model's maximum context length is 8192 tokens",
        "context_length_exceeded",
        "reduce the length of the messages",
        "input is too long",                       # Ollama
        "prompt is too long",
        "requested tokens exceed the model maximum",
        "n_ctx is 4096",
    ])
    def test_recognised(self, message):
        assert is_context_length_error(Exception(message)) is True

    @pytest.mark.parametrize("message", [
        "rate limit reached",
        "the server had an error processing your request",
        "content moderation triggered",
    ])
    def test_not_mistaken_for_other_errors(self, message):
        assert is_context_length_error(Exception(message)) is False

    def test_llm_raises_dedicated_exception_not_a_bare_retry(self):
        """A context rejection must not be retried with the same oversized
        prompt -- that can never succeed -- and must not surface as a raw
        APIError."""
        import openai

        config = MagicMock()
        config.model = "m"
        config.base_url = ""
        config.temperature = 0.0
        config.max_tokens = 100
        config.frequency_penalty = 0.0
        config.presence_penalty = 0.0
        config.timeout = 5

        client = LLMClient(config)
        client.client.chat.completions.create = AsyncMock(
            side_effect=openai.APIError("Context size has been exceeded", request=MagicMock(), body=None)
        )

        async def drain():
            async for _ in client.stream([{"role": "user", "content": "x"}]):
                pass

        with pytest.raises(ContextWindowExceeded):
            asyncio.run(drain())
        # Not retried: one attempt only.
        assert client.client.chat.completions.create.await_count == 1


async def _drain(agen):
    """Consume an AgentEvent stream into a list."""
    return [event async for event in agen]


class TestLoopDetector:
    """The guard itself, where the judgement calls live.

    Everything else about a looping turn is mock scaffolding; what actually has
    to be right is where "the same thing again" stops looking like the same
    thing.
    """

    @staticmethod
    def _read(arguments):
        return [ToolCall(id="c", name="read", arguments=arguments)]

    def test_the_same_call_repeated_is_a_loop(self):
        detector = _LoopDetector()
        for _ in range(4):
            reason = detector.note_tool_calls(self._read({"path": "/tmp/a.py"}))
        assert reason == "called `read` 4 times in a row with nearly the same arguments"

    def test_an_argument_nudged_one_segment_over_still_counts(self):
        """A model spinning its wheels rewords rather than repeating byte for byte.

        Exact comparison misses this, and it is the shape a real loop takes: the
        path creeps, the search term is tweaked, and nothing changes.
        """
        detector = _LoopDetector()
        for index in range(4):
            reason = detector.note_tool_calls(
                self._read({"path": f"/tmp/a{index}.py", "reason": "looking for the bug"})
            )
        assert reason is not None

    def test_different_tools_are_not_a_loop(self):
        detector = _LoopDetector()
        for name in ("read", "grep", "edit", "read"):
            reason = detector.note_tool_calls(
                [ToolCall(id="c", name=name, arguments={"path": "/tmp/a.py"})]
            )
        assert reason is None

    def test_a_genuinely_different_call_breaks_the_streak(self):
        """Working through a list of files is what the tools are for."""
        detector = _LoopDetector()
        for path in ("/tmp/a.py", "/tmp/b.py", "/tmp/c.py", "/tmp/d.py"):
            reason = detector.note_tool_calls(self._read({"path": path}))
        assert reason is None

    def test_different_work_resets_a_streak_in_progress(self):
        detector = _LoopDetector()
        for path in ("/tmp/a.py", "/tmp/a.py", "/tmp/a.py"):
            detector.note_tool_calls(self._read({"path": path}))
        detector.note_tool_calls(self._read({"path": "/tmp/elsewhere.py"}))
        for _ in range(3):
            reason = detector.note_tool_calls(self._read({"path": "/tmp/a.py"}))
        assert reason is None

    def test_an_answer_repeated_is_a_loop(self):
        detector = _LoopDetector()
        answer = "The bug is in the retry loop in client.py, line 42."
        for _ in range(3):
            reason = detector.note_text(answer)
        assert reason == "gave the same answer 3 times in a row"

    def test_an_answer_reworded_every_time_still_counts(self):
        """Framing churns while the substance holds still.

        Each rewording overlaps its immediate predecessor by under half, so
        comparing only against the last answer reads this as three unrelated
        replies rather than one answer three times.
        """
        detector = _LoopDetector()
        for answer in (
            "Here are the folders in the workspace:\n\naxolotl/\nzoe/\n\nWant a look?",
            "The folders in this workspace are:\n\naxolotl/\nzoe/\n\nShall I explore?",
            "Listing the workspace folders:\n\naxolotl/\nzoe/\n\nLet me know.",
        ):
            reason = detector.note_text(answer)
        assert reason == "gave the same answer 3 times in a row"

    def test_a_genuinely_different_answer_is_not_a_loop(self):
        detector = _LoopDetector()
        for answer in (
            "The bug is in the retry loop in client.py.",
            "Let me check how the session cache is invalidated.",
            "Found it: _invalidate runs before the commit, so the cache is stale.",
        ):
            reason = detector.note_text(answer)
        assert reason is None

    def test_a_restatement_the_guard_dropped_still_counts(self):
        """These are the loops that look like progress.

        Each repeat is swallowed, so the user sees their answer once and then
        tool calls that change nothing -- the turn grinds out the nudge budget
        and reports itself incomplete. Only the drops show it was circling.
        """
        detector = _LoopDetector()
        assert detector.note_text("The bug is in client.py, in the retry loop.") is None
        assert detector.note_restatement() is None
        assert detector.note_restatement() == "gave the same answer 3 times in a row"

    def test_blank_text_is_not_a_step(self):
        detector = _LoopDetector()
        assert detector.note_text("") is None
        assert detector.note_text("   ") is None


class TestLoopStopAdvice:
    """The turn has stopped, but the message must not tell the user how to
    make it stop again.

    The nudge-budget `incomplete` ends with "Use Continue to let it keep
    going", which is right there and wrong here: continuing is what produced
    the loop in the first place.
    """

    @staticmethod
    def _stop_message():
        return _loop_event("called `read` 4 times in a row").data["message"]

    def test_it_is_not_an_error(self):
        """Nothing malfunctioned -- the detector worked.

        `error` renders red in the transcript, which reads as a malfunction
        and as the agent having crashed rather than stopped.
        """
        assert _loop_event("gave the same answer 3 times in a row").type == "incomplete"

    def test_it_does_not_advise_pressing_continue(self):
        message = self._stop_message().lower()
        assert "use continue" not in message
        assert "let it keep going" not in message

    def test_it_does_admit_the_task_may_be_unfinished(self):
        """Otherwise a stopped turn reads as a finished one.

        `incomplete` shows no "Complete" badge, but saying so costs nothing
        and keeps the reason for stopping from reading as a verdict.
        """
        assert "may not be complete" in self._stop_message()

    def test_it_names_what_repeated(self):
        """The user has to be able to tell the model apart from the task.

        "The agent kept stopping after using tools" gives them nothing to
        act on; which call, or which answer, does.
        """
        assert "`read`" in self._stop_message()

    def test_it_says_why_continuing_is_unlikely_to_help(self):
        assert "likely" in self._stop_message()

class TestRestates:
    def test_the_same_answer_restates(self):
        assert _restates("the bug is in client.py", "the bug is in client.py")

    def test_an_unrelated_answer_does_not(self):
        assert not _restates("the bug is in client.py", "here is a poem about rain")

    def test_a_growing_answer_is_a_new_answer_not_a_restatement(self):
        """A longer reply that only adds detail is progress, not a repeat.

        Guarding on length alone would call the second half of every real
        answer a loop.
        """
        short = "The bug is in client.py, in the retry loop on line 42."
        long = (
            "The bug is in client.py, in the retry loop on line 42. It retries "
            "without a backoff, so a failing dependency gets hammered. Here is "
            "why the delay matters, and what to change."
        )
        assert not _restates(short, long)

    def test_an_empty_reply_restates_nothing(self):
        assert not _restates("the bug is in client.py", "")

class TestAgentStop:
    """Stopping a turn must end it promptly and must not throw away work that
    already happened.

    Two separate defects fed the "Stop does nothing for a while" report:

      * The cooperative `cancel_event` is only inspected between streamed chunks
        and between tool calls, so a turn waiting on its first token, or one
        sitting inside `asyncio.gather` on a long tool, kept running to
        completion. The server now also cancels the asyncio task.
      * Both the flag path and the hard-cancel path unwound without persisting
        what had already streamed, leaving the placeholder assistant row empty
        -- the text the user watched appear vanished. They also left an
        assistant message carrying `tool_calls` with no matching tool result,
        which makes the provider reject the next request.
    """

    @pytest.mark.asyncio
    async def test_cancel_mid_stream_persists_partial_output(self, agent, mock_session):
        """Stop part-way through a stream keeps the text and reasoning that
        already arrived, and drops what never did."""
        from codeassist.llm import ReasoningDelta, TextDelta

        async def fake_stream(messages, openai_tools):
            yield TextDelta("half an answer")
            yield ReasoningDelta("thinking so far")
            agent.cancel_event.set()  # the user pressed Stop
            yield TextDelta(" never delivered")

        agent.llm.stream = fake_stream

        events = await _drain(agent.run("Hello"))

        last = mock_session.update_message.call_args_list[-1]
        assert last.kwargs["content"] == "half an answer", "partial text is saved"
        assert last.kwargs["reasoning_content"] == "thinking so far", "partial reasoning is saved"
        assert "never delivered" not in str(last.kwargs["content"])
        assert "cancelled" in [e.type for e in events]

    @pytest.mark.asyncio
    async def test_cancel_mid_stream_answers_streamed_tool_calls(self, agent, mock_session):
        """A tool call that streamed before the stop still needs a tool result,
        or the transcript is invalid for the next turn."""
        async def fake_stream(messages, openai_tools):
            yield ToolCall(id="c1", name="shell", arguments={"command": "echo hi"})
            agent.cancel_event.set()
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

        agent.llm.stream = fake_stream

        await _drain(agent.run("Hello"))

        tool_msgs = [c for c in mock_session.add_message.call_args_list
                     if c.args and c.args[0] == "tool"]
        assert tool_msgs, "the unrun tool call must be answered"
        assert tool_msgs[-1].kwargs["tool_call_id"] == "c1"

    @pytest.mark.asyncio
    async def test_cancel_interrupts_a_running_tool_promptly(self, agent, mock_session):
        """A long tool call must not hold the run open. Cancelling the task
        unwinds the pending await instead of waiting the tool out."""
        started = asyncio.Event()
        finished = False

        async def slow_execute(name, args):
            nonlocal finished
            started.set()
            await asyncio.sleep(30)
            finished = True
            return ToolResult(output="never", error=False)

        agent.tools.execute = slow_execute
        agent._trust_all = True
        agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

        async def turn():
            yield ToolCall(id="c1", name="shell", arguments={"command": "sleep 30"})
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

        agent.llm.stream = lambda messages, openai_tools: turn()

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "go"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }

            task = asyncio.create_task(_drain(agent.run("go")))
            await asyncio.wait_for(started.wait(), timeout=5)

            task.cancel()
            # Must settle promptly, not after the tool's own 30s.
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)

        assert not finished, "the interrupted tool must not have run to completion"

    @pytest.mark.asyncio
    async def test_cancel_during_tool_call_answers_the_call(self, agent, mock_session):
        """Interrupting a running tool still owes the transcript a result for
        its tool_call, otherwise the next request to the provider is rejected."""
        started = asyncio.Event()

        async def slow_execute(name, args):
            started.set()
            await asyncio.sleep(30)
            return ToolResult(output="never", error=False)

        agent.tools.execute = slow_execute
        agent._trust_all = True
        agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

        async def turn():
            yield ToolCall(id="c1", name="shell", arguments={"command": "sleep 30"})
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

        agent.llm.stream = lambda messages, openai_tools: turn()

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "go"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }

            task = asyncio.create_task(_drain(agent.run("go")))
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)

        # The shielded write must have completed even though the task was cancelled.
        await asyncio.sleep(0.05)
        tool_msgs = [c for c in mock_session.add_message.call_args_list
                     if c.args and c.args[0] == "tool"]
        assert tool_msgs, "the interrupted tool call must still be answered"
        assert tool_msgs[-1].kwargs["tool_call_id"] == "c1"

    @pytest.mark.asyncio
    async def test_stop_while_confirming_answers_every_call(self, agent, mock_session):
        """Stop pressed during the confirmation loop still owes the transcript a
        result for every tool call it already persisted.

        The calls are written as an assistant row before the user has approved
        any of them, so returning from the loop without answering them leaves
        the next request carrying tool calls with no results and the provider
        rejects it -- the session then fails on every later message.
        """
        agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

        async def turn():
            for index in range(3):
                yield ToolCall(id=f"c{index}", name="shell",
                               arguments={"command": f"echo {index}"})
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

        agent.llm.stream = lambda messages, openai_tools: turn()

        agent.get_permission_action = AsyncMock(return_value="ask")

        waiting = asyncio.Event()
        asked = []

        async def confirm(confirm_id):
            # The user presses Stop with the first dialog still up. The
            # remaining two calls were persisted with the assistant row and
            # never reach a dialog at all, so nothing else will answer them.
            asked.append(confirm_id)
            waiting.set()
            agent.cancel()
            return False

        agent.wait_for_confirm = confirm

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "go"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            task = asyncio.create_task(_drain(agent.run("go")))
            await asyncio.wait_for(waiting.wait(), timeout=5)
            await asyncio.wait_for(task, timeout=5)

        assert asked, "the fixture must reach a confirmation to mean anything"
        self._assert_no_dangling_tool_calls(mock_session)

    @pytest.mark.asyncio
    async def test_hard_cancel_while_confirming_answers_every_call(self, agent, mock_session):
        """The same, but the server cancels the task outright instead of setting
        the cooperative flag.

        `CancelledError` unwinds the confirmation loop at whatever await it
        happened to be sitting in, so it has to be caught there rather than
        relying on the stop flag being set.
        """
        agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)
        agent.get_permission_action = AsyncMock(return_value="ask")

        async def turn():
            for index in range(3):
                yield ToolCall(id=f"c{index}", name="shell",
                               arguments={"command": f"echo {index}"})
            yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

        agent.llm.stream = lambda messages, openai_tools: turn()

        waiting = asyncio.Event()

        async def hang(confirm_id):
            waiting.set()
            await asyncio.sleep(30)

        agent.wait_for_confirm = hang

        with patch("codeassist.agent.build_openai_messages") as mock_build, \
             patch("codeassist.agent.check_context_limit") as mock_ctx, \
             patch("codeassist.agent.effective_context_window", new=AsyncMock(return_value=128000)), \
             patch("codeassist.agent.KnowledgeBase.log_tool_execution", new=AsyncMock()):
            mock_build.return_value = [{"role": "user", "content": "go"}]
            mock_ctx.return_value = {
                "needs_compaction": False, "total_tokens": 10,
                "usage_pct": 1.0, "severity": "ok",
            }
            task = asyncio.create_task(_drain(agent.run("go")))
            await asyncio.wait_for(waiting.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)

        # The shielded write must have completed even though the task was cancelled.
        await asyncio.sleep(0.05)
        self._assert_no_dangling_tool_calls(mock_session)

    @staticmethod
    def _assert_no_dangling_tool_calls(mock_session):
        """Every tool call written to the session must have a result with it."""
        called, answered = set(), set()
        for call in mock_session.add_message.call_args_list:
            if call.args and call.args[0] == "assistant" and call.kwargs.get("tool_calls"):
                called |= {tc["id"] for tc in call.kwargs["tool_calls"]}
            if call.args and call.args[0] == "tool":
                answered.add(call.kwargs.get("tool_call_id"))
        for call in mock_session.update_message.call_args_list:
            if call.kwargs.get("tool_calls"):
                called |= {tc["id"] for tc in call.kwargs["tool_calls"]}
        assert called, "the fixture must persist tool calls for this to mean anything"
        assert not called - answered, (
            f"tool calls {sorted(called - answered)} were persisted with no "
            "result to answer them, so the next request is invalid"
        )

