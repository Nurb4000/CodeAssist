"""
Create Skill Tool - Allows creating new skills with KB integration.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from knowledge import KnowledgeBase

log = logging.getLogger(__name__)


TOOLS = {
    "create_skill": {
        "name": "create_skill",
        "description": (
            "Create a new skill for repetitive workflows. "
            "Skills are markdown files that define reusable workflows. "
            "The skill is saved to .codeassist/skills/ and becomes available immediately."
        ),
        "parameters": {
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
        },
    }
}


async def execute(
    name: str,
    description: str,
    content: str,
    slash_command: str | None = None,
    tags: list[str] | None = None,
    workspace: str = ".",
    session_id: str | None = None,
) -> str:
    """Create a new skill file and log to KB."""
    try:
        # Validate skill name
        if not name.replace("-", "").replace("_", "").isalnum():
            return "Error: Skill name must contain only letters, numbers, hyphens, or underscores"
        
        # Check if skill already exists
        skill_path = Path(workspace) / ".codeassist" / "skills" / f"{name}.md"
        if skill_path.exists():
            return f"Error: Skill '{name}' already exists at {skill_path}"
        
        # Ensure directory exists
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Build frontmatter
        frontmatter_lines = [
            "---",
            f"name: {name}",
            f"description: {description}",
        ]
        if slash_command:
            frontmatter_lines.append(f"slash: {slash_command}")
        frontmatter_lines.append("---")
        
        # Write skill file
        skill_content = "\n".join(frontmatter_lines) + "\n\n" + content
        skill_path.write_text(skill_content, encoding="utf-8")
        
        # Log to KB
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="skill_created",
            scope="project",
            scope_identifier=f".codeassist/skills/{name}.md",
            content=f"Created skill '{name}': {description}",
            source_session_id=session_id,
            confidence=1.0,
            tags=tags or ["skill", "auto_created"],
            metadata={
                "skill_name": name,
                "slash_command": slash_command,
                "created_by": "create_skill_tool",
            },
        )
        
        # Generate embedding for the skill
        try:
            from embeddings import get_embedding_manager
            manager = get_embedding_manager()
            import asyncio
            asyncio.create_task(manager.generate_and_store_embedding(entry_id, f"{name}: {description} {content[:500]}"))
        except Exception as e:
            log.debug("Embedding generation skipped: %s", e)
        
        # Reload skills in the registry
        _reload_skills(workspace)
        
        return f"✅ Skill '{name}' created successfully!\n\nLocation: {skill_path}\n\nUse '/{slash_command}' or mention '{name}' to invoke."
    
    except Exception as e:
        log.exception("Failed to create skill")
        return f"Error creating skill: {e}"


def _reload_skills(workspace: str):
    """Trigger skill reload in the registry."""
    try:
        # Import here to avoid circular imports
        from skills import SkillRegistry
        from config import load_config
        
        config = load_config()
        registry = SkillRegistry(Path(workspace), config.skills)
        registry.discover()
        
        # Store in module-level cache for access
        import skills
        skills._global_registry = registry
        
        log.info("Skills reloaded after creation")
    except Exception as e:
        log.warning("Failed to reload skills: %s", e)
