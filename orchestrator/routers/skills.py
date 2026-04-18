"""Skill enumeration — exposes the picker's list of user-invocable skills.

Thin layer over `skills.list_skills`. The project name is optional; when
provided we resolve it to a path so project-local `.claude/skills/<name>/`
folders are included alongside personal/plugin/bundled sources.
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Project
from skills import list_skills

logger = logging.getLogger("orchestrator")

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
def get_skills(project: str | None = Query(None), db: Session = Depends(get_db)):
    project_path: str | None = None
    if project:
        proj = db.get(Project, project)
        if proj:
            project_path = proj.path
    return {"skills": list_skills(project_path=project_path)}
