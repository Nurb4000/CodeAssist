"""Tests for the create_skill tool.

The interesting property is not that a file appears -- it is that the file the
tool writes is one the registry can read back. The tool used to render its own
frontmatter, so it was a second writer with its own bugs: a description
containing a quote came back with literal backslashes, one containing a newline
produced a file that discovery skipped, and a name like "café" passed the tool's
own check and was then silently ignored by the parser.
"""
import pytest

from codeassist.config import SkillsConfig
from codeassist.skills import SkillRegistry


@pytest.fixture
def skill_tool(tmp_path, monkeypatch):
    """A CreateSkill tool pointed at a tmp workspace, with the KB write stubbed.

    The knowledge entry and the embedding are side quests; the file is the
    contract, so the tests below are about that.
    """
    from codeassist.tools import create_skill as module

    async def _fake_entry(**_kwargs):
        return "entry-1"

    monkeypatch.setattr(module.KnowledgeBase, "create_knowledge_entry", _fake_entry)
    monkeypatch.setattr(module, "get_embedding_manager", None, raising=False)

    tool = module.CreateSkill()
    tool.workspace = tmp_path
    return tool


@pytest.fixture
def live_registry(tmp_path, monkeypatch):
    """Stand in for the boot-time registry the server serves skills from."""
    from codeassist import server

    registry = SkillRegistry(
        tmp_path,
        SkillsConfig(directories=["codeassist/skills", "runtime/skills"],
                     include_packaged=False),
    )
    registry.discover()
    monkeypatch.setattr(server, "skill_registry", registry, raising=False)
    return registry


def _read_back(tmp_path, name):
    """Load the skill the way the server does: through a real registry."""
    registry = SkillRegistry(
        tmp_path,
        SkillsConfig(directories=["codeassist/skills", "runtime/skills"],
                     include_packaged=False),
    )
    registry.discover()
    return registry.get_skill(name)


class TestCreateSkillTool:
    @pytest.mark.asyncio
    async def test_written_skill_is_discoverable(self, skill_tool, tmp_path):
        result = await skill_tool.execute(
            name="deploy", description="Ship it", content="1. build\n2. push",
            slash_command="deploy",
        )

        assert not result.error, result.output
        assert (tmp_path / "runtime" / "skills" / "deploy.md").is_file()
        skill = _read_back(tmp_path, "deploy")
        assert skill is not None
        assert skill.description == "Ship it"
        assert skill.slash_command == "deploy"
        assert skill.content == "1. build\n2. push"

    @pytest.mark.asyncio
    async def test_quotes_in_a_description_survive_the_round_trip(self, skill_tool, tmp_path):
        """The writer escapes the quotes; the parser has to undo them."""
        await skill_tool.execute(
            name="quoted", description='He said "go" -- then left', content="body",
        )

        assert _read_back(tmp_path, "quoted").description == 'He said "go" -- then left'

    @pytest.mark.asyncio
    async def test_description_with_a_newline_survives_the_round_trip(
        self, skill_tool, tmp_path
    ):
        """Raw newlines used to break the frontmatter, and the agent was told
        the skill had been created -- with half its description missing."""
        result = await skill_tool.execute(
            name="multiline", description="First line\nSecond line", content="body",
        )

        assert not result.error, result.output
        skill = _read_back(tmp_path, "multiline")
        assert skill is not None
        assert skill.description == "First line\nSecond line"

    @pytest.mark.asyncio
    async def test_a_created_skill_is_usable_without_a_restart(
        self, skill_tool, live_registry, tmp_path
    ):
        """Writing the file is only half the job.

        The serving registry holds its own copy built at startup, so before this
        the agent got "created successfully" for a skill it could not invoke
        until the process restarted.
        """
        assert live_registry.get_skill("fresh") is None

        result = await skill_tool.execute(
            name="fresh", description="d", content="body", slash_command="fresh",
        )

        assert not result.error, result.output
        assert live_registry.get_skill("fresh") is not None
        assert live_registry.get_by_slash_command("fresh") is not None
        assert "next skill reload" not in result.output

    @pytest.mark.asyncio
    async def test_creation_still_succeeds_when_there_is_no_server_to_activate(
        self, skill_tool, tmp_path, monkeypatch
    ):
        from codeassist import server

        monkeypatch.setattr(server, "skill_registry", None, raising=False)
        result = await skill_tool.execute(name="cli", description="d", content="body")

        assert not result.error, result.output
        assert _read_back(tmp_path, "cli") is not None
        assert "next skill reload" in result.output

    @pytest.mark.asyncio
    async def test_a_name_the_registry_would_skip_is_refused(self, skill_tool, tmp_path):
        """`isalnum` accepted this; the parser's pattern does not."""
        result = await skill_tool.execute(name="café", description="d", content="c")

        assert result.error
        assert not (tmp_path / "runtime").exists()

    @pytest.mark.asyncio
    async def test_missing_description_is_refused(self, skill_tool, tmp_path):
        result = await skill_tool.execute(name="no-desc", description="", content="c")

        assert result.error
        assert not (tmp_path / "runtime").exists()

    @pytest.mark.asyncio
    async def test_existing_skill_is_not_clobbered(self, skill_tool, tmp_path):
        await skill_tool.execute(name="mine", description="first", content="body")

        result = await skill_tool.execute(name="mine", description="second", content="body")

        assert result.error
        assert _read_back(tmp_path, "mine").description == "first"