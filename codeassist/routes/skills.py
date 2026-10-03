"""Skills API routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _fresh_registry():
    """Build an undiscovered disk SkillRegistry against the live workspace.

    Uses ``cfg.workspace`` (the same resolved workspace the boot-time global
    registry uses) so edit/delete/reload operate on exactly what
    ``GET /api/skills`` lists.
    """
    from pathlib import Path

    from codeassist.skills import SkillRegistry

    from ..server import get_config

    cfg = get_config()
    return SkillRegistry(Path(cfg.workspace), cfg.skills)


def _discover_registry():
    """Build a fresh disk SkillRegistry and discover skills from it."""
    registry = _fresh_registry()
    registry.discover()
    return registry


def _refresh_live_registry():
    """Re-discover the boot-time registry so an on-disk change takes effect now.

    Every mutating route needs this, and doing it by hand meant reaching into two
    private dicts and re-implementing what ``SkillRegistry.reload()`` already
    does -- five copies of it, all liable to drift. Returns the refreshed
    registry so a caller can report what the server now serves, or None if the
    app booted without one.
    """
    from codeassist.skills import SkillRegistry

    from ..server import skill_registry

    if not isinstance(skill_registry, SkillRegistry):
        return None
    skill_registry.reload()
    return skill_registry


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
    """Create a new custom skill on disk. Body must contain 'name' and
    'description'; 'content' and 'slash_command' are optional.

    The file lands in the custom skills directory, the same place the
    ``create_skill`` agent tool writes, so it shows up in the list immediately.
    This used to insert a row into the legacy ``skills`` table that nothing read
    back: the create form reported success and the skill never appeared.
    """
    from codeassist.skills import SkillRegistry, SkillValidationError

    from ..server import get_config

    cfg = get_config()
    if not cfg.skills.enabled:
        raise HTTPException(status_code=400, detail="Skills are not enabled")

    registry = _discover_registry()
    name = (body.get("name") or "").strip()
    try:
        path = registry.create_skill(
            name,
            description=body.get("description", ""),
            content=body.get("content", ""),
            slash_command=body.get("slash_command"),
        )
    except SkillValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))

    _refresh_live_registry()
    return {
        "ok": True,
        "name": name,
        "path": path.relative_to(registry.workspace).as_posix(),
        "category": SkillRegistry.CATEGORY_CUSTOM,
    }


@router.get("/list")
async def list_all_skills():
    """List all skills discovered from disk (bypasses database)."""
    registry = _discover_registry()
    return {"skills": registry.list_skills()}


@router.post("/reload")
async def reload_skills():
    """Hot-reload skills from disk without restarting the server.

    Reports the count the live registry now holds. This used to reload a
    throwaway registry and answer with that one's private skill count, so the
    button reported a number unrelated to what the server was serving -- and
    never actually reloaded the live one.
    """
    registry = _refresh_live_registry()
    count = len(registry.list_skills()) if registry else 0
    return {"message": "Skills reloaded", "count": count}


def _not_user_owned(registry, name: str, action: str) -> HTTPException:
    """409 for a mutation aimed at a skill the app owns rather than the user.

    The registry enforces this too (its update/remove helpers refuse anything
    that is not a custom skill); doing it here as well lets the API explain why
    instead of reporting a generic "no workspace file" back.
    """
    skill = registry.get_skill(name)
    category = registry.category_for(skill) if skill else "unknown"
    return HTTPException(
        status_code=409,
        detail=(
            f"Skill '{name}' is a {category} skill and cannot be {action}. "
            f"Copy it into {registry.CUSTOM_DIR}/ to change it as your own skill."
        ),
    )


@router.put("/{name}")
async def update_skill(name: str, body: dict):
    """Update a custom skill's description/content/slash in place on disk.

    The change is written back to the skill's source file and the live registry
    is re-discovered so it takes effect immediately. Base and packaged skills are
    read-only and are rejected with a 409.
    """
    registry = _discover_registry()
    skill = registry.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    if not registry.is_user_owned(name):
        raise _not_user_owned(registry, name, "edited")

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
    _refresh_live_registry()
    return {"ok": True, "path": str(path)}


@router.delete("/{name}")
async def delete_skill(name: str):
    """Delete a custom skill's source file from the workspace.

    Base and packaged skills are not the user's to remove and are rejected with
    a 409 rather than deleting a shipped file.
    """
    registry = _discover_registry()
    if not registry.get_skill(name):
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    if not registry.is_user_owned(name):
        raise _not_user_owned(registry, name, "deleted")

    path = registry.remove_skill(name)
    if path is None:
        raise HTTPException(status_code=409,
                            detail=f"Skill '{name}' is not backed by a workspace file")

    _refresh_live_registry()
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
    registry = _discover_registry()
    try:
        result = registry.import_skills(manifest)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Refresh the live registry so subsequent sessions/tools see the imports.
    _refresh_live_registry()
    return {"ok": True, **result}


@router.post("/{name}/promote")
async def promote_skill(name: str):
    """Promote a custom skill into the shipped base directory."""
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

    _refresh_live_registry()
    return {"ok": True, "path": target}
