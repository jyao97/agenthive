"""Claude Code skills — discovery, JSONL parsing helpers, source taxonomy.

Single home for all skill-related logic so jsonl_parser, routers/skills,
and slash_commands stay independent. If Claude Code changes how skills
are stored or invoked, only this file needs to track it.

Skill sources scanned by `list_skills`:
  personal   — ~/.claude/skills/<name>/SKILL.md
  project    — <project>/.claude/skills/<name>/SKILL.md
  plugin     — ~/.claude/plugins/*/skills/<name>/SKILL.md
  bundled    — hardcoded list of skills that ship with Claude Code CLI

Frontmatter rule: skills with `user-invocable: false` are filtered out
(model-only — should not appear in the user-facing picker).
"""

import logging
import os
import re

import yaml

from config import CLAUDE_HOME

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bundled skills (ship with Claude Code; no on-disk SKILL.md to scan)
# Source: Claude Code "Slash Commands" reference, entries marked [Skill].
# ---------------------------------------------------------------------------

BUNDLED_SKILLS: list[dict] = [
    {"name": "batch", "description": "Run multiple agent tasks in batch"},
    {"name": "claude-api", "description": "Interact with Claude API directly"},
    {"name": "debug", "description": "Debug an issue"},
    {"name": "fewer-permission-prompts", "description": "Reduce permission prompt frequency"},
    {"name": "loop", "description": "Run a repeating loop task"},
    {"name": "simplify", "description": "Simplify code"},
]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

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


def _read_skill_file(skill_path: str) -> dict | None:
    """Return {name, description} for a SKILL.md, or None if filtered out."""
    try:
        with open(skill_path, "r", errors="replace") as f:
            head = f.read(8192)
    except OSError as e:
        logger.debug("Skipping unreadable skill %s: %s", skill_path, e)
        return None
    fm = _parse_frontmatter(head)
    if fm.get("user-invocable") is False:
        return None
    return {
        "name": str(fm.get("name") or os.path.basename(os.path.dirname(skill_path))),
        "description": str(fm.get("description", "")).strip(),
    }


def _scan_skill_dir(skills_dir: str, source: str) -> list[dict]:
    """List all SKILL.md children of `skills_dir`, tagged with `source`."""
    out: list[dict] = []
    if not os.path.isdir(skills_dir):
        return out
    try:
        entries = sorted(os.listdir(skills_dir))
    except OSError as e:
        logger.warning("Failed to list %s: %s", skills_dir, e)
        return out
    for name in entries:
        skill_path = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(skill_path):
            continue
        info = _read_skill_file(skill_path)
        if info is None:
            continue
        info["source"] = source
        info["path"] = skill_path
        out.append(info)
    return out


def list_skills(project_path: str | None = None) -> list[dict]:
    """Enumerate skills from all known sources.

    Returns: list of {name, description, source, path?} dicts.
    Skills with `user-invocable: false` are filtered out.

    Source precedence (first wins on name collision):
      personal > project > plugin > command > bundled
    """
    skills: list[dict] = []
    seen: set[str] = set()

    def _add(entry: dict) -> None:
        name = entry.get("name")
        if not name or name in seen:
            return
        skills.append(entry)
        seen.add(name)

    # Personal
    for s in _scan_skill_dir(os.path.join(CLAUDE_HOME, "skills"), "personal"):
        _add(s)

    # Project
    if project_path:
        for s in _scan_skill_dir(
            os.path.join(project_path, ".claude", "skills"), "project",
        ):
            _add(s)

    # Plugin (one-level glob)
    plugins_root = os.path.join(CLAUDE_HOME, "plugins")
    if os.path.isdir(plugins_root):
        try:
            for plugin_name in sorted(os.listdir(plugins_root)):
                plugin_skills_dir = os.path.join(plugins_root, plugin_name, "skills")
                if os.path.isdir(plugin_skills_dir):
                    for s in _scan_skill_dir(
                        plugin_skills_dir, f"plugin:{plugin_name}",
                    ):
                        _add(s)
        except OSError as e:
            logger.warning("Failed to enumerate plugins: %s", e)

    # Built-in slash commands with known lifecycle (steers users away from
    # KNOWN_PROBLEMATIC ones — anything in COMMANDS is guaranteed safe to
    # send through the orchestrator).
    from slash_commands import COMMANDS
    for cmd, cfg in COMMANDS.items():
        _add({
            "name": cmd.lstrip("/"),
            "description": cfg.description,
            "source": "command",
        })

    # Bundled CLI skills (no on-disk SKILL.md)
    for b in BUNDLED_SKILLS:
        _add({**b, "source": "bundled"})

    return skills


# ---------------------------------------------------------------------------
# JSONL parsing helpers (consumed by jsonl_parser)
# ---------------------------------------------------------------------------

def is_hidden_meta_entry(entry: dict) -> bool:
    """True if a user JSONL entry is a hidden injection (skill body, system
    reminder, etc.) that should be dropped from the visible turn stream.

    All `isMeta` entries are dropped: the Skill tool_use turn that precedes
    a skill body already conveys the invocation; the body itself is on disk
    if a user wants to read it.
    """
    return bool(entry.get("isMeta"))


def format_skill_summary(skill_input: dict) -> str:
    """Display string for a `Skill` tool_use turn."""
    return f"> `Skill` {skill_input.get('skill', '')}"


def skill_turn_metadata(skill_input: dict) -> dict:
    """Extra metadata to attach to a `Skill` tool_use turn."""
    return {"skill_name": skill_input.get("skill", "")}
