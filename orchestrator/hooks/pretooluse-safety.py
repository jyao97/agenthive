#!/usr/bin/env python3
"""PreToolUse safety hook: deterministic guardrails for Xylocopa agents.
Blocks dangerous shell commands and out-of-project file modifications
BEFORE they execute — replaces prompt-based safety rules with hard blocks.

Exit 0 with permissionDecision:"deny" -> block the tool call
Exit 0 with no output -> allow the tool call
"""

import json
import os
import re
import sys


def deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if not tool_name:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    cmd = tool_input.get("command", "")
    file_path = tool_input.get("file_path", "")
    cwd = data.get("cwd", "")

    if tool_name == "Bash":
        if not cmd:
            sys.exit(0)

        # --- git reset --hard (allowed only inside worktrees) ---
        if re.search(r"git\s+reset\s+--hard", cmd):
            if "/.claude/worktrees/" not in cwd:
                deny(
                    "BLOCKED: git reset --hard is prohibited outside worktrees. "
                    "Use a worktree for destructive resets."
                )

        # --- git clean -f (any flag combo containing f) ---
        if re.search(r"git\s+clean\s+.*-[a-zA-Z]*f", cmd):
            deny("BLOCKED: git clean -f permanently deletes untracked files.")

        # --- git checkout -- . / git restore . ---
        if re.search(r"git\s+(checkout\s+--\s+\.|restore\s+\.)", cmd):
            deny(
                "BLOCKED: git checkout -- . / git restore . discards all "
                "uncommitted changes."
            )

        # --- git push --force / -f ---
        if re.search(r"git\s+push\s+.*(-f\b|--force)", cmd):
            deny("BLOCKED: git push --force can overwrite remote history.")

        # --- rm -rf (any flag combo with both r and f) ---
        if re.search(
            r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b", cmd
        ):
            deny("BLOCKED: rm -rf is prohibited. Remove specific files instead.")

        # --- DROP TABLE / TRUNCATE ---
        if re.search(r"\b(DROP\s+TABLE|TRUNCATE)\b", cmd, re.IGNORECASE):
            deny(
                "BLOCKED: Destructive DB operations (DROP TABLE / TRUNCATE) "
                "are prohibited."
            )

    elif tool_name in ("Write", "Edit"):
        if not file_path or not cwd:
            sys.exit(0)

        # Block writes outside the project directory
        real_file = os.path.realpath(file_path)
        real_cwd = os.path.realpath(cwd)

        if not (real_file == real_cwd or real_file.startswith(real_cwd + "/")):
            deny(
                "BLOCKED: Cannot modify files outside the project directory "
                f"({cwd})."
            )

    sys.exit(0)


if __name__ == "__main__":
    main()
