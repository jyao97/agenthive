"""Auto-generate CLAUDE.md and PROGRESS.md for managed projects.

Scans a project directory to infer tech stack, directory structure,
key paths, and verification commands, then writes templated files.

Design: CLAUDE.md stays under 50 lines — a one-screen rule sheet.
Detailed project docs overflow to README.md automatically.
"""

import json
import logging
import os
import re

logger = logging.getLogger("orchestrator.scaffolder")

TEMPLATE_HEADER = "> Read this file at the start of every task"

MAX_CLAUDE_LINES = 60  # warn if generated CLAUDE.md exceeds this
MAX_PROJECT_RULES_LINES = 20  # overflow to README.md if exceeded

IGNORED_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".next",
    "dist", "build", ".cache", ".tox", ".mypy_cache", ".pytest_cache",
    ".eggs", "target", "coverage", ".turbo", ".output", ".nuxt",
    ".claude", "wandb", "checkpoints", "logs", "data", ".idea",
    "backups", "uploads",
}


def _needs_scaffold(filepath: str) -> bool:
    """Return True if the file is missing or doesn't match the template header."""
    if not os.path.isfile(filepath):
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(5):
                line = f.readline()
                if TEMPLATE_HEADER in line:
                    return False
    except OSError:
        return True
    return True


def _top_dirs(project_path: str) -> str:
    """List top-level directories only (one-liner style)."""
    try:
        entries = sorted(os.listdir(project_path))
    except OSError:
        return ""
    dirs = [e + "/" for e in entries
            if os.path.isdir(os.path.join(project_path, e))
            and e not in IGNORED_DIRS and not e.startswith(".")]
    return ", ".join(dirs) if dirs else "N/A"


def _detect_tech_stack(project_path: str) -> str:
    """Infer tech stack from project files."""
    indicators = []

    pkg_json = os.path.join(project_path, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json) as f:
                pkg = json.load(f)
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "react" in deps:
                indicators.append("React")
            if "next" in deps:
                indicators.append("Next.js")
            if "vue" in deps:
                indicators.append("Vue")
            if "typescript" in deps or os.path.isfile(os.path.join(project_path, "tsconfig.json")):
                indicators.append("TypeScript")
            elif not any(x in indicators for x in ("React", "Next.js", "Vue")):
                indicators.append("JavaScript/Node.js")
            if "vite" in deps:
                indicators.append("Vite")
            if "tailwindcss" in deps:
                indicators.append("TailwindCSS")
        except (json.JSONDecodeError, OSError):
            indicators.append("Node.js")

    if os.path.isfile(os.path.join(project_path, "requirements.txt")):
        indicators.append("Python")
        try:
            with open(os.path.join(project_path, "requirements.txt")) as f:
                reqs = f.read().lower()
            if "torch" in reqs:
                indicators.append("PyTorch")
            if "tensorflow" in reqs:
                indicators.append("TensorFlow")
            if "fastapi" in reqs:
                indicators.append("FastAPI")
            if "django" in reqs:
                indicators.append("Django")
            if "flask" in reqs:
                indicators.append("Flask")
            if "ros" in reqs or "rospy" in reqs:
                indicators.append("ROS")
        except OSError:
            logger.debug("Failed to read requirements.txt in %s", project_path, exc_info=True)

    pyproject = os.path.join(project_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        if "Python" not in indicators:
            indicators.append("Python")
        try:
            with open(pyproject) as f:
                content = f.read().lower()
            if "torch" in content:
                indicators.append("PyTorch")
            if "fastapi" in content:
                indicators.append("FastAPI")
        except OSError:
            logger.debug("Failed to read pyproject.toml in %s", project_path, exc_info=True)

    if os.path.isfile(os.path.join(project_path, "Cargo.toml")):
        indicators.append("Rust")

    if os.path.isfile(os.path.join(project_path, "go.mod")):
        indicators.append("Go")

    gemfile = os.path.join(project_path, "Gemfile")
    if os.path.isfile(gemfile):
        indicators.append("Ruby")
        try:
            with open(gemfile) as f:
                content = f.read().lower()
            if "jekyll" in content:
                indicators.append("Jekyll")
            if "rails" in content:
                indicators.append("Rails")
        except OSError:
            logger.debug("Failed to read Gemfile in %s", project_path, exc_info=True)

    if os.path.isfile(os.path.join(project_path, "_config.yml")):
        if "Jekyll" not in indicators:
            indicators.append("Jekyll")

    if os.path.isfile(os.path.join(project_path, "CMakeLists.txt")):
        indicators.append("C/C++ (CMake)")

    # Fallback: scan file extensions
    if not indicators:
        exts = set()
        for f in os.listdir(project_path):
            _, ext = os.path.splitext(f)
            if ext:
                exts.add(ext)
        ext_map = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".rs": "Rust", ".go": "Go", ".java": "Java",
            ".cpp": "C++", ".c": "C", ".rb": "Ruby",
        }
        for ext, lang in ext_map.items():
            if ext in exts:
                indicators.append(lang)

    return ", ".join(dict.fromkeys(indicators)) if indicators else "Unknown"


