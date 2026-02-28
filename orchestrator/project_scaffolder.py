"""Auto-generate CLAUDE.md and PROGRESS.md for managed projects.

Scans a project directory to infer tech stack, directory structure,
key paths, and verification commands, then writes templated files.
"""

import json
import logging
import os
import subprocess

logger = logging.getLogger("orchestrator.scaffolder")

TEMPLATE_HEADER = "> Read this file at the start of every task"

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
            # Check first 5 lines for the header marker
            for _ in range(5):
                line = f.readline()
                if TEMPLATE_HEADER in line:
                    return False
    except OSError:
        return True
    return True


def _dir_tree(project_path: str, max_depth: int = 2) -> str:
    """Generate directory tree (top N levels), ignoring common junk dirs."""
    lines = []
    base = os.path.basename(project_path.rstrip("/"))
    lines.append(f"{base}/")

    def _walk(path: str, prefix: str, depth: int):
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return
        dirs = [e for e in entries if os.path.isdir(os.path.join(path, e))
                and e not in IGNORED_DIRS and not e.startswith(".")]
        files = [e for e in entries if os.path.isfile(os.path.join(path, e))
                 and not e.startswith(".")]
        # At depth 0, show key files; at depth 1, just dirs
        items = []
        if depth == 0:
            key_files = [f for f in files if f in (
                "package.json", "requirements.txt", "pyproject.toml",
                "Cargo.toml", "go.mod", "Makefile", "Gemfile",
                "setup.py", "setup.cfg", "CMakeLists.txt",
                "docker-compose.yml", "Dockerfile",
            )]
            items = [(f, False) for f in key_files] + [(d, True) for d in dirs]
        else:
            items = [(d, True) for d in dirs]
        for i, (name, is_dir) in enumerate(items):
            connector = "└── " if i == len(items) - 1 else "├── "
            suffix = "/" if is_dir else ""
            lines.append(f"{prefix}{connector}{name}{suffix}")
            if is_dir:
                ext = "    " if i == len(items) - 1 else "│   "
                _walk(os.path.join(path, name), prefix + ext, depth + 1)

    _walk(project_path, "", 0)
    return "\n".join(lines)


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
            pass

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
            pass

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
            pass

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

    # Config files (priority order)
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

    # Entry points
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

    # Test directories / files
    test_candidates = ["tests/", "test/", "spec/", "__tests__/",
                       "src/tests/", "src/__tests__/"]
    for td in test_candidates:
        if os.path.isdir(os.path.join(project_path, td.rstrip("/"))):
            result["tests"] = td
            break

    # Check for test files at root
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
                result["build"] = f"`npm run build` ({scripts['build']})"
            if "test" in scripts:
                result["test"] = f"`npm test` ({scripts['test']})"
            if "lint" in scripts:
                result["lint"] = f"`npm run lint` ({scripts['lint']})"
        except (json.JSONDecodeError, OSError):
            pass

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
            pass

    pyproject = os.path.join(project_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject) as f:
                content = f.read()
            if "pytest" in content.lower() and result["test"] == "N/A":
                result["test"] = "`pytest`"
            if "black" in content.lower() or "ruff" in content.lower():
                if result["lint"] == "N/A":
                    result["lint"] = "`pre-commit run --all-files`" if os.path.isfile(
                        os.path.join(project_path, ".pre-commit-config.yaml")
                    ) else "`ruff check .`" if "ruff" in content.lower() else "`black --check .`"
        except OSError:
            pass

    # Jekyll
    if os.path.isfile(os.path.join(project_path, "Gemfile")):
        if result["build"] == "N/A":
            result["build"] = "`bundle exec jekyll build`"
        if result["test"] == "N/A":
            result["test"] = "`bundle exec jekyll serve` (manual)"

    return result


def _downgrade_headers(content: str) -> str:
    """Downgrade markdown headers so they nest under ## Project-Specific Rules.

    # H1 -> ### H3, ## H2 -> ### H3, ### H3 -> #### H4.
    Skips lines inside fenced code blocks (```).
    """
    lines = content.split("\n")
    in_code_block = False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if line.startswith("### "):
            lines[i] = "#" + line      # ### -> ####
        elif line.startswith("## "):
            lines[i] = "#" + line      # ## -> ###
        elif line.startswith("# "):
            lines[i] = "##" + line     # # -> ###
    return "\n".join(lines)


