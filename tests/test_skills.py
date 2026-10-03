"""Tests for skills system."""

from pathlib import Path

import pytest

from codeassist.config import SkillsConfig
from codeassist.skills import SkillRegistry, SkillTool, SkillValidationError
from codeassist.tools import ToolResult


class TestSkillRegistry:
    """Test skill discovery and management."""

    @pytest.fixture
    def skill_registry(self, tmp_path):
        """Create a skill registry with test skills."""
        config = type('Config', (), {
            'enabled': True,
            'directories': ['.skills'],
            'include_packaged': False,
        })()
        
        # Create skill directory
        skill_dir = tmp_path / '.skills'
        skill_dir.mkdir()
        
        # Create test skill
        skill_file = skill_dir / 'test-skill.md'
        skill_file.write_text("""---
name: test-skill
description: A test skill for unit tests
slash: test
---

This is the test skill content.
It should be discoverable.
""")
        
        return SkillRegistry(tmp_path, config)

    def test_discover_skills(self, skill_registry):
        """Test discovering skills from directory."""
        skills = skill_registry.discover()
        
        assert len(skills) == 1
        assert skills[0].name == "test-skill"

    def test_get_skill_by_name(self, skill_registry):
        """Test getting a skill by name."""
        skill_registry.discover()
        skill = skill_registry.get_skill("test-skill")
        
        assert skill is not None
        assert skill.name == "test-skill"
        assert "test skill content" in skill.content.lower()

    def test_get_skill_by_slash_command(self, skill_registry):
        """Test getting a skill by slash command."""
        skill_registry.discover()
        skill = skill_registry.get_by_slash_command("test")
        
        assert skill is not None
        assert skill.name == "test-skill"

    def test_list_skills(self, skill_registry):
        """Test listing all skills."""
        skill_registry.discover()
        skills = skill_registry.list_skills()
        
        assert len(skills) == 1
        assert skills[0]["name"] == "test-skill"
        assert skills[0]["description"] == "A test skill for unit tests"

    def test_list_skills_tags_origin_category(self, tmp_path):
        """Each listed skill says whether it is base, custom or packaged.

        The admin page keys the edit/delete row actions off this: only `custom`
        skills are the user's to change, so a base or packaged skill must not be
        reported as if it were one.
        """
        for sub, name, desc in (
            ("codeassist/skills", "base-one", "shipped"),
            ("runtime/skills", "custom-one", "mine"),
        ):
            d = tmp_path / sub
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n---\nbody\n"
            )

        registry = SkillRegistry(tmp_path, SkillsConfig(include_packaged=False))
        registry.discover()

        assert {s["name"]: s["category"] for s in registry.list_skills()} == {
            "base-one": "base",
            "custom-one": "custom",
        }

    def test_packaged_skill_is_not_reported_as_custom(self, tmp_path):
        """A skill loaded from the installed package is neither base nor custom.

        Misreporting it as `custom` would offer the admin page a Delete button
        that can only ever fail.
        """
        registry = SkillRegistry(tmp_path, SkillsConfig(include_packaged=True))
        registry.discover()

        skill = registry.get_skill("code-review")
        assert skill is not None
        assert registry.category_for(skill) == SkillRegistry.CATEGORY_PACKAGED

    def test_get_instructions(self, skill_registry):
        """Test getting skill instructions for system prompt."""
        skill_registry.discover()
        instructions = skill_registry.get_instructions()
        
        assert "test-skill" in instructions
        assert "<slash_command>/test</slash_command>" in instructions

    def test_no_skills_enabled(self, tmp_path):
        """Test behavior when skills are disabled."""
        config = type('Config', (), {
            'enabled': False,
            'directories': ['.skills'],
            'include_packaged': False,
        })()
        
        registry = SkillRegistry(tmp_path, config)
        skills = registry.discover()
        
        assert len(skills) == 0

    def test_empty_skill_directory(self, tmp_path):
        """Test behavior with empty skill directory."""
        config = type('Config', (), {
            'enabled': True,
            'directories': ['.skills'],
            'include_packaged': False,
        })()
        
        skill_dir = tmp_path / '.skills'
        skill_dir.mkdir()
        
        registry = SkillRegistry(tmp_path, config)
        skills = registry.discover()
        
        assert len(skills) == 0

    def test_multiple_skills(self, tmp_path):
        """Test discovering multiple skills."""
        config = type('Config', (), {
            'enabled': True,
            'directories': ['.skills'],
            'include_packaged': False,
        })()
        
        skill_dir = tmp_path / '.skills'
        skill_dir.mkdir()
        
        # Create first skill
        (skill_dir / 'skill1.md').write_text("""---
name: skill-one
description: First skill
slash: s1
---
Content 1
""")
        
        # Create second skill
        (skill_dir / 'skill2.md').write_text("""---
name: skill-two
description: Second skill
slash: s2
---
Content 2
""")
        
        registry = SkillRegistry(tmp_path, config)
        skills = registry.discover()
        
        assert len(skills) == 2


