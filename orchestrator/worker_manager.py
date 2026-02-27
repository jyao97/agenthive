"""Worker Manager — host subprocess lifecycle for CC workers and agents."""

import logging
import os
import signal
import subprocess
import uuid

from config import CLAUDE_BIN, PROJECTS_DIR
from models import Agent, Project, Task

logger = logging.getLogger("orchestrator.worker")


class WorkerManager:
    """Manages CC worker subprocesses (ephemeral tasks and persistent agents)."""

    def __init__(self):
        # pid_str -> {process, output_file, project, started_at}
        self._processes: dict[str, dict] = {}
        self._verify_claude()

    def _verify_claude(self):
        """Check that the claude CLI is available."""
        try:
            result = subprocess.run(
                [CLAUDE_BIN, "--version"],
                capture_output=True, text=True, timeout=10,
                env=self._clean_env(),
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                logger.info("Claude CLI found: %s", version)
            else:
                logger.warning("Claude CLI returned non-zero: %s", result.stderr.strip())
        except FileNotFoundError:
            logger.warning(
                "Claude CLI '%s' not found — install it or set CLAUDE_BIN",
                CLAUDE_BIN,
            )
        except Exception as e:
            logger.warning("Failed to verify claude CLI: %s", e)

    @staticmethod
    def _clean_env() -> dict[str, str]:
        """Return os.environ without CLAUDECODE vars so spawned claude
        processes don't think they're nested inside another session.
        Sets AGENTHIVE_MANAGED=1 so the orchestrator can distinguish its
        own subprocesses from tmux-launched CLI sessions."""
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        env["AGENTHIVE_MANAGED"] = "1"
        return env

    def _get_project_path(self, project_name: str) -> str:
        """Return the absolute path for a project directory."""
        if PROJECTS_DIR:
            return os.path.join(PROJECTS_DIR, project_name)
        return os.path.join("/projects", project_name)

    # =====================================================================
    # Project setup
    # =====================================================================

    def ensure_project_ready(self, project: Project) -> str:
        """Validate project directory exists. Returns the project path."""
        project_path = project.path
        if not os.path.isdir(project_path):
            raise FileNotFoundError(f"Project directory not found: {project_path}")
        logger.debug("Project %s ready at %s", project.name, project_path)
        return project_path

    def clone_project(self, project_name: str, git_url: str):
        """Clone a git repo into the projects directory."""
        project_path = self._get_project_path(project_name)
        if os.path.isdir(project_path):
            logger.info("Project dir %s already exists, skipping clone", project_path)
            return
        try:
            subprocess.run(
                ["git", "clone", git_url, project_path],
                check=True, capture_output=True, text=True, timeout=120,
            )
            logger.info("Cloned project %s from %s", project_name, git_url)
        except subprocess.CalledProcessError as e:
            logger.warning(
                "Git clone failed for %s: %s — creating empty directory",
                project_name, e.stderr.strip(),
            )
            os.makedirs(project_path, exist_ok=True)

    def ensure_project_dir(self, project_name: str):
        """Ensure the project directory exists."""
        project_path = self._get_project_path(project_name)
        os.makedirs(project_path, exist_ok=True)

    # =====================================================================
    # Ephemeral task workers (one-shot)
    # =====================================================================

    def _build_prompt(self, task: Task, project: Project) -> str:
        """Wrap the user prompt with worker instructions."""
        return (
            f"You are working in project: {project.display_name}\n"
            f"Project path: {project.path}\n"
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
        """Start an ephemeral worker subprocess for a task. Returns PID string."""
        prompt = self._build_prompt(task, project)
        project_path = project.path
        output_file = f"/tmp/claude-output-{uuid.uuid4().hex[:8]}.log"

        cmd = [
            CLAUDE_BIN, "-p", prompt,
            "--dangerously-skip-permissions",
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
                env=self._clean_env(),
            )

        pid_str = str(process.pid)
        self._processes[pid_str] = {
            "process": process,
            "output_file": output_file,
            "project": project.name,
            "type": "worker",
        }

        logger.info(
            "Started worker PID %s for task %s (project: %s)",
            pid_str, task.id, project.name,
        )
        return pid_str

    # =====================================================================
    # Agent exec (persistent conversations)
    # =====================================================================

    def exec_claude_in_agent(
        self,
        project_path: str,
        prompt: str,
        project: Project,
        agent: Agent,
        resume_session_id: str | None = None,
        message_id: str | None = None,
    ) -> tuple[str, str]:
        """Run claude as a subprocess for an agent message.
        Returns (pid_str, output_file) for monitoring.
        """
        # Use message_id for predictable file name so partial output
        # can be recovered after a crash.
        file_tag = message_id or uuid.uuid4().hex[:8]
        output_file = f"/tmp/claude-output-{file_tag}.log"

        cmd = [CLAUDE_BIN, "-p", prompt,
               "--output-format", "stream-json", "--verbose"]
        if getattr(agent, "skip_permissions", True):
            cmd.insert(3, "--dangerously-skip-permissions")

        if agent.model:
            cmd.extend(["--model", agent.model])
        if agent.effort:
            cmd.extend(["--effort", agent.effort])
        if agent.worktree:
            cmd.extend(["--worktree", agent.worktree])
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])

        with open(output_file, "w") as out_f:
            process = subprocess.Popen(
                cmd,
                cwd=project_path,
                stdout=out_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=self._clean_env(),
            )

        pid_str = str(process.pid)
        self._processes[pid_str] = {
            "process": process,
            "output_file": output_file,
            "project": project.name,
            "type": "agent",
        }

        logger.info(
            "Exec started for agent %s (pid=%s, resume=%s)",
            agent.id, pid_str, bool(resume_session_id),
        )
        return pid_str, output_file

    def is_exec_running(self, pid_str: str) -> bool:
        """Check if a subprocess is still running."""
        info = self._processes.get(pid_str)
        if not info:
            return False
        return info["process"].poll() is None

    def read_exec_output(self, pid_str: str, output_file: str) -> str:
        """Read the output file from a process."""
        # Use the output_file directly (it's on the host filesystem)
        try:
            with open(output_file, "r", errors="replace") as f:
                return f.read()
        except FileNotFoundError:
            return ""  # file not created yet — normal
        except OSError as e:
            logger.warning("read_exec_output failed for %s (%s): %s", pid_str, output_file, e)
            return ""

    # =====================================================================
    # Common operations
    # =====================================================================

    def get_status(self, pid_str: str) -> str:
        """Get process status: running / exited / removed."""
        info = self._processes.get(pid_str)
        if not info:
            return "removed"
        rc = info["process"].poll()
        if rc is None:
            return "running"
        return "exited"

    def get_logs(self, pid_str: str, tail: int = 0) -> str:
        """Get process output logs."""
        info = self._processes.get(pid_str)
        if not info:
            return ""
        output_file = info.get("output_file", "")
        if not output_file:
            return ""
        try:
            with open(output_file, "r", errors="replace") as f:
                content = f.read()
            if tail > 0:
                lines = content.splitlines()
                return "\n".join(lines[-tail:])
            return content
        except FileNotFoundError:
            return ""
        except OSError as e:
            logger.warning("get_logs failed for %s: %s", pid_str, e)
            return ""

    def stop_worker(self, pid_str: str):
        """Stop a worker subprocess."""
        info = self._processes.get(pid_str)
        if not info:
            logger.debug("Process %s not found (already cleaned up)", pid_str)
            return

        process = info["process"]
        if process.poll() is None:
            # Try graceful termination first
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError) as e:
                logger.warning("killpg SIGTERM failed for %s: %s", pid_str, e)
                try:
                    process.terminate()
                except (ProcessLookupError, OSError) as e2:
                    logger.warning("process.terminate() also failed for %s: %s", pid_str, e2)

            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Force kill
                logger.warning("Process %s did not exit after SIGTERM, sending SIGKILL", pid_str)
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError) as e:
                    logger.warning("killpg SIGKILL failed for %s: %s", pid_str, e)
                    try:
                        process.kill()
                    except (ProcessLookupError, OSError) as e2:
                        logger.error("All kill attempts failed for %s: %s", pid_str, e2)

            logger.info("Stopped process %s", pid_str)
        else:
            logger.debug("Process %s already exited", pid_str)

        # Clean up tracking
        self._processes.pop(pid_str, None)

    def stop_project_processes(self, project_name: str):
        """Stop all tracked processes for a project."""
        to_stop = [
            pid_str for pid_str, info in self._processes.items()
            if info.get("project") == project_name
        ]
        for pid_str in to_stop:
            self.stop_worker(pid_str)
        if to_stop:
            logger.info("Stopped %d processes for project %s", len(to_stop), project_name)

    def cleanup_exited(self):
        """Remove tracking entries for exited processes."""
        exited = [
            pid_str for pid_str, info in self._processes.items()
            if info["process"].poll() is not None
        ]
        for pid_str in exited:
            self._processes.pop(pid_str, None)
        if exited:
            logger.info("Cleaned up %d exited processes", len(exited))

    def list_processes(self) -> list[dict]:
        """List all tracked processes."""
        results = []
        for pid_str, info in self._processes.items():
            process = info["process"]
            rc = process.poll()
            status = "running" if rc is None else "exited"
            results.append({
                "id": pid_str,
                "name": f"claude-{info.get('type', 'worker')}-{pid_str}",
                "status": status,
                "created": "",
                "project": info.get("project", ""),
            })
        return results

    def ping(self) -> bool:
        """Check if claude CLI is reachable."""
        try:
            result = subprocess.run(
                [CLAUDE_BIN, "--version"],
                capture_output=True, text=True, timeout=10,
                env=self._clean_env(),
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.debug("Claude CLI ping failed: %s", e)
            return False
