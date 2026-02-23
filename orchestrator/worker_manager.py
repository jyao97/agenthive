"""Worker Manager — Docker container lifecycle for CC workers."""

import logging
import os
import shlex
from typing import AsyncGenerator

import docker
import docker.errors

from config import WORKER_CPU_LIMIT, WORKER_IMAGE, WORKER_MEM_LIMIT, WORKER_NETWORK
from models import Project, Task

logger = logging.getLogger("orchestrator.worker")


class WorkerManager:
    """Manages CC worker Docker containers."""

    def __init__(self):
        self.docker_client = docker.from_env()
        self._verify_image()

    def _verify_image(self):
        """Check that the worker image exists."""
        try:
            self.docker_client.images.get(WORKER_IMAGE)
            logger.info("Worker image '%s' found", WORKER_IMAGE)
        except docker.errors.ImageNotFound:
            logger.warning(
                "Worker image '%s' not found — build it with: "
                "docker build -t %s ./worker/",
                WORKER_IMAGE, WORKER_IMAGE,
            )

    def _build_prompt(self, task: Task, project: Project) -> str:
        """Wrap the user prompt with worker instructions."""
        return (
            f"You are working in project: {project.display_name}\n"
            f"Project path: /projects/{project.name}\n"
            f"\n"
            f"First read the project's CLAUDE.md to understand project conventions.\n"
            f"\n"
            f"Task:\n{task.prompt}\n"
            f"\n"
            f"When done:\n"
            f"1. git add + commit with message format: [task-{task.id}] short description\n"
            f"2. Append lessons learned to PROGRESS.md\n"
            f"3. Output EXIT_SUCCESS\n"
            f"\n"
            f"If you fail, output EXIT_FAILURE: reason"
        )

    def start_worker(self, task: Task, project: Project) -> str:
        """Start a worker container for a task. Returns container ID."""
        prompt = self._build_prompt(task, project)
        # Use shell-safe quoting for the prompt
        escaped_prompt = shlex.quote(prompt)

        container_name = f"cc-worker-{task.id}"

        # Clean up any leftover container with the same name
        try:
            old = self.docker_client.containers.get(container_name)
            old.remove(force=True)
            logger.warning("Removed leftover container %s", container_name)
        except docker.errors.NotFound:
            pass

        container = self.docker_client.containers.run(
            image=WORKER_IMAGE,
            entrypoint=["bash", "-c"],
            command=[
                f"cd /projects/{project.name} && "
                f"claude -p {escaped_prompt} "
                f"--dangerously-skip-permissions "
                f"--output-format stream-json --verbose"
            ],
            volumes={
                "cc-projects": {"bind": "/projects", "mode": "rw"},
                "cc-git-bare": {"bind": "/git-bare", "mode": "rw"},
                os.path.expanduser("~/.claude"): {
                    "bind": "/home/ccworker/.claude",
                    "mode": "ro",
                },
            },
            working_dir=f"/projects/{project.name}",
            cpu_quota=int(WORKER_CPU_LIMIT * 100000),
            mem_limit=WORKER_MEM_LIMIT,
            network=WORKER_NETWORK,
            auto_remove=False,  # Keep container to read logs after exit
            detach=True,
            name=container_name,
        )
        logger.info(
            "Started worker %s for task %s (project: %s)",
            container_name, task.id, project.name,
        )
        return container.id

    def get_status(self, container_id: str) -> str:
        """Get container status: running / exited / error / removed."""
        try:
            container = self.docker_client.containers.get(container_id)
            return container.status
        except docker.errors.NotFound:
            return "removed"

    def get_logs(self, container_id: str, tail: int = 0) -> str:
        """Get container logs. tail=0 means all logs."""
        try:
            container = self.docker_client.containers.get(container_id)
            kwargs = {"stdout": True, "stderr": True}
            if tail > 0:
                kwargs["tail"] = tail
            return container.logs(**kwargs).decode("utf-8", errors="replace")
        except docker.errors.NotFound:
            return ""

    def stream_logs(self, container_id: str) -> AsyncGenerator[str, None]:
        """Stream container logs line by line."""
        try:
            container = self.docker_client.containers.get(container_id)
            for chunk in container.logs(stream=True, follow=True):
                yield chunk.decode("utf-8", errors="replace")
        except docker.errors.NotFound:
            return

    def stop_worker(self, container_id: str):
        """Stop and remove a worker container."""
        try:
            container = self.docker_client.containers.get(container_id)
            if container.status == "running":
                container.stop(timeout=10)
            container.remove(force=True)
            logger.info("Stopped and removed container %s", container_id[:12])
        except docker.errors.NotFound:
            logger.debug("Container %s already removed", container_id[:12])

    def cleanup_exited(self):
        """Remove all exited cc-worker containers."""
        containers = self.docker_client.containers.list(
            all=True,
            filters={"name": "cc-worker-", "status": "exited"},
        )
        for c in containers:
            try:
                c.remove()
                logger.debug("Cleaned up exited container %s", c.name)
            except docker.errors.APIError:
                pass
        if containers:
            logger.info("Cleaned up %d exited worker containers", len(containers))

    def list_workers(self) -> list[dict]:
        """List all cc-worker containers with their status."""
        containers = self.docker_client.containers.list(
            all=True,
            filters={"name": "cc-worker-"},
        )
        return [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "created": c.attrs.get("Created", ""),
            }
            for c in containers
        ]

    def ping(self) -> bool:
        """Check if Docker daemon is reachable."""
        try:
            self.docker_client.ping()
            return True
        except Exception:
            return False
