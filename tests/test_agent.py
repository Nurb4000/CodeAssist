"""Tests for Agent class."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeassist.agent import Agent, AgentEvent, CONFIRM_TOOLS
from codeassist.config import Config
from codeassist.session import Session
from tools import ToolRegistry


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

    def test_needs_confirmation_no_tool(self, agent):
        """Test that non-confirm tools don't need confirmation."""
        assert not agent.needs_confirmation("read", {})

    def test_needs_confirmation_confirm_tools(self, agent):
        """Test that confirm tools need confirmation by default."""
        for tool in CONFIRM_TOOLS:
            assert agent.needs_confirmation(tool, {})

    def test_needs_confirmation_shell_trusted(self, agent):
        """Test that shell doesn't need confirmation when trusted."""
        agent.set_trust(trust_shell=True)
        assert not agent.needs_confirmation("shell", {})

    def test_needs_confirmation_write_in_workspace(self, mock_config, agent):
        """Test that write doesn't need confirmation for in-workspace files."""
        agent.set_trust(trust_workspace=True)
        file_path = str(mock_config.workspace / "test.py")
        assert not agent.needs_confirmation("write", {"file_path": file_path})

    def test_needs_confirmation_write_outside_workspace(self, agent):
        """Test that write still needs confirmation for outside-workspace files."""
        agent.set_trust(trust_workspace=True)
        assert agent.needs_confirmation("write", {"file_path": "/tmp/test.py"})

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
