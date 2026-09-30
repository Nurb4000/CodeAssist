"""Tests for the tool registry's result contract.

The agent reads ``result.output`` on whatever ``ToolRegistry.execute`` returns.
A tool that returned a bare ``str`` instead of a ``ToolResult`` therefore did not
just break itself -- it aborted the whole turn with
``AttributeError: 'str' object has no attribute 'output'``, surfaced to the user
as a bare "Unexpected error". That is what happened when a turn tried to use a
skill.
"""
import pytest

from codeassist.tools import Tool, ToolRegistry, ToolResult


class _StringTool(Tool):
    """A tool written the way some third-party/plugin tools are: returns a str."""

    name = "string_tool"
    description = "Returns a bare string."
    parameters = {"type": "object", "properties": {}}  # noqa: RUF012

    async def execute(self, **kwargs) -> str:
        return "plain string output"


class _NoneTool(Tool):
    name = "none_tool"
    description = "Returns None."
    parameters = {"type": "object", "properties": {}}  # noqa: RUF012

    async def execute(self, **kwargs):
        return None


class _ProperTool(Tool):
    name = "proper_tool"
    description = "Returns a ToolResult."
    parameters = {"type": "object", "properties": {}}  # noqa: RUF012

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(output="proper output")


class _ErroringTool(Tool):
    name = "erroring_tool"
    description = "Raises."
    parameters = {"type": "object", "properties": {}}  # noqa: RUF012

    async def execute(self, **kwargs):
        raise RuntimeError("boom")


@pytest.fixture
def registry(tmp_path):
    return ToolRegistry(tmp_path)


class TestToolResultContract:
    @pytest.mark.asyncio
    async def test_a_tool_returning_a_str_is_coerced(self, registry):
        # The regression: without coercion this value reached the agent as a
        # str and `result.output` raised.
        registry.register(_StringTool())

        result = await registry.execute("string_tool", {})

        assert isinstance(result, ToolResult)
        assert result.output == "plain string output"
        assert result.error is False

    @pytest.mark.asyncio
    async def test_a_coerced_str_is_readable_by_the_agent(self, registry):
        """The agent's exact access pattern, which is what blew up."""
        registry.register(_StringTool())

        result = await registry.execute("string_tool", {})
        truncated = result.output.strip()

        assert truncated == "plain string output"

    @pytest.mark.asyncio
    async def test_a_tool_returning_none_does_not_crash_the_turn(self, registry):
        registry.register(_NoneTool())

        result = await registry.execute("none_tool", {})

        assert isinstance(result, ToolResult)
        assert result.error is True
        assert "NoneType" in result.output

    @pytest.mark.asyncio
    async def test_a_proper_tool_result_passes_through_untouched(self, registry):
        registry.register(_ProperTool())

        result = await registry.execute("proper_tool", {})

        assert result.output == "proper output"
        assert result.error is False

    @pytest.mark.asyncio
    async def test_a_raising_tool_still_reports_an_error_result(self, registry):
        registry.register(_ErroringTool())

        result = await registry.execute("erroring_tool", {})

        assert isinstance(result, ToolResult)
        assert result.error is True
        assert "boom" in result.output

    @pytest.mark.asyncio
    async def test_an_unknown_tool_is_an_error_result(self, registry):
        result = await registry.execute("nope", {})

        assert isinstance(result, ToolResult)
        assert result.error is True

    @pytest.mark.asyncio
    async def test_no_registered_tool_is_annotated_to_return_a_str(self, tmp_path):
        """Static check over the real registry: no tool advertises ``-> str``.

        A bare-str return is what broke a turn, and the annotation is the
        mistake that introduces one -- it is what told a plugin author to return
        a string. This inspects signatures only; calling every tool for real
        would run shell, process and network tools for nothing.
        """
        import logging
        import typing

        from codeassist.tools import create_registry

        registry = create_registry(tmp_path)
        offenders = []
        for name in registry.list_names():
            tool = registry.get(name)
            if tool is None:
                continue
            try:
                hints = typing.get_type_hints(tool.execute)
            except Exception as e:  # noqa: BLE001 - unresolvable annotation
                # Not a contract violation; just not statically checkable.
                logging.getLogger(__name__).debug(
                    "could not resolve annotations for %s: %s", name, e
                )
                continue
            if hints.get("return") is str:
                offenders.append(name)

        assert not offenders, (
            f"tools annotated to return str rather than ToolResult: {offenders}"
        )


class TestSkillToolThroughTheAgent:
    """The reported failure: using a skill aborted the turn.

    A turn asked to use the music skill, got a permission prompt for the `skill`
    tool, and then failed with
    ``AttributeError: 'str' object has no attribute 'output'`` -- surfaced as a
    bare "Unexpected error". `SkillTool.execute` returned a str, the registry
    passed it through, and the agent's `result.output` raised.
    """

    @pytest.mark.asyncio
    async def test_using_a_skill_returns_a_readable_result(self, tmp_path):
        from codeassist.skills import SkillRegistry, SkillTool
        from codeassist.tools import create_registry

        skill_dir = tmp_path / ".skills"
        skill_dir.mkdir()
        (skill_dir / "music.md").write_text(
            "---\nname: music\ndescription: Compose music\nslash: music\n---\n\n"
            "Write lyrics and a prompt for the generator.\n"
        )
        registry = SkillRegistry(tmp_path, type("C", (), {"enabled": True, "directories": [".skills"]})())
        registry.discover()

        tool_registry = create_registry(tmp_path)
        tool_registry.register(SkillTool(registry))

        result = await tool_registry.execute("skill", {"action": "get", "skill_name": "music"})

        # Exactly what the agent does with it.
        assert isinstance(result, ToolResult), "a str here is what crashed the turn"
        assert "Write lyrics and a prompt" in result.output
        assert result.error is False

    @pytest.mark.asyncio
    async def test_a_music_skill_round_trip_needs_no_exceptions(self, tmp_path):
        """The whole failure mode in one assertion: a skill lookup must not raise."""
        from codeassist.skills import SkillRegistry, SkillTool
        from codeassist.tools import create_registry

        skill_dir = tmp_path / ".skills"
        skill_dir.mkdir()
        (skill_dir / "music.md").write_text(
            "---\nname: music\ndescription: Compose music\n---\n\nUse ACE-Step.\n"
        )
        registry = SkillRegistry(tmp_path, type("C", (), {"enabled": True, "directories": [".skills"]})())
        registry.discover()
        tool_registry = create_registry(tmp_path)
        tool_registry.register(SkillTool(registry))

        for action, args in (("list", {}), ("get", {"skill_name": "music"})):
            result = await tool_registry.execute("skill", {"action": action, **args})
            assert not isinstance(result, str)
            assert isinstance(result, ToolResult)
            # The agent's access pattern.
            _ = result.output
