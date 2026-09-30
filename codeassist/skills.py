import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .tools import ToolResult

log = logging.getLogger(__name__)


class SkillValidationError(Exception):
    """Raised when a skill file fails validation."""


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


def validate_skill_frontmatter(frontmatter: dict[str, str], path: Path) -> None:
    """Validate skill frontmatter fields.

    Raises SkillValidationError if required fields are missing or invalid.
    """
    errors = []

    name = frontmatter.get("name", "")
    if not name or not name.strip():
        errors.append("missing required field 'name'")
    elif not re.match(r'^[a-zA-Z0-9_-]+$', name):
        errors.append(f"name must contain only alphanumeric characters, hyphens, and underscores (got: '{name}')")

    description = frontmatter.get("description", "")
    if not description or not description.strip():
        errors.append("missing required field 'description'")

    slash = frontmatter.get("slash")
    if slash is not None and not re.match(r'^[a-zA-Z0-9_-]+$', slash):
        errors.append(f"slash command must contain only alphanumeric characters, hyphens, and underscores (got: '{slash}')")

    if errors:
        raise SkillValidationError(
            f"Invalid frontmatter in {path}: {'; '.join(errors)}"
        )


class SkillRegistry:
    """Discovers and manages skills from the workspace."""

    def __init__(self, workspace: Path, config=None):
        self.workspace = workspace
        self.config = config
        self._skills: dict[str, Skill] = {}
        self._slash_commands: dict[str, Skill] = {}
        self._last_discover_time: float = 0

    def discover(self) -> list[Skill]:
        """Discover skills from the packaged skills and the configured
        workspace directories.

        The packaged skills are scanned first so that a workspace copy of the
        same skill shadows the shipped default. Each directory is scanned at
        most once, so a workspace that *is* the project root does not report
        every skill twice.
        """
        if not self.config or not self.config.enabled:
            return []

        scanned: set[Path] = set()

        if getattr(self.config, "include_packaged", True):
            self._scan_dir(self.packaged_skills_dir(), scanned)

        for dir_name in self.config.directories:
            self._scan_dir(self.workspace / dir_name, scanned)

        return list(self._skills.values())

    @staticmethod
    def packaged_skills_dir() -> Path:
        """The skills shipped inside the package, next to this module.

        Resolved from ``__file__`` rather than the workspace so the defaults are
        found for any ``server.workspace`` (a container may mount an unrelated
        directory, or a wheel install may not have a project checkout at all).
        """
        return Path(__file__).resolve().parent / "skills"

    def _scan_dir(self, directory: Path, scanned: set[Path]) -> None:
        """Scan ``directory`` for skills, skipping any already scanned."""
        try:
            resolved = directory.resolve()
        except OSError:  # pragma: no cover - unresolvable path
            log.warning("Skipping unresolvable skill directory %s", directory)
            return
        if not resolved.is_dir() or resolved in scanned:
            return
        scanned.add(resolved)
        self._discover_from_directory(resolved)

    def reload(self) -> list[Skill]:
        """Hot-reload skills from disk."""
        import time
        self._skills.clear()
        self._slash_commands.clear()
        self._last_discover_time = time.time()
        return self.discover()

    def _discover_from_directory(self, directory: Path):
        """Discover skills from a directory."""
        for skill_file in directory.rglob("*.md"):
            try:
                skill = self._parse_skill_file(skill_file)
                if skill:
                    # Check for duplicate slash commands
                    if skill.slash_command and skill.slash_command in self._slash_commands:
                        existing = self._slash_commands[skill.slash_command]
                        log.warning(
                            "Duplicate slash command '/%s': '%s' (from %s) overrides '%s' (from %s)",
                            skill.slash_command, skill.name, skill_file,
                            existing.name, existing.source,
                        )
                    self._skills[skill.name] = skill
                    if skill.slash_command:
                        self._slash_commands[skill.slash_command] = skill
                    log.debug("Discovered skill: %s from %s", skill.name, skill_file)
            except SkillValidationError as e:
                log.warning("Skipping invalid skill file %s: %s", skill_file, e)
            except Exception as e:  # noqa: BLE001
                log.error("Failed to parse skill file %s: %s", skill_file, e)

    def _parse_skill_file(self, path: Path) -> Skill | None:
        """Parse a skill markdown file with frontmatter and validate it."""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
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

        # Validate frontmatter before creating the skill
        validate_skill_frontmatter(frontmatter, path)

        return Skill(
            name=name,
            description=description,
            content=body.strip(),
            slash_command=slash_command,
            source=self._source_for(path),
        )

    def _source_for(self, path: Path) -> str:
        """Describe where a skill file lives.

        Workspace-backed skills get a workspace-relative posix path, which is
        what makes them editable, movable and exportable. Skills that ship with
        the package sit outside the workspace, so they are tagged with a
        ``package:`` prefix instead of a misleading relative path.
        """
        try:
            return path.resolve().relative_to(self.workspace.resolve()).as_posix()
        except (ValueError, OSError):
            return f"{self.PACKAGE_SOURCE_PREFIX}{path.as_posix()}"

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_by_slash_command(self, command: str) -> Skill | None:
        """Get a skill by slash command."""
        return self._slash_commands.get(command)

    # --- disk edit / remove (admin-managed skills) ------------------------- #

    def _resolve_skill_path(self, name: str) -> Path | None:
        """Return the on-disk source path for a discovered skill, scoped to the
        workspace. Returns None if the skill is unknown, ships with the package,
        or its source escapes the workspace (guards against path traversal via
        a crafted `source`)."""

        skill = self._skills.get(name)
        if not skill or not skill.source:
            return None
        if skill.source.startswith(self.PACKAGE_SOURCE_PREFIX):
            return None  # packaged default, not workspace-managed
        candidate = (self.workspace / skill.source).resolve()
        workspace_root = self.workspace.resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate

    @staticmethod
    def format_skill_file(name: str, description: str, content: str,
                          slash_command: str | None = None) -> str:
        """Render a skill markdown file (frontmatter + body) matching the
        parser used by ``_parse_skill_file``."""
        lines = ["---", f"name: {name}"]
        if description:
            # Quote to survive the simple ``key: value`` frontmatter parser.
            lines.append(f'description: "{description.replace(chr(34), chr(92) + chr(34))}"')
        if slash_command:
            lines.append(f"slash: {slash_command}")
        lines.append("---")
        body = content.strip("\n")
        return "\n".join(lines) + ("\n" if not body else "\n\n" + body) + "\n"

    def update_skill(self, name: str, description: str = "", content: str = "",
                     slash_command: str | None = None) -> Path | None:
        """Rewrite a discovered skill's file in place. Returns the path, or None
        if the skill is not backed by a discoverable workspace file."""
        path = self._resolve_skill_path(name)
        if path is None:
            return None
        path.write_text(self.format_skill_file(name, description, content, slash_command),
                        encoding="utf-8")
        return path

    def remove_skill(self, name: str) -> Path | None:
        """Delete a discovered skill's file. Returns the path, or None if not
        backed by a discoverable workspace file."""
        path = self._resolve_skill_path(name)
        if path is None:
            return None
        path.unlink()
        return path

    def list_skills(self) -> list[dict]:
        """List all available skills."""
        return [skill.to_dict() for skill in self._skills.values()]

    # --- export / import / promote (portability) --------------------------- #

    SKILLS_BUNDLE = "codeassist-skills-bundle"
    BASE_DIR = "codeassist/skills"
    CUSTOM_DIR = "runtime/skills"
    PACKAGE_SOURCE_PREFIX = "package:"

    def _category_for(self, rel_posix: str) -> str:
        return "base" if rel_posix.startswith(self.BASE_DIR + "/") else "custom"

    def export_skills(self) -> dict:
        """Return a portable JSON manifest of every discovered skill.

        Mirrors the KB export envelope (``version`` / ``exported_at`` / ``data``).
        Each entry records the rendered body plus the category (``base`` for
        shipped skills under ``codeassist/skills``, ``custom`` for everything
        else) so it can be round-tripped and promoted.

        Only workspace-backed skills are exported; packaged defaults are already
        installed with the package and are not re-imported over the top of them.
        """
        manifest = {
            "format": self.SKILLS_BUNDLE,
            "version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "data": {"skills": []},
        }
        for skill in self.discover():
            path = self._resolve_skill_path(skill.name)
            if path is None:
                continue
            rel = path.relative_to(self.workspace).as_posix()
            manifest["data"]["skills"].append({
                "name": skill.name,
                "description": skill.description,
                "slash": skill.slash_command,
                "body": skill.content,
                "category": self._category_for(rel),
                "path": rel,
            })
        return manifest

    def import_skills(self, manifest: dict) -> dict:
        """Write skills from an :meth:`export_skills` manifest to disk.

        ``base`` entries land in ``codeassist/skills`` and ``custom`` entries in
        ``runtime/skills``; the registry is reloaded so imports take effect.
        """
        if manifest.get("format") != self.SKILLS_BUNDLE:
            raise ValueError("not a CodeAssist skill bundle")

        imported = []
        for entry in manifest.get("data", {}).get("skills", []):
            name = entry["name"]
            target_dir = self.workspace / (self.BASE_DIR
                                          if entry.get("category") == "base"
                                          else self.CUSTOM_DIR)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{name}.md"
            target.write_text(
                self.format_skill_file(
                    name,
                    entry.get("description", ""),
                    entry.get("body", ""),
                    entry.get("slash"),
                ),
                encoding="utf-8",
            )
            imported.append(target.relative_to(self.workspace).as_posix())

        self.reload()
        return {"imported": imported}

    def promote_skill(self, name: str) -> str | None:
        """Move a custom skill into the shipped base directory.

        Returns the new relative path, or ``None`` if the skill is not backed by
        a discoverable workspace file (or already lives under the base dir).
        """
        path = self._resolve_skill_path(name)
        if path is None:
            return None
        rel = path.relative_to(self.workspace).as_posix()
        if rel.startswith(self.BASE_DIR + "/"):
            return None  # already a base skill
        target_dir = self.workspace / self.BASE_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{name}.md"
        if target.exists():
            return None  # a base skill with the same name already exists
        path.rename(target)
        self.reload()
        return target.relative_to(self.workspace).as_posix()

    def get_instructions(self) -> str:
        """Get instructions for all skills to include in system prompt."""
        if not self._skills:
            return ""

        instructions = "<available_skills>\n"
        for skill in self._skills.values():
            instructions += "  <skill>\n"
            instructions += f"    <name>{skill.name}</name>\n"
            instructions += f"    <description>{skill.description}</description>\n"
            if skill.slash_command:
                instructions += f"    <slash_command>/{skill.slash_command}</slash_command>\n"
            instructions += "  </skill>\n"
        instructions += "</available_skills>"

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
    parameters = {  # noqa: RUF012
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

    async def execute(self, action: str, skill_name: str | None = None) -> "ToolResult":
        # Must return a ToolResult, not a bare str: the registry hands this
        # straight to the agent, which reads `result.output`. Returning a str
        # made any use of a skill -- e.g. asking for the music skill -- abort
        # the turn with "'str' object has no attribute 'output'".
        from .tools import ToolResult

        if action == "list":
            skills = self.registry.list_skills()
            if not skills:
                return ToolResult(
                    output="No skills available. Add skill files to your configured skill directories.",
                    error=True,
                )

            result = ["**Available Skills:**\n"]
            for skill in skills:
                slash = f" (/{skill['slash_command']})" if skill.get('slash_command') else ""
                result.append(f"- **{skill['name']}**: {skill['description']}{slash}")

            return ToolResult(output="\n".join(result))

        elif action == "get":
            if not skill_name:
                return ToolResult(
                    output="Error: skill_name is required for 'get' action", error=True
                )

            skill = self.registry.get_skill(skill_name)
            if not skill:
                return ToolResult(
                    output=f"Error: skill '{skill_name}' not found. Use 'list' to see available skills.",
                    error=True,
                )

            return ToolResult(output=f"**Skill: {skill.name}**\n\n{skill.content}")

        return ToolResult(
            output=f"Error: unknown action '{action}'. Use 'list' or 'get'.", error=True
        )
