"""Tests for instruction discovery, session sharing, apply patch, skills, plugins, and context sources."""
from pathlib import Path

import pytest

from codeassist.instruction_discovery import (
    InstructionDiscoverer,
    load_file_instructions,
)
from codeassist.session_manager import SessionTool
from codeassist.system_context import (
    DateSource,
    EnvironmentSource,
    InstructionsSource,
    SkillsSource,
    SystemContextManager,
)
from codeassist.tools.apply_patch import is_gpt_model


class TestInstructionDiscovery:
    @pytest.mark.asyncio
    async def test_load_file_instructions_existing(self, tmp_path):
        """Test loading instructions from an existing file."""
        content = "# Project Instructions\nFollow these rules."
        filepath = tmp_path / "AGENTS.md"
        filepath.write_text(content)

        source = await load_file_instructions(filepath)
        assert source.loaded is True
        assert source.content == content
        assert source.error == ""

    @pytest.mark.asyncio
    async def test_load_file_instructions_missing(self, tmp_path):
        """Test loading instructions from a missing file."""
        filepath = tmp_path / "NONEXISTENT.md"
        source = await load_file_instructions(filepath)
        assert source.loaded is False
        assert "not found" in source.error.lower()

    @pytest.mark.asyncio
    async def test_load_file_instructions_too_large(self, tmp_path):
        """Test that files exceeding max size are rejected."""
        filepath = tmp_path / "huge.md"
        filepath.write_text("x" * (65 * 1024))  # 65KB > 64KB limit

        source = await load_file_instructions(filepath, max_size=64 * 1024)
        assert source.loaded is False
        assert "too large" in source.error.lower()

    @pytest.mark.asyncio
    async def test_discover_project_instructions(self, tmp_path):
        """Test discovering AGENTS.md in workspace."""
        # Create nested directory structure with AGENTS.md at root
        (tmp_path / "AGENTS.md").write_text("# Root Instructions")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "CLAUDE.md").write_text("# Nested Instructions")

        discoverer = InstructionDiscoverer()
        sources = await discoverer.discover(workspace=tmp_path)
        # Should find AGENTS.md at workspace root
        assert len(sources) >= 1
        assert any("Root Instructions" in s.content for s in sources)

    @pytest.mark.asyncio
    async def test_discover_with_config_paths(self, tmp_path):
        """Test loading instructions from config-specified paths."""
        custom_file = tmp_path / "custom.md"
        custom_file.write_text("# Custom Instructions")

        discoverer = InstructionDiscoverer()
        sources = await discoverer.discover(
            workspace=tmp_path,
            config_paths=[str(custom_file)],
            disable_project_config=True,
        )
        assert len(sources) == 1
        assert "Custom Instructions" in sources[0].content

    def test_get_combined_content(self):
        """Test combining multiple instruction sources."""
        discoverer = InstructionDiscoverer()
        discoverer.sources = [
            type('Source', (), {'loaded': True, 'filename': 'AGENTS.md', 'content': '# A'})(),
            type('Source', (), {'loaded': True, 'filename': 'CLAUDE.md', 'content': '# B'})(),
            type('Source', (), {'loaded': False, 'filename': 'missing.md', 'content': ''})(),
        ]
        combined = discoverer.get_combined_content()
        assert "# A" in combined
        assert "# B" in combined
        assert "---" in combined

    def test_get_instruction_block(self):
        """Test formatted instruction block for system prompt."""
        discoverer = InstructionDiscoverer()
        discoverer.sources = [
            type('Source', (), {'loaded': True, 'filename': 'AGENTS.md', 'content': '# Rules'})(),
        ]
        block = discoverer.get_instruction_block()
        assert "## Project Instructions" in block
        assert "# Rules" in block


