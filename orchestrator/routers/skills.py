"""Skill routes — enumerate Claude Code skills from ~/.claude/skills/."""

import logging
import os
import re

import yaml
from fastapi import APIRouter

from config import CLAUDE_HOME

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skills"])


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


@router.get("/api/skills")
async def list_skills():
    """List all skills in the user's default Claude skill directory.

    Reads frontmatter (name, description) from each ~/.claude/skills/<name>/SKILL.md.
    """
    skills_dir = os.path.join(CLAUDE_HOME, "skills")
    skills: list[dict] = []
    if not os.path.isdir(skills_dir):
        return {"skills": skills}
    try:
        entries = sorted(os.listdir(skills_dir))
    except OSError as e:
        logger.warning("Failed to list %s: %s", skills_dir, e)
        return {"skills": skills}
    for name in entries:
        skill_path = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(skill_path):
            continue
        try:
            with open(skill_path, "r", errors="replace") as f:
                head = f.read(8192)
        except OSError as e:
            logger.debug("Skipping unreadable skill %s: %s", skill_path, e)
            continue
        fm = _parse_frontmatter(head)
        skills.append({
            "name": str(fm.get("name") or name),
            "description": str(fm.get("description", "")).strip(),
            "path": skill_path,
        })
    return {"skills": skills}