class TestSkillTool:
    """Test skill tool execution."""

    @pytest.fixture
    def skill_tool(self, tmp_path):
        """Create a skill tool with test skills."""
        config = type('Config', (), {
            'enabled': True,
            'directories': ['.skills'],
            'include_packaged': False,
        })()
        
        skill_dir = tmp_path / '.skills'
        skill_dir.mkdir()
        
        (skill_dir / 'test.md').write_text("""---
name: test-skill
description: A test skill
slash: test
---
Test content here.
""")
        
        registry = SkillRegistry(tmp_path, config)
        registry.discover()
        
        return SkillTool(registry)

    @pytest.mark.asyncio
    async def test_skill_tool_list(self, skill_tool):
        """Test listing skills via tool."""
        result = await skill_tool.execute(action="list")
        
        assert isinstance(result, ToolResult)
        assert "test-skill" in result.output
        assert "A test skill" in result.output
        assert result.error is False

    @pytest.mark.asyncio
    async def test_skill_tool_get(self, skill_tool):
        """Test getting skill instructions via tool."""
        result = await skill_tool.execute(action="get", skill_name="test-skill")
        
        assert isinstance(result, ToolResult)
        assert "test-skill" in result.output
        assert "Test content here" in result.output
        assert result.error is False

    @pytest.mark.asyncio
    async def test_skill_tool_get_nonexistent(self, skill_tool):
        """Test getting nonexistent skill."""
        result = await skill_tool.execute(action="get", skill_name="nonexistent")
        
        assert isinstance(result, ToolResult), "the agent reads .output and would crash on a str"
        assert "not found" in result.output.lower()
        assert result.error is True

    @pytest.mark.asyncio
    async def test_skill_tool_invalid_action(self, skill_tool):
        """Test invalid action."""
        result = await skill_tool.execute(action="invalid")
        
        assert isinstance(result, ToolResult)
        assert "Error" in result.output or "unknown action" in result.output.lower()
        assert result.error is True

    def test_skill_tool_schema(self, skill_tool):
        """Test skill tool schema."""
        schema = skill_tool.schema()
        
        assert schema["name"] == "skill"
        assert "description" in schema
        assert "parameters" in schema


class TestSkillValidation:
    """Test skill frontmatter validation."""

    def test_valid_frontmatter(self, tmp_path):
        """Test that valid frontmatter passes validation."""
        from codeassist.skills import validate_skill_frontmatter
        
        frontmatter = {
            "name": "my-skill",
            "description": "A valid skill",
            "slash": "myskill",
        }
        
        # Should not raise
        validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_missing_name(self, tmp_path):
        """Test that missing name is caught."""
        from codeassist.skills import SkillValidationError, validate_skill_frontmatter
        
        frontmatter = {
            "description": "A valid skill",
        }
        
        with pytest.raises(SkillValidationError, match="missing required field 'name'"):
            validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_invalid_name_chars(self, tmp_path):
        """Test that invalid characters in name are caught."""
        from codeassist.skills import SkillValidationError, validate_skill_frontmatter
        
        frontmatter = {
            "name": "my skill!",
            "description": "A valid skill",
        }
        
        with pytest.raises(SkillValidationError, match="alphanumeric characters"):
            validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_missing_description(self, tmp_path):
        """Test that missing description is caught."""
        from codeassist.skills import SkillValidationError, validate_skill_frontmatter
        
        frontmatter = {
            "name": "my-skill",
        }
        
        with pytest.raises(SkillValidationError, match="missing required field 'description'"):
            validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_invalid_slash_command(self, tmp_path):
        """Test that invalid slash command characters are caught."""
        from codeassist.skills import SkillValidationError, validate_skill_frontmatter
        
        frontmatter = {
            "name": "my-skill",
            "description": "A valid skill",
            "slash": "my skill!",
        }
        
        with pytest.raises(SkillValidationError, match="alphanumeric characters"):
            validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_empty_name(self, tmp_path):
        """Test that empty name is caught."""
        from codeassist.skills import SkillValidationError, validate_skill_frontmatter
        
        frontmatter = {
            "name": "",
            "description": "A valid skill",
        }
        
        with pytest.raises(SkillValidationError, match="missing required field 'name'"):
            validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_skill_with_no_slash_command(self, tmp_path):
        """Test that skills without slash command pass validation."""
        from codeassist.skills import validate_skill_frontmatter
        
        frontmatter = {
            "name": "my-skill",
            "description": "A valid skill",
        }
        
        # Should not raise
        validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_discover_skips_invalid_skills(self, tmp_path):
        """Test that invalid skill files are skipped during discovery."""
        from codeassist.skills import SkillRegistry
        
        config = type('Config', (), {
            'enabled': True,
            'directories': ['.skills'],
            'include_packaged': False,
        })()
        
        skill_dir = tmp_path / '.skills'
        skill_dir.mkdir()
        
        # Create a valid skill
        (skill_dir / "valid.md").write_text("""---
name: valid-skill
description: A valid skill
slash: valid
---
Content here.
""")
        
        # Create an invalid skill (missing description)
        (skill_dir / "invalid.md").write_text("""---
name: invalid-skill
slash: invalid
---
Content here.
""")
        
        registry = SkillRegistry(tmp_path, config)
        skills = registry.discover()
        
        assert len(skills) == 1
        assert skills[0].name == "valid-skill"

    def test_discover_skips_duplicate_slash_commands(self, tmp_path, caplog):
        """Test that duplicate slash commands log a warning."""
        from codeassist.skills import SkillRegistry
        
        config = type('Config', (), {
            'enabled': True,
            'directories': ['.skills'],
            'include_packaged': False,
        })()
        
        skill_dir = tmp_path / '.skills'
        skill_dir.mkdir()
        
        # Create two skills with the same slash command
        (skill_dir / "first.md").write_text("""---
name: first-skill
description: First skill
slash: duplicate
---
First content.
""")
        
        (skill_dir / "second.md").write_text("""---
name: second-skill
description: Second skill
slash: duplicate
---
Second content.
""")
        
        registry = SkillRegistry(tmp_path, config)
        skills = registry.discover()
        
        assert len(skills) == 2
        
        # Warning should be logged for duplicate slash command
        assert any("Duplicate slash command" in record.message for record in caplog.records)