def _detect_key_paths(project_path: str) -> dict:
    """Find config, entry point, and test paths."""
    result = {"config": "N/A", "entry_point": "N/A", "tests": "N/A"}

    config_candidates = [
        "package.json", "pyproject.toml", "setup.cfg", "setup.py",
        "Cargo.toml", "go.mod", "Gemfile", "_config.yml",
        "config.yaml", "config.yml", "config.json",
        ".env", "docker-compose.yml",
    ]
    found_configs = [c for c in config_candidates
                     if os.path.isfile(os.path.join(project_path, c))]
    if found_configs:
        result["config"] = ", ".join(found_configs[:3])

    entry_candidates = [
        "src/main.py", "main.py", "app.py", "server.py",
        "src/index.ts", "src/index.js", "src/main.tsx", "src/main.jsx",
        "src/App.tsx", "src/App.jsx", "index.js", "index.ts",
        "src/main.rs", "src/lib.rs", "cmd/main.go", "main.go",
        "manage.py", "wsgi.py",
    ]
    for ep in entry_candidates:
        if os.path.isfile(os.path.join(project_path, ep)):
            result["entry_point"] = ep
            break

    test_candidates = ["tests/", "test/", "spec/", "__tests__/",
                       "src/tests/", "src/__tests__/"]
    for td in test_candidates:
        if os.path.isdir(os.path.join(project_path, td.rstrip("/"))):
            result["tests"] = td
            break

    if result["tests"] == "N/A":
        test_files = [f for f in os.listdir(project_path)
                      if f.startswith("test_") or f.endswith("_test.py")
                      or f.endswith(".test.js") or f.endswith(".test.ts")
                      or f.endswith(".spec.js") or f.endswith(".spec.ts")]
        if test_files:
            result["tests"] = ", ".join(test_files[:3])

    return result


def _detect_commands(project_path: str) -> dict:
    """Detect build/test/lint commands."""
    result = {"build": "N/A", "test": "N/A", "lint": "N/A"}

    pkg_json = os.path.join(project_path, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json) as f:
                pkg = json.load(f)
            scripts = pkg.get("scripts", {})
            if "build" in scripts:
                result["build"] = f"`npm run build`"
            if "test" in scripts:
                result["test"] = f"`npm test`"
            if "lint" in scripts:
                result["lint"] = f"`npm run lint`"
        except (json.JSONDecodeError, OSError):
            logger.debug("Failed to read package.json scripts in %s", project_path, exc_info=True)

    makefile = os.path.join(project_path, "Makefile")
    if os.path.isfile(makefile):
        try:
            with open(makefile) as f:
                content = f.read()
            for target in ("build", "test", "lint"):
                if f"\n{target}:" in content or content.startswith(f"{target}:"):
                    if result[target] == "N/A":
                        result[target] = f"`make {target}`"
        except OSError:
            logger.debug("Failed to read Makefile in %s", project_path, exc_info=True)

    pyproject = os.path.join(project_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject) as f:
                content = f.read()
            if "pytest" in content.lower() and result["test"] == "N/A":
                result["test"] = "`pytest`"
            if "black" in content.lower() or "ruff" in content.lower():
                if result["lint"] == "N/A":
                    result["lint"] = "`ruff check .`" if "ruff" in content.lower() else "`black --check .`"
        except OSError:
            logger.debug("Failed to read pyproject.toml commands in %s", project_path, exc_info=True)

    if os.path.isfile(os.path.join(project_path, "Gemfile")):
        if result["build"] == "N/A":
            result["build"] = "`bundle exec jekyll build`"
        if result["test"] == "N/A":
            result["test"] = "`bundle exec jekyll serve` (manual)"

    return result


def _read_existing_content(filepath: str) -> str | None:
    """Read existing file content if it exists and doesn't already have the template header."""
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
        if not content:
            return None
        if TEMPLATE_HEADER in content:
            return None
        return content
    except OSError:
        return None


