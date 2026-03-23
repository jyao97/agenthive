"""Worker/process monitoring routes."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from database import get_db
from models import Project

router = APIRouter(tags=["workers"])


@router.get("/api/processes")
async def list_processes_endpoint(request: Request):
    """List running Claude processes (active agent execs)."""
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        return []
    return ad.get_active_processes()


@router.get("/api/workers")
async def list_tracked_processes(request: Request):
    """List all tracked Claude subprocess entries."""
    wm = getattr(request.app.state, "worker_manager", None)
    if not wm:
        return []
    return wm.list_workers()


@router.get("/api/projects/{project_name}/worktrees")
async def list_project_worktrees(project_name: str, db: Session = Depends(get_db)):
    """List git worktrees for a specific project."""
    proj = db.get(Project, project_name)
    if not proj:
        return []
    from git_manager import GitManager
    gm = GitManager()
    return gm.get_worktrees(proj.path)
