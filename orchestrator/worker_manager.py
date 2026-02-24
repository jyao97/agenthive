"""Worker Manager — Docker container lifecycle for CC workers and agents."""

import logging
import shlex

import docker
import docker.errors

from config import (
    CLAUDE_CODE_OAUTH_TOKEN,
    HOST_PROJECTS_DIR,
    HOST_USER_UID,
    WORKER_CPU_LIMIT,
    WORKER_IMAGE,
    WORKER_MEM_LIMIT,
    WORKER_NETWORK,
)
from models import Agent, Project, Task

logger = logging.getLogger("orchestrator.worker")

# Git config applied at container startup (auth is handled via CLAUDE_CODE_OAUTH_TOKEN env var)
_SETUP_CMDS = (
    "git config --global user.name 'CC Worker' && "
    "git config --global user.email 'cc-worker@localhost' && "
    "git config --global init.defaultBranch main"
)


class WorkerManager:
    """Manages CC worker Docker containers (ephemeral tasks and persistent agents)."""

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

    def _projects_volume(self, mode="rw"):
        """Return the projects volume spec — host bind mount or named volume."""
        if HOST_PROJECTS_DIR:
            return {HOST_PROJECTS_DIR: {"bind": "/projects", "mode": mode}}
        return {"cc-projects": {"bind": "/projects", "mode": mode}}

    def _base_volumes(self, rw=True):
        """Build the common volumes dict."""
        mode = "rw" if rw else "ro"
        return {
            **self._projects_volume(mode),
            "cc-git-bare": {"bind": "/git-bare", "mode": mode},
        }

    def _worker_env(self):
        """Build the environment dict for worker containers."""
        env = {"HOME": "/worker-home"}
        if CLAUDE_CODE_OAUTH_TOKEN:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = CLAUDE_CODE_OAUTH_TOKEN
        else:
            logger.warning("CLAUDE_CODE_OAUTH_TOKEN not set — workers will not be authenticated")
        return env

    # =====================================================================
    # Ephemeral task workers (original one-shot behavior)
    # =====================================================================

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
        """Start an ephemeral worker container for a task. Returns container ID."""
        prompt = self._build_prompt(task, project)
        escaped_prompt = shlex.quote(prompt)
        container_name = f"cc-worker-{task.id}"

        # Clean up any leftover container with the same name
        try:
            old = self.docker_client.containers.get(container_name)
            old.remove(force=True)
            logger.warning("Removed leftover container %s", container_name)
        except docker.errors.NotFound:
            pass

        volumes = self._base_volumes(rw=True)

        container = self.docker_client.containers.run(
            image=WORKER_IMAGE,
            entrypoint=["bash", "-c"],
            command=[
                f"{_SETUP_CMDS} && "
                f"cd /projects/{project.name} && "
                f"claude -p {escaped_prompt} "
                f"--dangerously-skip-permissions "
                f"--output-format stream-json --verbose"
            ],
            volumes=volumes,
            working_dir=f"/projects/{project.name}",
            environment=self._worker_env(),
            tmpfs={"/worker-home": f"uid={HOST_USER_UID},gid={HOST_USER_UID}"},
            user=f"{HOST_USER_UID}:{HOST_USER_UID}",
            cpu_quota=int(WORKER_CPU_LIMIT * 100000),
            mem_limit=WORKER_MEM_LIMIT,
            network=WORKER_NETWORK,
            auto_remove=False,
            detach=True,
            name=container_name,
        )
        logger.info(
            "Started worker %s for task %s (project: %s)",
            container_name, task.id, project.name,
        )
        return container.id

    # =====================================================================
    # Persistent agent containers
    # =====================================================================

    def _ensure_session_volume(self, project_name: str) -> str:
        """Ensure a named volume for Claude session data exists with correct ownership.
        Returns volume name."""
        vol_name = f"cc-session-{project_name}"
        created = False
        try:
            self.docker_client.volumes.get(vol_name)
            logger.debug("Session volume %s already exists", vol_name)
        except docker.errors.NotFound:
            self.docker_client.volumes.create(
                name=vol_name,
                driver="local",
                labels={"managed-by": "cc-orchestrator", "project": project_name},
            )
            created = True
            logger.info("Created session volume %s", vol_name)

        if created:
            # New volume root dir is owned by root — fix ownership with a
            # throwaway container so the non-root worker can write to it.
            self.docker_client.containers.run(
                image="alpine",
                command=["chown", f"{HOST_USER_UID}:{HOST_USER_UID}", "/vol"],
                volumes={vol_name: {"bind": "/vol", "mode": "rw"}},
                auto_remove=True,
                detach=False,
            )
            logger.debug("Set ownership on %s to uid %s", vol_name, HOST_USER_UID)

        return vol_name

    def remove_session_volume(self, project_name: str):
        """Remove the session volume for a project (call on project deletion)."""
        vol_name = f"cc-session-{project_name}"
        try:
            vol = self.docker_client.volumes.get(vol_name)
            vol.remove(force=True)
            logger.info("Removed session volume %s", vol_name)
        except docker.errors.NotFound:
            logger.debug("Session volume %s not found (already removed)", vol_name)

    def ensure_project_container(self, project: Project) -> str:
        """Get or create a persistent container for a project. PID 1 = sleep infinity.
        Returns container ID.  All agents in the same project share this container.
        """
        container_name = f"cc-project-{project.name}"

        # Check if a running container already exists
        try:
            existing = self.docker_client.containers.get(container_name)
            if existing.status == "running":
                logger.debug("Reusing project container %s", container_name)
                return existing.id
            # Exists but not running — remove and recreate
            existing.remove(force=True)
            logger.warning("Removed non-running project container %s", container_name)
        except docker.errors.NotFound:
            pass

        # Named volume keeps .claude/ session data across container restarts
        session_vol = self._ensure_session_volume(project.name)

        volumes = self._base_volumes(rw=True)
        # Mount session volume at $HOME/.claude/ — persists sessions + refreshed tokens.
        # Tmpfs stays for $HOME (scratch space), the named volume sub-mount takes precedence.
        volumes[session_vol] = {"bind": "/worker-home/.claude", "mode": "rw"}

        container = self.docker_client.containers.run(
            image=WORKER_IMAGE,
            entrypoint=["bash", "-c"],
            command=[f"{_SETUP_CMDS} && exec sleep infinity"],
            volumes=volumes,
            working_dir=f"/projects/{project.name}",
            environment=self._worker_env(),
            tmpfs={"/worker-home": f"uid={HOST_USER_UID},gid={HOST_USER_UID}"},
            user=f"{HOST_USER_UID}:{HOST_USER_UID}",
            cpu_quota=int(WORKER_CPU_LIMIT * 100000),
            mem_limit=WORKER_MEM_LIMIT,
            network=WORKER_NETWORK,
            auto_remove=False,
            detach=True,
            name=container_name,
        )
        logger.info(
            "Started project container %s (project: %s, session_vol: %s)",
            container_name, project.name, session_vol,
        )
        return container.id

    def exec_claude_in_agent(
        self,
        container_id: str,
        prompt: str,
        project: Project,
        agent: Agent,
        resume_session_id: str | None = None,
    ) -> tuple[str, str]:
        """Run claude via docker exec inside a persistent agent container.
        Returns (exec_id, output_file) for monitoring.
        """
        escaped_prompt = shlex.quote(prompt)
        # Use a unique output file per invocation
        import uuid
        output_file = f"/tmp/claude-output-{uuid.uuid4().hex[:8]}.log"

        resume_flag = f"--resume {shlex.quote(resume_session_id)}" if resume_session_id else ""
        worktree_flag = f"--worktree {shlex.quote(agent.worktree)}" if agent.worktree else ""
        cmd = (
            f"cd /projects/{project.name} && "
            f"claude -p {escaped_prompt} "
            f"--dangerously-skip-permissions "
            f"--output-format stream-json --verbose "
            f"{worktree_flag} "
            f"{resume_flag} "
            f"2>&1 | tee {output_file}"
        )

        container = self.docker_client.containers.get(container_id)
        exec_result = self.docker_client.api.exec_create(
            container.id,
            ["bash", "-c", cmd],
            workdir=f"/projects/{project.name}",
            user=f"{HOST_USER_UID}:{HOST_USER_UID}",
            environment=self._worker_env(),
        )
        exec_id = exec_result["Id"]

        # Start the exec (non-blocking — we use detach=True via socket=False, stream=False)
        self.docker_client.api.exec_start(exec_id, detach=True)

        logger.info(
            "Exec started in agent %s (exec_id=%s, resume=%s)",
            agent.id, exec_id[:12], bool(resume_session_id),
        )
        return exec_id, output_file

    def is_exec_running(self, exec_id: str) -> bool:
        """Check if a docker exec is still running."""
        try:
            info = self.docker_client.api.exec_inspect(exec_id)
            return info.get("Running", False)
        except docker.errors.APIError:
            return False

    def read_exec_output(self, container_id: str, output_file: str) -> str:
        """Read the output file from inside a container."""
        try:
            container = self.docker_client.containers.get(container_id)
            exit_code, output = container.exec_run(
                ["cat", output_file],
                user=f"{HOST_USER_UID}:{HOST_USER_UID}",
            )
            if exit_code == 0:
                return output.decode("utf-8", errors="replace")
            return ""
        except (docker.errors.NotFound, docker.errors.APIError):
            return ""

    def stop_project_container(self, container_id: str):
        """Stop and remove a project container."""
        try:
            container = self.docker_client.containers.get(container_id)
            if container.status == "running":
                container.stop(timeout=10)
            container.remove(force=True)
            logger.info("Stopped and removed project container %s", container_id[:12])
        except docker.errors.NotFound:
            logger.debug("Project container %s already removed", container_id[:12])

    # =====================================================================
    # Project setup
    # =====================================================================

    def clone_project(self, project_name: str, git_url: str):
        """Clone a git repo into the projects directory via a temporary container."""
        volumes = self._projects_volume("rw")
        try:
            self.docker_client.containers.run(
                image="alpine/git",
                command=["clone", git_url, f"/projects/{project_name}"],
                volumes=volumes,
                auto_remove=True,
                detach=False,
            )
            logger.info("Cloned project %s from %s", project_name, git_url)
        except docker.errors.ContainerError:
            # Clone failed (private repo, bad URL, etc.) — create empty dir
            logger.warning("Git clone failed for %s — creating empty directory", project_name)
            self.docker_client.containers.run(
                image="alpine",
                command=["mkdir", "-p", f"/projects/{project_name}"],
                volumes=volumes,
                auto_remove=True,
                detach=False,
            )

    def ensure_project_dir(self, project_name: str):
        """Ensure the project directory exists in the projects volume."""
        volumes = self._projects_volume("rw")
        self.docker_client.containers.run(
            image="alpine",
            command=["mkdir", "-p", f"/projects/{project_name}"],
            volumes=volumes,
            auto_remove=True,
            detach=False,
        )

    # =====================================================================
    # Common operations
    # =====================================================================

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

    def stream_logs(self, container_id: str):
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

    def list_containers(self) -> list[dict]:
        """List all cc-project and cc-worker containers with their status."""
        results = []
        for prefix in ("cc-project-", "cc-worker-"):
            containers = self.docker_client.containers.list(
                all=True,
                filters={"name": prefix},
            )
            results.extend(
                {
                    "id": c.id,
                    "name": c.name,
                    "status": c.status,
                    "created": c.attrs.get("Created", ""),
                    "project": c.name.replace("cc-project-", "").replace("cc-worker-", "") if c.name else "",
                }
                for c in containers
            )
        return results

    def ping(self) -> bool:
        """Check if Docker daemon is reachable."""
        try:
            self.docker_client.ping()
            return True
        except Exception:
            return False
