"""Skills API routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
async def list_skills():
    """List all discovered skills from configured directories."""
    from ..server import get_config, skill_registry
    cfg = get_config()
    if not cfg.skills.enabled or not skill_registry:
        return {"skills": []}
    return {"skills": skill_registry.list_skills()}


@router.post("")
async def create_skill(body: dict):
    """Create a new skill stored in the database. Body must contain 'name', 'description', and 'content'."""
    from ..server import get_config
    from session import Skill
    cfg = get_config()
    if not cfg.skills.enabled:
        raise HTTPException(status_code=400, detail="Skills are not enabled")
    skill = await Skill.create(
        name=body.get("name"),
        description=body.get("description", ""),
        content=body.get("content", ""),
        slash_command=body.get("slash_command"),
    )
    return {"id": skill.id}


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str):
    """Delete a skill by ID (soft-delete, sets enabled=0)."""
    from session import Skill
    skill = Skill(skill_id)
    await skill.delete()
    return {"ok": True}


@router.get("/list")
async def list_all_skills():
    """List all skills discovered from disk (bypasses database)."""
    from skills import SkillRegistry
    from config import load_config
    from pathlib import Path
    config = load_config()
    workspace = Path(config.server.workspace)
    registry = SkillRegistry(workspace, config.skills)
    skills = registry.discover()
    return {"skills": [s.to_dict() for s in skills]}


@router.post("/reload")
async def reload_skills():
    """Hot-reload skills from disk without restarting the server."""
    from skills import SkillRegistry
    from config import load_config
    from pathlib import Path
    config = load_config()
    workspace = Path(config.server.workspace)
    registry = SkillRegistry(workspace, config.skills)
    registry.reload()
    return {"message": "Skills reloaded", "count": len(registry._skills)}
