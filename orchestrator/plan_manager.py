"""Plan Manager — plan generation and approval workflow."""

import logging
import shlex

from config import CLAUDE_CODE_OAUTH_TOKEN, HOST_USER_UID, WORKER_CPU_LIMIT, WORKER_IMAGE, WORKER_MEM_LIMIT, WORKER_NETWORK
from models import Project, Task

logger = logging.getLogger("orchestrator.plan")

PLAN_PROMPT_TEMPLATE = """You are a task planner for project: {project_name}
Project path: /projects/{project_path}

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

    def start_planning(self, task: Task, project: Project) -> str:
        """Start a planning worker container. Returns container_id."""
        import os

        prompt = PLAN_PROMPT_TEMPLATE.format(
            project_name=project.display_name,
            project_path=project.name,
            task_prompt=task.prompt,
        )
        escaped_prompt = shlex.quote(prompt)
        container_name = f"cc-planner-{task.id}"

        # Clean up leftover
        import docker.errors
        try:
            old = self.worker_mgr.docker_client.containers.get(container_name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass

        volumes = {
            "cc-projects": {"bind": "/projects", "mode": "ro"},  # Read-only for planning
        }

        setup_cmds = (
            "git config --global user.name 'CC Worker' && "
            "git config --global user.email 'cc-worker@localhost'"
        )

        env = {"HOME": "/worker-home"}
        if CLAUDE_CODE_OAUTH_TOKEN:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = CLAUDE_CODE_OAUTH_TOKEN

        container = self.worker_mgr.docker_client.containers.run(
            image=WORKER_IMAGE,
            entrypoint=["bash", "-c"],
            command=[
                f"{setup_cmds} && "
                f"cd /projects/{project.name} && "
                f"claude -p {escaped_prompt} "
                f"--dangerously-skip-permissions "
                f"--output-format stream-json --verbose"
            ],
            volumes=volumes,
            working_dir=f"/projects/{project.name}",
            environment=env,
            tmpfs={"/worker-home": f"uid={HOST_USER_UID},gid={HOST_USER_UID}"},
            user=f"{HOST_USER_UID}:{HOST_USER_UID}",
            cpu_quota=int(WORKER_CPU_LIMIT * 100000),
            mem_limit=WORKER_MEM_LIMIT,
            network=WORKER_NETWORK,
            auto_remove=False,
            detach=True,
            name=container_name,
        )
        logger.info("Started planner %s for task %s", container_name, task.id)
        return container.id

    @staticmethod
    def extract_plan(logs: str) -> str:
        """Extract the plan text from worker output."""
        import json
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
