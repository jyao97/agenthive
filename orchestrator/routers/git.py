"""Git routes — log, status, branches, worktrees, merge, checkout."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import Project

router = APIRouter(prefix="/api/git", tags=["git"])


@router.get("/{project}/log")
async def git_log(project: str, request: Request, limit: int = 30, db: Session = Depends(get_db)):
    """Get recent git commits for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_log(proj.path, limit=limit)


@router.get("/{project}/status")
async def git_status(project: str, request: Request, db: Session = Depends(get_db)):
    """Get git status (staged, unstaged, untracked) for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_status(proj.path)


@router.get("/{project}/branches")
async def git_branches(project: str, request: Request, db: Session = Depends(get_db)):
    """Get branches for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_branches(proj.path)


@router.get("/{project}/worktrees")
async def git_worktrees(project: str, request: Request, db: Session = Depends(get_db)):
    """List git worktrees for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_worktrees(proj.path)


@router.post("/{project}/push")
async def git_push(project: str, request: Request, db: Session = Depends(get_db)):
    """Push current branch to origin."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    branch = gm.get_current_branch(proj.path)
    result = gm.push(proj.path, branch)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("error", "Push failed"))
    return result


@router.post("/{project}/merge/{branch:path}")
async def git_merge(project: str, branch: str, request: Request, db: Session = Depends(get_db)):
    """Merge a branch into the current branch for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    result = gm.merge_branch(proj.path, branch)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/{project}/checkout/{branch:path}")
async def git_checkout(project: str, branch: str, request: Request, db: Session = Depends(get_db)):
    """Checkout a branch for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    result = gm.checkout(proj.path, branch)
    if result.startswith("ERROR:"):
        raise HTTPException(status_code=409, detail=result)
    return {"success": True, "branch": branch, "message": result}
