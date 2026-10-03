"""
Create Skill Tool - Allows creating new skills with KB integration.
"""

import logging
from pathlib import Path

from codeassist.knowledge import KnowledgeBase
from codeassist.skills import (
    SkillRegistry,
    SkillValidationError,
    validate_skill_frontmatter,
)

from . import Tool, ToolResult

log = logging.getLogger(__name__)


class CreateSkill(Tool):
    name = "create_skill"
    description = (
        "Create a new skill for repetitive workflows. "
        "Skills are markdown files that define reusable workflows. "
        "The skill is saved to runtime/skills/ and becomes available immediately."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name (lowercase, hyphens allowed)",
            },
            "description": {
                "type": "string",
                "description": "Brief description of what this skill does",
            },
            "slash_command": {
                "type": "string",
                "description": "Optional slash command (e.g., 'deploy')",
            },
            "content": {
                "type": "string",
                "description": "The skill workflow instructions in markdown",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags for categorization",
            },
        },
        "required": ["name", "description", "content"],
    }

    async def execute(self, name: str, description: str, content: str,
                      slash_command: str | None = None,
                      tags: list[str] | None = None,
                      session_id: str | None = None) -> ToolResult:
        workspace = str(self.workspace) if hasattr(self, "workspace") else "."
        skill_path = Path(workspace) / "runtime" / "skills" / f"{name}.md"
        # Validate against the same rules the reader applies. This replaces a
        # local `isalnum` check that was both looser and stricter than the
        # registry: it accepted unicode names ("café") that discovery then
        # skipped, and it never checked the description at all -- a description
        # containing a newline produced a file nothing could parse, and the
        # agent was told it had worked. Bad input is not an internal error, so
        # it is reported without the traceback the catch-all below logs.
        try:
            validate_skill_frontmatter(
                {"name": name, "description": description or "", "slash": slash_command},
                skill_path,
            )
        except SkillValidationError as e:
            return ToolResult(output=f"Error creating skill: {e}", error=True)

        try:
            if skill_path.exists():
                return ToolResult(output=f"Error: Skill '{name}' already exists at {skill_path}", error=True)

            skill_path.parent.mkdir(parents=True, exist_ok=True)

            # The registry owns the on-disk format: it quotes and escapes the
            # description, and its parser is the exact inverse. Hand-rolled
            # frontmatter here was a second writer with the same bugs.
            skill_path.write_text(
                SkillRegistry.format_skill_file(name, description, content, slash_command),
                encoding="utf-8",
            )

            entry_id = await KnowledgeBase.create_knowledge_entry(
                entry_type="skill_created",
                scope="project",
                scope_identifier=f"runtime/skills/{name}.md",
                content=f"Created skill '{name}': {description}",
                source_session_id=session_id,
                confidence=1.0,
                tags=tags or ["skill", "auto_created"],
                metadata={"skill_name": name, "slash_command": slash_command, "created_by": "create_skill_tool"},
            )

            try:
                from codeassist.embeddings import get_embedding_manager
                manager = get_embedding_manager()
                import asyncio
                asyncio.create_task(manager.generate_and_store_embedding(entry_id, f"{name}: {description} {content[:500]}"))
            except Exception as e:  # noqa: BLE001
                log.debug("Embedding generation skipped: %s", e)

            return ToolResult(output=f"Skill '{name}' created successfully!\n\nLocation: {skill_path}\n\nUse '/{slash_command}' or mention '{name}' to invoke.")

        except Exception as e:
            log.exception("Failed to create skill")
            return ToolResult(output=f"Error creating skill: {e}", error=True)


# Legacy TOOLS dict for backward compatibility
TOOLS = {
    "create_skill": {
        "name": "create_skill",
        "description": CreateSkill.description,
        "parameters": CreateSkill.parameters,
    }
}
