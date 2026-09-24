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

    from codeassist.skills import SkillRegistry

    from ..server import get_config

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
    from codeassist.session import Skill

    from ..server import get_config
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
    from pathlib import Path

    from codeassist.config import load_config
    from codeassist.skills import SkillRegistry
    config = load_config()
    workspace = Path(config.server.workspace)
    registry = SkillRegistry(workspace, config.skills)
    skills = registry.discover()
    return {"skills": [s.to_dict() for s in skills]}


@router.post("/reload")
async def reload_skills():
    """Hot-reload skills from disk without restarting the server."""
    from pathlib import Path

    from codeassist.config import load_config
    from codeassist.skills import SkillRegistry
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
    from codeassist.skills import SkillRegistry

    from ..server import skill_registry as global_registry

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
    from codeassist.skills import SkillRegistry

    from ..server import skill_registry as global_registry

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


@router.get("/export")
async def export_skills():
    """Export all skills (base + custom) as a portable JSON manifest."""
    registry = _discover_registry()
    return registry.export_skills()


@router.post("/import")
async def import_skills(manifest: dict):
    """Import skills from a manifest produced by :func:`export_skills`.

    ``base`` entries are written to the shipped directory and ``custom`` entries
    to the runtime directory; the live registry is reloaded so imports take effect.
    """
    from codeassist.skills import SkillRegistry

    from ..server import skill_registry as global_registry

    registry = _discover_registry()
    try:
        result = registry.import_skills(manifest)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Refresh the live registry so subsequent sessions/tools see the imports.
    if isinstance(global_registry, SkillRegistry):
        global_registry._skills.clear()
        global_registry._slash_commands.clear()
        global_registry.discover()
    return {"ok": True, **result}


@router.post("/{name}/promote")
async def promote_skill(name: str):
    """Promote a custom skill into the shipped base directory."""
    from codeassist.skills import SkillRegistry

    from ..server import skill_registry as global_registry

    registry = _discover_registry()
    target = registry.promote_skill(name)
    if target is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Skill '{name}' could not be promoted: it is not a discoverable "
                "custom skill, or a base skill with the same name already exists."
            ),
        )

    if isinstance(global_registry, SkillRegistry):
        global_registry._skills.clear()
        global_registry._slash_commands.clear()
        global_registry.discover()
    return {"ok": True, "path": target}