class TestPackagedSkills:
    """The skills shipped inside the package must load for any workspace.

    Regression cover for default skills silently disappearing: discovery used to
    be workspace-relative only, so pointing `server.workspace` at an unrelated
    directory (a bind mount, a scratch dir) dropped all 16 defaults.
    """

    @staticmethod
    def _skills_dir() -> Path:
        return SkillRegistry.packaged_skills_dir()

    def test_shipped_skill_files_are_installed(self):
        """Guard the packaging config: the wheel must carry the defaults."""
        skill_files = sorted(self._skills_dir().glob("*.md"))

        assert skill_files, "no packaged skill files found"
        assert len(skill_files) >= 10, f"expected the shipped defaults, found {len(skill_files)}"

    def test_loads_without_a_matching_workspace(self, tmp_path):
        """A workspace with no codeassist/ dir still gets the defaults."""
        config = SkillsConfig(enabled=True, directories=["codeassist/skills", "runtime/skills"])
        assert not (tmp_path / "codeassist").exists()

        skills = SkillRegistry(tmp_path, config).discover()

        assert len(skills) >= 10
        assert {s.name for s in skills} >= {"clean", "code-review", "test"}

    def test_packaged_skills_are_tagged_and_read_only(self, tmp_path):
        """Packaged skills carry a package: source and cannot be edited."""
        config = SkillsConfig(enabled=True, directories=[])

        registry = SkillRegistry(tmp_path, config)
        registry.discover()

        skill = registry.get_skill("code-review")
        assert skill is not None
        assert skill.source.startswith(SkillRegistry.PACKAGE_SOURCE_PREFIX)
        assert registry._resolve_skill_path("code-review") is None
        assert registry.update_skill("code-review", "x", "y") is None
        assert registry.remove_skill("code-review") is None

    def test_workspace_copy_shadows_packaged_skill(self, tmp_path):
        """A workspace skill of the same name overrides the shipped default."""
        skill_dir = tmp_path / "runtime" / "skills"
        skill_dir.mkdir(parents=True)
        (skill_dir / "clean.md").write_text(
            "---\nname: clean\ndescription: My own clean skill\n---\nlocal body\n"
        )

        registry = SkillRegistry(tmp_path, SkillsConfig())
        registry.discover()

        clean = registry.get_skill("clean")
        assert clean.description == "My own clean skill"
        assert clean.source == "runtime/skills/clean.md"

    def test_project_root_workspace_does_not_double_count(self):
        """A workspace that is the checkout scans the defaults exactly once."""
        import codeassist

        project_root = Path(codeassist.__file__).resolve().parent.parent
        if not (project_root / "codeassist" / "skills").is_dir():
            pytest.skip("not running from a source checkout")

        skills = SkillRegistry(project_root, SkillsConfig()).discover()
        names = [s.name for s in skills]

        assert len(names) == len(set(names)), "skills were discovered more than once"
        # Workspace-backed, so the defaults stay editable from a checkout.
        assert all(not s.source.startswith(SkillRegistry.PACKAGE_SOURCE_PREFIX)
                   for s in skills)

    def test_include_packaged_opt_out(self, tmp_path):
        """`include_packaged = false` runs with workspace skills only."""
        config = SkillsConfig(enabled=True, directories=["codeassist/skills"],
                              include_packaged=False)

        assert SkillRegistry(tmp_path, config).discover() == []