class TestSystemContext:
    @pytest.mark.asyncio
    async def test_build_context_with_sources(self):
        """Test building combined context from multiple sources."""
        mgr = SystemContextManager()
        mgr.register(DateSource())
        mgr.register(EnvironmentSource())

        context = await mgr.build_context(Path("/tmp"))
        assert "Today's date:" in context
        assert "Working directory:" in context

    @pytest.mark.asyncio
    async def test_epoch_increments_on_change(self):
        """Test that epoch increments when context changes."""
        mgr = SystemContextManager()
        source = InstructionsSource()
        mgr.register(source)

        # Initial build (empty)
        await mgr.build_context(Path("/tmp"))
        initial_epoch = mgr.epoch

        # Update instructions
        source.update("# New Instructions")
        await mgr.build_context(Path("/tmp"))
        assert mgr.epoch > initial_epoch

    def test_date_source_refresh(self):
        """Test that DateSource detects date changes."""
        source = DateSource()
        # First call should not need refresh (no prior date set)
        assert source.should_refresh(Path("/tmp")) is True
        # After getting context, last_date is set
        from datetime import date as d
        source._last_date = d.today().isoformat()  # noqa: DTZ011
        assert source.should_refresh(Path("/tmp")) is False
        # Simulate date change
        source._last_date = "2020-01-01"
        assert source.should_refresh(Path("/tmp")) is True

    @pytest.mark.asyncio
    async def test_skills_source_update(self):
        """Test SkillsSource content update."""
        source = SkillsSource()
        source.update("<skills>test</skills>")
        context = await source.get_context(Path("/tmp"))
        assert "test" in context

    @pytest.mark.asyncio
    async def test_check_for_updates(self):
        """Test checking for context updates."""
        mgr = SystemContextManager()
        source = DateSource()
        # Set last_date to today so no refresh needed
        from datetime import date as d
        source._last_date = d.today().isoformat()  # noqa: DTZ011
        mgr.register(source)
        # No updates needed
        result = await mgr.check_for_updates(Path("/tmp"))
        assert result is None

    def test_get_epoch_change_message(self):
        """Test epoch change message formatting."""
        mgr = SystemContextManager()
        mgr._epoch = 5
        msg = mgr.get_epoch_change_message("New context")
        assert "epoch 5" in msg
        assert "New context" in msg


class TestSessionSharing:
    def test_session_tool_has_share_action(self):
        """Test that SessionTool includes 'share' action."""
        tool = SessionTool(current_session_id="test-123")
        schema = tool.schema()
        assert "share" in schema["parameters"]["properties"]["action"]["enum"]


class TestApplyPatchModelDetection:
    def test_gpt_models(self):
        """Test GPT model detection."""
        assert is_gpt_model("gpt-4o") is True
        assert is_gpt_model("gpt-4") is True
        assert is_gpt_model("gpt-3.5-turbo") is True
        assert is_gpt_model("o1-preview") is True
        assert is_gpt_model("o3-mini") is True

    def test_non_gpt_models(self):
        """Test non-GPT model detection."""
        assert is_gpt_model("llama-3-8b") is False
        assert is_gpt_model("codellama-13b") is False
        assert is_gpt_model("local-model") is False
        assert is_gpt_model("") is False
        assert is_gpt_model(None) is False


class TestSkillGuidanceFormat:
    def test_xml_format(self):
        """Test that skill guidance uses XML format."""
        from codeassist.config import SkillsConfig
        from codeassist.skills import SkillRegistry

        registry = SkillRegistry(Path("/tmp"), SkillsConfig())
        # Manually add a skill for testing
        from codeassist.skills import Skill
        skill = Skill(
            name="test-skill",
            description="A test skill",
            content="# Test\nSkill content here.",
            slash_command="test",
            source=Path("/tmp/test.md"),
        )
        registry._skills["test-skill"] = skill

        instructions = registry.get_instructions()
        assert "<available_skills>" in instructions
        assert "<skill>" in instructions
        assert "<name>test-skill</name>" in instructions
        assert "<description>A test skill</description>" in instructions
        assert "<slash_command>/test</slash_command>" in instructions
        assert "</available_skills>" in instructions

    def test_empty_registry(self):
        """Test that empty registry returns empty string."""
        from codeassist.config import SkillsConfig
        from codeassist.skills import SkillRegistry

        registry = SkillRegistry(Path("/tmp"), SkillsConfig())
        assert registry.get_instructions() == ""


class TestPluginHooks:
    @pytest.mark.asyncio
    async def test_fire_hook(self):
        """Test firing lifecycle hooks."""
        from codeassist.config import PluginConfig
        from codeassist.plugins import PluginRegistry

        registry = PluginRegistry(Path("/tmp"), PluginConfig(enabled=True))
        # No plugins loaded, should return empty results
        results = await registry.fire_hook("agent.transform", {})
        assert results == []

    def test_reload_empty(self):
        """Test reloading with no plugins."""
        from codeassist.config import PluginConfig
        from codeassist.plugins import PluginRegistry

        registry = PluginRegistry(Path("/tmp"), PluginConfig(enabled=True))
        # Should not raise
        registry.reload()
