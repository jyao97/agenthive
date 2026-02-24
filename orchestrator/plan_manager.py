"""Plan Manager — plan generation and approval workflow."""

import json
import logging
import os
import subprocess
import uuid

from config import CLAUDE_BIN, PROJECTS_DIR
from models import Project, Task

logger = logging.getLogger("orchestrator.plan")

PLAN_PROMPT_TEMPLATE = """You are a task planner for project: {project_name}
Project path: {project_path}

First read the project's CLAUDE.md to understand the codebase.

Analyze the following task and output an execution plan.
Do NOT make any code changes. Do NOT run any commands that modify files.

Task:
{task_prompt}

Output your plan in this exact format:

## Files to Modify
- list each file path and what changes are needed

## Change Summary
Brief description of the overall approach

## Complexity Estimate
Low / Medium / High — with brief justification

## Risks
- potential issues or side effects

## Test Strategy
- how to verify the changes work

After outputting the plan, output EXIT_SUCCESS"""


class PlanManager:
    """Handles plan generation for tasks before execution."""

    def __init__(self, worker_manager):
        self.worker_mgr = worker_manager

    def _get_project_path(self, project_name: str) -> str:
        if PROJECTS_DIR:
            return os.path.join(PROJECTS_DIR, project_name)
        return os.path.join("/projects", project_name)

    def start_planning(self, task: Task, project: Project) -> str:
        """Start a planning subprocess. Returns PID string."""
        project_path = self._get_project_path(project.name)
        prompt = PLAN_PROMPT_TEMPLATE.format(
            project_name=project.display_name,
            project_path=project_path,
            task_prompt=task.prompt,
        )

        output_file = f"/tmp/claude-planner-{uuid.uuid4().hex[:8]}.log"

        # No --dangerously-skip-permissions: the planner is read-only
        cmd = [
            CLAUDE_BIN, "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
        ]

        with open(output_file, "w") as out_f:
            process = subprocess.Popen(
                cmd,
                cwd=project_path,
                stdout=out_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        pid_str = str(process.pid)

        # Track in worker manager so it can be monitored/stopped
        self.worker_mgr._processes[pid_str] = {
            "process": process,
            "output_file": output_file,
            "project": project.name,
            "type": "planner",
        }

        logger.info("Started planner PID %s for task %s", pid_str, task.id)
        return pid_str

    @staticmethod
    def extract_plan(logs: str) -> str:
        """Extract the plan text from worker output."""
        plan_parts = []
        for line in logs.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                # Look for assistant text content
                if event.get("type") == "assistant" and "message" in event:
                    msg = event["message"]
                    if isinstance(msg, dict):
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "text":
                                plan_parts.append(block["text"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        if plan_parts:
            return "\n".join(plan_parts)

        # Fallback: return raw text between known markers
        text = logs
        for marker in ["## Files to Modify", "## Change Summary"]:
            if marker in text:
                idx = text.index(marker)
                return text[idx:].split("EXIT_SUCCESS")[0].strip()

        return "(Plan could not be extracted from worker output)"
