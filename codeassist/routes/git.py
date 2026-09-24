"""Git repository API routes."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/git/repos", tags=["git"])


@router.get("")
async def list_git_repos():
    """List all tracked Git repositories."""
    from codeassist.session import GitRepo

    from ..server import get_config
    cfg = get_config()
    if not cfg.git.enabled:
        return {"repos": []}
    return await GitRepo.list_all()
