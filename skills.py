import logging
import re
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class Skill:
    """Represents a reusable agent-guided workflow."""

    def __init__(self, name: str, description: str, content: str,
                 slash_command: str | None = None, source: str | None = None):
        self.name = name
        self.description = description
        self.content = content
        self.slash_command = slash_command
        self.source = source or "local"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "slash_command": self.slash_command,
            "source": self.source,
        }


class SkillRegistry:
    """Discovers and manages skills from the workspace."""

    def __init__(self, workspace: Path, config=None):
        self.workspace = workspace
        self.config = config
        self._skills: dict[str, Skill] = {}
        self._slash_commands: dict[str, Skill] = {}

    def discover(self) -> list[Skill]:
        """Discover skills from configured directories."""
        if not self.config or not self.config.enabled:
            return []

        directories = self.config.directories
        for dir_name in directories:
            skill_dir = self.workspace / dir_name
            if skill_dir.exists() and skill_dir.is_dir():
                self._discover_from_directory(skill_dir)

        return list(self._skills.values())

    def _discover_from_directory(self, directory: Path):
        """Discover skills from a directory."""
        for skill_file in directory.rglob("*.md"):
            try:
                skill = self._parse_skill_file(skill_file)
                if skill:
                    self._skills[skill.name] = skill
                    if skill.slash_command:
                        self._slash_commands[skill.slash_command] = skill
                    log.debug("Discovered skill: %s from %s", skill.name, skill_file)
            except Exception as e:
                log.error("Failed to parse skill file %s: %s", skill_file, e)

    def _parse_skill_file(self, path: Path) -> Skill | None:
        """Parse a skill markdown file with frontmatter."""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            log.error("Failed to read skill file %s: %s", path, e)
            return None

        # Parse frontmatter
        frontmatter = {}
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                fm_content = parts[1]
                body = parts[2] if len(parts) > 2 else ""

                # Simple frontmatter parsing (name, description, slash)
                for line in fm_content.strip().split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        frontmatter[key.strip()] = value.strip().strip('"').strip("'")

        name = frontmatter.get("name", path.stem)
        description = frontmatter.get("description", "")
        slash_command = frontmatter.get("slash")

        return Skill(
            name=name,
            description=description,
            content=body.strip(),
            slash_command=slash_command,
            source=str(path.relative_to(self.workspace)),
        )

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_by_slash_command(self, command: str) -> Skill | None:
        """Get a skill by slash command."""
        return self._slash_commands.get(command)

    def list_skills(self) -> list[dict]:
        """List all available skills."""
        return [skill.to_dict() for skill in self._skills.values()]

    def get_instructions(self) -> str:
        """Get instructions for all skills to include in system prompt."""
        if not self._skills:
            return ""

        instructions = "\n\n## Available Skills\n\n"
        for skill in self._skills.values():
            slash = f" (slash: /{skill.slash_command})" if skill.slash_command else ""
            instructions += f"- **{skill.name}**: {skill.description}{slash}\n"

        instructions += "\nTo use a skill, mention it by name or use its slash command.\n"
        return instructions

    def execute(self, skill_name: str, context: dict[str, Any]) -> str:
        """Execute a skill with the given context."""
        skill = self._skills.get(skill_name)
        if not skill:
            return f"Error: skill '{skill_name}' not found"

        # In a full implementation, this would execute the skill workflow
        # For now, return the skill content as instructions
        return f"Skill '{skill.name}' instructions:\n\n{skill.content}"


class SkillTool:
    """Tool for interacting with skills."""

    name = "skill"
    description = (
        "List available skills or get instructions for a specific skill. "
        "Use 'list' to see all skills, or provide a skill name to get its instructions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get"],
                "description": "Action to perform",
            },
            "skill_name": {
                "type": "string",
                "description": "Name of the skill (for 'get' action)",
            },
        },
        "required": ["action"],
    }

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute(self, action: str, skill_name: str | None = None) -> str:
        if action == "list":
            skills = self.registry.list_skills()
            if not skills:
                return "No skills available. Add skill files to your configured skill directories."

            result = ["**Available Skills:**\n"]
            for skill in skills:
                slash = f" (/{skill['slash_command']})" if skill.get('slash_command') else ""
                result.append(f"- **{skill['name']}**: {skill['description']}{slash}")

            return "\n".join(result)

        elif action == "get":
            if not skill_name:
                return "Error: skill_name is required for 'get' action"

            skill = self.registry.get_skill(skill_name)
            if not skill:
                return f"Error: skill '{skill_name}' not found. Use 'list' to see available skills."

            return f"**Skill: {skill.name}**\n\n{skill.content}"

        else:
            return f"Error: unknown action '{action}'. Use 'list' or 'get'."
