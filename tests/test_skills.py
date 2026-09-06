"""Tests for skills system."""
import pytest
from pathlib import Path

from codeassist.skills import SkillRegistry, SkillTool


class TestSkillRegistry:
    """Test skill discovery and management."""

    @pytest.fixture
    def skill_registry(self, tmp_path):
        """Create a skill registry with test skills."""
        config = type('Config', (), {
            'enabled': True,
            'directories': ['.skills']
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
            'directories': ['.skills']
        })()
        
        registry = SkillRegistry(tmp_path, config)
        skills = registry.discover()
        
        assert len(skills) == 0

    def test_empty_skill_directory(self, tmp_path):
        """Test behavior with empty skill directory."""
        config = type('Config', (), {
            'enabled': True,
            'directories': ['.skills']
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
            'directories': ['.skills']
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
            'directories': ['.skills']
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
        
        assert "test-skill" in result
        assert "A test skill" in result

    @pytest.mark.asyncio
    async def test_skill_tool_get(self, skill_tool):
        """Test getting skill instructions via tool."""
        result = await skill_tool.execute(action="get", skill_name="test-skill")
        
        assert "test-skill" in result
        assert "Test content here" in result

    @pytest.mark.asyncio
    async def test_skill_tool_get_nonexistent(self, skill_tool):
        """Test getting nonexistent skill."""
        result = await skill_tool.execute(action="get", skill_name="nonexistent")
        
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_skill_tool_invalid_action(self, skill_tool):
        """Test invalid action."""
        result = await skill_tool.execute(action="invalid")
        
        assert "Error" in result or "unknown action" in result.lower()

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
        from codeassist.skills import validate_skill_frontmatter, SkillValidationError
        
        frontmatter = {
            "description": "A valid skill",
        }
        
        with pytest.raises(SkillValidationError, match="missing required field 'name'"):
            validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_invalid_name_chars(self, tmp_path):
        """Test that invalid characters in name are caught."""
        from codeassist.skills import validate_skill_frontmatter, SkillValidationError
        
        frontmatter = {
            "name": "my skill!",
            "description": "A valid skill",
        }
        
        with pytest.raises(SkillValidationError, match="alphanumeric characters"):
            validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_missing_description(self, tmp_path):
        """Test that missing description is caught."""
        from codeassist.skills import validate_skill_frontmatter, SkillValidationError
        
        frontmatter = {
            "name": "my-skill",
        }
        
        with pytest.raises(SkillValidationError, match="missing required field 'description'"):
            validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_invalid_slash_command(self, tmp_path):
        """Test that invalid slash command characters are caught."""
        from codeassist.skills import validate_skill_frontmatter, SkillValidationError
        
        frontmatter = {
            "name": "my-skill",
            "description": "A valid skill",
            "slash": "my skill!",
        }
        
        with pytest.raises(SkillValidationError, match="alphanumeric characters"):
            validate_skill_frontmatter(frontmatter, tmp_path / "test.md")

    def test_empty_name(self, tmp_path):
        """Test that empty name is caught."""
        from codeassist.skills import validate_skill_frontmatter, SkillValidationError
        
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
            'directories': ['.skills']
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
        import logging
        from codeassist.skills import SkillRegistry
        
        config = type('Config', (), {
            'enabled': True,
            'directories': ['.skills']
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