class TestCreateSkill:
    """Writing a new skill file into the custom skills directory."""

    @staticmethod
    def _registry(tmp_path):
        return SkillRegistry(
            tmp_path, SkillsConfig(directories=["codeassist/skills", "runtime/skills"],
                                   include_packaged=False)
        )

    def test_create_writes_a_discoverable_custom_skill(self, tmp_path):
        registry = self._registry(tmp_path)
        registry.discover()

        path = registry.create_skill("my-skill", "does a thing", "step one", "mine")

        assert path == tmp_path / "runtime" / "skills" / "my-skill.md"
        assert path.is_file()
        # Discoverable immediately, and user-owned so it can be edited/deleted.
        assert registry.get_skill("my-skill") is not None
        assert registry.is_user_owned("my-skill") is True
        assert registry.get_by_slash_command("mine") is not None

    def test_create_refuses_to_clobber_an_existing_custom_skill(self, tmp_path):
        """Overwriting is refused, so nothing the user wrote is lost silently."""
        registry = self._registry(tmp_path)
        registry.discover()
        registry.create_skill("mine", "the original", "body")

        with pytest.raises(FileExistsError):
            registry.create_skill("mine", "the replacement", "other body")

        registry.discover()
        assert registry.get_skill("mine").description == "the original"

    def test_create_may_shadow_a_base_skill(self, tmp_path):
        """Creating a base skill's name is how a shipped skill gets overridden.

        Discovery prefers the later directory, so the custom copy wins -- and the
        base file is left where it is for the next `promote`/reset.
        """
        base_dir = tmp_path / "codeassist" / "skills"
        base_dir.mkdir(parents=True)
        (base_dir / "clean.md").write_text(
            "---\nname: clean\ndescription: shipped\n---\nshipped body\n", encoding="utf-8"
        )
        registry = self._registry(tmp_path)
        registry.discover()
        assert registry.get_skill("clean").description == "shipped"

        registry.create_skill("clean", "my own clean", "mine", "myclean")

        assert (base_dir / "clean.md").is_file(), "the shipped file is untouched"
        registry.discover()
        assert registry.get_skill("clean").description == "my own clean"
        assert registry.category_for(registry.get_skill("clean")) == "custom"

    @pytest.mark.parametrize("bad_name", ["", "   ", "has space", "../../etc/passwd", "a/b"])
    def test_create_rejects_a_name_that_would_not_reparse(self, tmp_path, bad_name):
        """A name the frontmatter parser rejects never reaches the filesystem.

        The name pattern is also what keeps the write inside the custom
        directory, so a traversal attempt has to die here.
        """
        registry = self._registry(tmp_path)
        registry.discover()

        with pytest.raises(SkillValidationError):
            registry.create_skill(bad_name, "d", "body")

        assert not (tmp_path / "runtime").exists()

    def test_create_requires_a_description(self, tmp_path):
        """`description` is what the agent sees when choosing a skill."""
        registry = self._registry(tmp_path)

        with pytest.raises(SkillValidationError):
            registry.create_skill("no-desc", "", "body")

        assert not (tmp_path / "runtime").exists()

    @pytest.mark.parametrize("description", [
        'He said "go" -- then left',
        "First line\nSecond line",
        'mixed: "quoted"\nsecond\\line\twith tab',
        "100% done \\ literally",
    ])
    def test_a_description_survives_a_reparse(self, tmp_path, description):
        """Whatever a description contains, it must come back unchanged.

        The frontmatter parser is a line-at-a-time `key: value` split, so the
        writer has to escape a newline (which would otherwise truncate the
        description at the first line) and a quote (which would otherwise close
        the value early), and the reader has to undo exactly those.
        """
        registry = self._registry(tmp_path)
        registry.discover()

        registry.create_skill("desc", description, "body")

        registry.discover()
        assert registry.get_skill("desc").description == description

