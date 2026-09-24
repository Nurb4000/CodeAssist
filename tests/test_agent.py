"""Tests for Agent class."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeassist.agent import (
    CONFIRM_TOOLS,
    MAX_RESEARCH_NUDGES,
    SESSION_TRUST,
    Agent,
)
from codeassist.llm import Finish, TextDelta, ToolCall, Usage
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


class TestAgentResearchOnlyNudge:
    """The loop must not mark a turn complete when the model stops after only
    research (read/grep/webfetch/...) with no concrete change. It should nudge
    the model to continue, then stop once real progress is made or the nudge
    budget is exhausted."""

    @pytest.mark.asyncio
    async def test_nudges_then_completes_once_progress_made(self, agent, mock_session):
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
            agent._trust_all = True  # skip confirm dialogs for read/documentation
            agent.tools.execute = AsyncMock(return_value=ToolResult(output="ok", error=False))
            agent._tool_output_store.save_if_needed = AsyncMock(return_value=None)

            async def turn_research():
                yield ToolCall(id="c1", name="read", arguments={"path": "/tmp/x"})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_summary():
                yield TextDelta("That's all I found.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_document():
                yield ToolCall(id="c2", name="documentation", arguments={})
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            async def turn_done():
                yield TextDelta("Wrote the docs.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            turns = [
                turn_research(),
                turn_summary(),
                turn_document(),
                turn_done(),
            ]
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

            # research -> summary(nudge#1) -> document(progress) -> done
            assert calls["n"] == 4
            assert agent._research_only_nudges == 1
            assert agent._run_progress_made is True
            assert agent._run_used_tools is True
            types = [e.type for e in events]
            assert "done" in types
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
            agent.config.tools.tool_output_max_tokens = 1000000  # avoid MagicMock compare in truncate_tool_result
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
                    yield TextDelta(f"Still researching — finding {i}.")
                    yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))
                return gen()

            # Alternate research/summary repeatedly. Each summary is preceded by
            # a research turn (tools used), so the model keeps getting pushed
            # until the nudge budget (MAX_RESEARCH_NUDGES) is spent, at which
            # point the run is flagged incomplete rather than "done".
            turns = []
            for i in range(MAX_RESEARCH_NUDGES + 1):
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

            # research/summary x6 = 12 stream calls; 6 research-stops, the last
            # one exhausting the 5-nudge budget and emitting "incomplete".
            assert calls["n"] == 12
            assert agent._research_only_nudges == MAX_RESEARCH_NUDGES
            assert agent._run_progress_made is False
            types = [e.type for e in events]
            assert "incomplete" in types
            assert "done" not in types

    @pytest.mark.asyncio
    async def test_accepts_plain_answer_after_first_nudge(self, agent, mock_session):
        """A model that answers (no further tools) after the first, gentle nudge
        is respected — the loop finishes normally rather than being forced to
        keep working. This is what lets a legitimate research-only question end."""
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

            async def turn_answer():
                yield TextDelta("The file contains a TODO list.")
                yield Finish("stop", usage=Usage(prompt_tokens=1, completion_tokens=1))

            turns = [turn_research(), turn_answer()]
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

            # One gentle nudge, model answers without doing more work -> done.
            assert calls["n"] == 2
            assert agent._research_only_nudges == 1
            assert agent._since_nudge_tools is False
            types = [e.type for e in events]
            assert "done" in types
            assert "incomplete" not in types