def _extract_project_rules(claude_path: str) -> str:
    """Extract only the ## Project-Specific Rules section from an existing scaffolded CLAUDE.md.

    Returns the content after the header, or empty string if not found.
    """
    if not os.path.isfile(claude_path):
        return ""
    try:
        with open(claude_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""

    # Only extract from already-scaffolded files
    if TEMPLATE_HEADER not in text:
        return ""

    m = re.search(r"^## Project-Specific Rules\s*\n", text, re.MULTILINE)
    if not m:
        return ""
    content = text[m.end():].strip()
    return content


def _overflow_to_readme(project_path: str, project_name: str, overflow: str) -> None:
    """Move overflow content from CLAUDE.md into README.md.

    Creates README.md if missing. Appends under a clearly marked section.
    """
    readme_path = os.path.join(project_path, "README.md")
    marker = "<!-- AUTO-GENERATED: Project details from CLAUDE.md scaffold -->"

    if os.path.isfile(readme_path):
        try:
            with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
                existing = f.read()
        except OSError:
            existing = ""

        # If marker already exists, replace that section
        if marker in existing:
            # Find the marker and replace everything after it until EOF or next marker
            idx = existing.index(marker)
            existing = existing[:idx].rstrip()

        # Append overflow
        content = existing.rstrip() + f"\n\n{marker}\n\n{overflow}\n"
    else:
        content = f"# {project_name}\n\n{marker}\n\n{overflow}\n"

    try:
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Wrote overflow project docs to README.md for '%s'", project_name)
    except OSError as e:
        logger.error("Failed to write README.md for '%s': %s", project_name, e)


def scaffold_project(project_name: str, project_path: str,
                     force: bool = False) -> dict:
    """Generate CLAUDE.md and PROGRESS.md for a project.

    If force=True, regenerate even if the template header already exists
    (preserves project-specific rules from the existing file).

    Returns dict with 'claude_md' and 'progress_md' booleans indicating
    which files were created/updated.
    """
    if not os.path.isdir(project_path):
        logger.warning("Project path does not exist: %s", project_path)
        return {"claude_md": False, "progress_md": False}

    result = {"claude_md": False, "progress_md": False}

    claude_path = os.path.join(project_path, "CLAUDE.md")
    progress_path = os.path.join(project_path, "PROGRESS.md")

    # --- CLAUDE.md ---
    needs = _needs_scaffold(claude_path) or force
    if needs:
        tech_stack = _detect_tech_stack(project_path)
        top_dirs = _top_dirs(project_path)
        key_paths = _detect_key_paths(project_path)
        commands = _detect_commands(project_path)

        # Collect project-specific rules
        # For force-regeneration: extract rules from existing scaffolded file
        project_rules = ""
        if force:
            project_rules = _extract_project_rules(claude_path)
        if not project_rules:
            # Fallback: read raw non-scaffolded content
            existing_claude = _read_existing_content(claude_path)
            if existing_claude:
                project_rules = existing_claude.strip()

        # If rules are long, overflow to README.md
        rules_lines = project_rules.split("\n") if project_rules else []
        if len(rules_lines) > MAX_PROJECT_RULES_LINES and project_rules:
            _overflow_to_readme(project_path, project_name, project_rules)
            project_rules = "See README.md for detailed project documentation."

        # Build short project-specific section
        rules_section = project_rules if project_rules else ""

        claude_content = f"""# CLAUDE.md
{TEMPLATE_HEADER}. Rarely modified — only update when project structure or conventions change.

## Universal Rules
- Think from first principles. Don't assume the user knows exactly what they want or the best way to get it. Start from the original requirement, question the approach, and suggest a better path if one exists
- Think step by step. Investigate before coding — read relevant code, trace the full flow, print findings before proposing a fix
- When a task is complex, break it into sub-tasks and spawn sub-agents to work in parallel
- Never guess. If unsure, read the code, check logs, or run a test first
- Every task must produce a visual verification artifact (screenshot, plot, diff, rendered output)
- If the goal or motivation is unclear, stop and discuss before writing code. If the goal is clear but the path isn't optimal, say so and suggest the better approach

## Do NOT
- Do not refactor or rename files unless the task explicitly requires it
- Do not delete or modify tests unless asked
- Do not change dependencies/package versions without explicit approval
- Do not modify CLAUDE.md
- Do not write to memory files (.claude/memory/, MEMORY.md) — only the orchestrator manages persistent memory

## Output Rules
- Keep responses concise — no long explanations unless asked
- For large outputs (logs, data), write to a file instead of printing to stdout
- Truncate error logs to the relevant section, don't paste entire stack traces

## Git Conventions
- Commit message format: `[scope] brief description` (e.g. `[frontend] fix image zoom gesture`)
- Commit frequently — small atomic commits, not one giant commit at the end
- Commit to master directly when appropriate

## Concurrency Rules
- Check which files other agents are currently modifying before editing shared files
- Prefer creating new files over modifying existing shared ones when possible

## Code Style
- Follow existing patterns in the codebase — don't introduce new conventions
- Match the indentation, naming, and structure of surrounding code

## Project: {project_name}
- Tech Stack: {tech_stack}
- Top Dirs: {top_dirs}
- Config: {key_paths['config']}
- Entry: {key_paths['entry_point']}
- Tests: {key_paths['tests']}
- Build: {commands['build']}  |  Test: {commands['test']}  |  Lint: {commands['lint']}

## Project-Specific Rules
{rules_section}
"""

        # Trim trailing whitespace
        claude_content = claude_content.rstrip() + "\n"

        line_count = len(claude_content.split("\n"))
        if line_count > MAX_CLAUDE_LINES:
            logger.warning(
                "CLAUDE.md for '%s' is %d lines (max %d). Consider trimming project-specific rules.",
                project_name, line_count, MAX_CLAUDE_LINES,
            )

        try:
            with open(claude_path, "w", encoding="utf-8") as f:
                f.write(claude_content)
            result["claude_md"] = True
            logger.info("Created CLAUDE.md for project '%s' (%d lines)", project_name, line_count)
        except OSError as e:
            logger.error("Failed to write CLAUDE.md for '%s': %s", project_name, e)

    # --- PROGRESS.md ---
    if _needs_scaffold(progress_path):
        existing_progress = _read_existing_content(progress_path)
        existing_entries = ""
        if existing_progress:
            match = re.search(r"^(##?#?\s*\[?\d{4}-\d{2}-\d{2})", existing_progress, re.MULTILINE)
            if match:
                entries = existing_progress[match.start():]
                entries = re.sub(
                    r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*(.+?)(?:\s*\|\s*Project:\s*\S+)?\s*$",
                    r"### \1 | Task: \2 | Status: success",
                    entries,
                    flags=re.MULTILINE,
                )
                entries = re.sub(r"\n---\n", "\n", entries)
                existing_entries = f"\n{entries}\n"
            else:
                existing_entries = f"\n{existing_progress}\n"

        progress_content = f"""# PROGRESS.md
{TEMPLATE_HEADER}. Append only, never delete entries.
> Updated when tasks complete — contains what worked, what failed, and why.

## {project_name} — Lessons Learned

<!-- Entry format:
### YYYY-MM-DD | Task: {{title}} | Status: success/abandoned
- What: (one line summary)
- Attempts: (what was tried)
- Resolution: (what finally worked)
- Lesson: (what future agents should know)
-->
{existing_entries}"""

        try:
            with open(progress_path, "w", encoding="utf-8") as f:
                f.write(progress_content)
            result["progress_md"] = True
            logger.info("Created PROGRESS.md for project '%s'", project_name)
        except OSError as e:
            logger.error("Failed to write PROGRESS.md for '%s': %s", project_name, e)

    return result


def trim_existing_claude(project_name: str, project_path: str) -> bool:
    """Trim an existing scaffolded CLAUDE.md that is over MAX_CLAUDE_LINES.

    Extracts the ## Project-Specific Rules section, and if it's too long,
    moves it to README.md and replaces with a pointer.

    Returns True if trimmed.
    """
    claude_path = os.path.join(project_path, "CLAUDE.md")
    if not os.path.isfile(claude_path):
        return False

    try:
        with open(claude_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False

    lines = text.split("\n")
    if len(lines) <= MAX_CLAUDE_LINES:
        return False

    # Find the ## Project-Specific Rules section
    m = re.search(r"^## Project-Specific Rules\s*\n", text, re.MULTILINE)
    if not m:
        return False

    before = text[:m.end()]
    overflow = text[m.end():].strip()

    if not overflow:
        return False

    # Move overflow to README.md
    _overflow_to_readme(project_path, project_name, overflow)

    # Replace in CLAUDE.md
    trimmed = before + "See README.md for detailed project documentation.\n"
    try:
        with open(claude_path, "w", encoding="utf-8") as f:
            f.write(trimmed)
        logger.info("Trimmed CLAUDE.md for '%s': %d -> %d lines",
                     project_name, len(lines), len(trimmed.split("\n")))
        return True
    except OSError as e:
        logger.error("Failed to trim CLAUDE.md for '%s': %s", project_name, e)
        return False


def backfill_all_projects(registry_path: str) -> list[dict]:
    """Backfill CLAUDE.md and PROGRESS.md for all projects in the registry.

    Also trims existing CLAUDE.md files that are over MAX_CLAUDE_LINES.
    Returns list of results per project.
    """
    import yaml

    if not os.path.isfile(registry_path):
        logger.warning("Registry file not found: %s", registry_path)
        return []

    with open(registry_path) as f:
        data = yaml.safe_load(f) or {}

    projects = data.get("projects", []) or []
    results = []

    for proj in projects:
        name = proj.get("name", "")
        path = proj.get("path", "")
        if not name or not path:
            continue
        if not os.path.isdir(path):
            logger.info("Skipping '%s' — path does not exist: %s", name, path)
            continue

        # Force-regenerate all CLAUDE.md to use new compact template
        r = scaffold_project(name, path, force=True)
        r["project"] = name
        results.append(r)

    return results