def _read_existing_content(filepath: str) -> str | None:
    """Read existing file content if it exists and doesn't already have the template header."""
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
        if not content:
            return None
        # If it already has the template header, don't preserve (it's our template)
        if TEMPLATE_HEADER in content:
            return None
        return content
    except OSError:
        return None


def scaffold_project(project_name: str, project_path: str) -> dict:
    """Generate CLAUDE.md and PROGRESS.md for a project.

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
    if _needs_scaffold(claude_path):
        tech_stack = _detect_tech_stack(project_path)
        dir_tree = _dir_tree(project_path)
        key_paths = _detect_key_paths(project_path)
        commands = _detect_commands(project_path)

        # Preserve existing content as project-specific rules
        # Downgrade headers so they nest properly under ## Project-Specific Rules
        existing_claude = _read_existing_content(claude_path)
        project_rules = ""
        if existing_claude:
            project_rules = f"\n{_downgrade_headers(existing_claude)}\n"

        claude_content = f"""# CLAUDE.md
{TEMPLATE_HEADER}. Rarely modified — only update when project structure or conventions change.

## Universal Rules
- Think step by step. Investigate before coding — read relevant code, trace the full flow, print findings before proposing a fix
- When a task is complex, break it into sub-tasks and spawn sub-agents to work in parallel
- Never guess. If unsure, read the code, check logs, or run a test first
- Every task must produce a visual verification artifact (screenshot, plot, diff, rendered output)

## Do NOT
- Do not refactor or rename files unless the task explicitly requires it
- Do not delete or modify tests unless asked
- Do not change dependencies/package versions without explicit approval
- Do not modify CLAUDE.md
- Never prompt for user confirmation — make your best judgment and proceed. If truly blocked, write the blocker to PROGRESS.md and exit

## Output Rules
- Keep responses concise — no long explanations unless asked
- For large outputs (logs, data), write to a file instead of printing to stdout
- Truncate error logs to the relevant section, don't paste entire stack traces

## Git Conventions
- Commit message format: `[scope] brief description` (e.g. `[frontend] fix image zoom gesture`)
- Commit frequently — small atomic commits, not one giant commit at the end
- Never commit to master directly, always work on assigned branch/worktree

## Concurrency Rules
- Check which files other agents are currently modifying before editing shared files
- Prefer creating new files over modifying existing shared ones when possible

## Code Style
- Follow existing patterns in the codebase — don't introduce new conventions
- Match the indentation, naming, and structure of surrounding code

## Project: {project_name}
## Tech Stack: {tech_stack}
## Directory Structure
```
{dir_tree}
```

## Key Paths
- Config: {key_paths['config']}
- Entry point: {key_paths['entry_point']}
- Tests: {key_paths['tests']}

## Verification Commands
- Build: {commands['build']}
- Test: {commands['test']}
- Lint: {commands['lint']}

## Project-Specific Rules
{project_rules}"""

        try:
            with open(claude_path, "w", encoding="utf-8") as f:
                f.write(claude_content)
            result["claude_md"] = True
            logger.info("Created CLAUDE.md for project '%s'", project_name)
        except OSError as e:
            logger.error("Failed to write CLAUDE.md for '%s': %s", project_name, e)

    # --- PROGRESS.md ---
    if _needs_scaffold(progress_path):
        existing_progress = _read_existing_content(progress_path)
        existing_entries = ""
        if existing_progress:
            # Strip old headers/instructions, keep only real entries.
            # Look for the first date-stamped entry (## [YYYY- or ### YYYY-)
            import re
            match = re.search(r"^(##?#?\s*\[?\d{4}-\d{2}-\d{2})", existing_progress, re.MULTILINE)
            if match:
                entries = existing_progress[match.start():]
                # Normalize entry headers to ### YYYY-MM-DD format
                entries = re.sub(
                    r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*(.+?)(?:\s*\|\s*Project:\s*\S+)?\s*$",
                    r"### \1 | Task: \2 | Status: success",
                    entries,
                    flags=re.MULTILINE,
                )
                # Remove --- separators between entries
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


def backfill_all_projects(registry_path: str) -> list[dict]:
    """Backfill CLAUDE.md and PROGRESS.md for all projects in the registry.

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

        r = scaffold_project(name, path)
        r["project"] = name
        results.append(r)

    return results
