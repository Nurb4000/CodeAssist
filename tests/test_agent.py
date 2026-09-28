"""Tests for Agent class."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeassist.agent import (
    CONFIRM_TOOLS,
    MAX_CONTINUATION_NUDGES,
    SESSION_TRUST,
    Agent,
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
                    yield ToolCall(id=f"c{i}", name="grep", arguments={"pattern": "foo"})
                    yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
                return gen()

            def make_summary(i):
                async def gen():
                    # Distinct text each time so the repetition guard (3 identical
                    # responses) does not fire before the nudge budget is spent.
                    yield TextDelta(f"Still going — check {i}.")
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

            async def turn_work():
                yield ToolCall(id="c1", name="shell", arguments={"command": "echo"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_wrapup():
                yield TextDelta("Reached the step budget; here is what remains.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            # 6 working turns then the forced wrap-up on the 7th (last) step.
            # Note: one turn_work() per slot — list-mult would share a single
            # generator object and silently consume it once.
            turns = [turn_work() for _ in range(6)] + [turn_wrapup()]
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
