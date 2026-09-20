"""Skills API routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _discover_registry():
    """Build a fresh disk SkillRegistry and discover skills from it.

    Uses ``cfg.workspace`` (the same resolved workspace the boot-time global
    registry uses) so edit/delete operate on exactly what ``GET /api/skills``
    lists.
    """
    from pathlib import Path

    from ..server import get_config
    from codeassist.skills import SkillRegistry

    cfg = get_config()
    registry = SkillRegistry(Path(cfg.workspace), cfg.skills)
    registry.discover()
    return registry


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
    from codeassist.session import Skill
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


@router.get("/list")
async def list_all_skills():
    """List all skills discovered from disk (bypasses database)."""
    from codeassist.skills import SkillRegistry
    from codeassist.config import load_config
    from pathlib import Path
    config = load_config()
    workspace = Path(config.server.workspace)
    registry = SkillRegistry(workspace, config.skills)
    skills = registry.discover()
    return {"skills": [s.to_dict() for s in skills]}


@router.post("/reload")
async def reload_skills():
    """Hot-reload skills from disk without restarting the server."""
    from codeassist.skills import SkillRegistry
    from codeassist.config import load_config
    from pathlib import Path
    config = load_config()
    workspace = Path(config.server.workspace)
    registry = SkillRegistry(workspace, config.skills)
    registry.reload()
    return {"message": "Skills reloaded", "count": len(registry._skills)}


@router.put("/{name}")
async def update_skill(name: str, body: dict):
    """Update a discovered skill's description/content/slash in place on disk.

    The change is written back to the skill's source file and the live registry
    is re-discovered so it takes effect immediately.
    """
    from ..server import skill_registry as global_registry
    from codeassist.skills import SkillRegistry

    registry = _discover_registry()
    skill = registry.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")

    path = registry.update_skill(
        name,
        description=body.get("description", skill.description),
        content=body.get("content", skill.content),
        slash_command=body.get("slash_command", skill.slash_command),
    )
    if path is None:
        raise HTTPException(status_code=409,
                            detail=f"Skill '{name}' is not backed by a workspace file")

    # Refresh the live registry so subsequent sessions/tools see the edit.
    if isinstance(global_registry, SkillRegistry):
        global_registry._skills.clear()
        global_registry._slash_commands.clear()
        global_registry.discover()
    return {"ok": True, "path": str(path)}


@router.delete("/{name}")
async def delete_skill(name: str):
    """Delete a discovered skill's source file from the workspace."""
    from ..server import skill_registry as global_registry
    from codeassist.skills import SkillRegistry

    registry = _discover_registry()
    if not registry.get_skill(name):
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")

    path = registry.remove_skill(name)
    if path is None:
        raise HTTPException(status_code=409,
                            detail=f"Skill '{name}' is not backed by a workspace file")

    if isinstance(global_registry, SkillRegistry):
        global_registry._skills.clear()
        global_registry._slash_commands.clear()
        global_registry.discover()
    return {"ok": True, "deleted": str(path)}
