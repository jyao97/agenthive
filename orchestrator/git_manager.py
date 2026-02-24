"""Git Manager — git operations via host subprocess."""

import logging
import os
import subprocess

from config import PROJECTS_DIR

logger = logging.getLogger("orchestrator.git")


class GitManager:
    """Git operations executed as host subprocesses."""

    def _project_path(self, project_name: str) -> str:
        if PROJECTS_DIR:
            return os.path.join(PROJECTS_DIR, project_name)
        return os.path.join("/projects", project_name)

    def _run_git(self, project_name: str, git_args: str, timeout: int = 30) -> str:
        """Run a git command against a project directory."""
        cwd = self._project_path(project_name)
        try:
            result = subprocess.run(
                ["git"] + git_args.split(),
                cwd=cwd,
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if stderr:
                    logger.warning("Git command failed for %s: %s", project_name, stderr)
                    return f"ERROR: {stderr}"
            return result.stdout.strip()
        except FileNotFoundError:
            msg = f"Project directory not found: {cwd}"
            logger.warning(msg)
            return f"ERROR: {msg}"
        except subprocess.TimeoutExpired:
            logger.warning("Git command timed out for %s", project_name)
            return "ERROR: command timed out"
        except Exception as e:
            logger.exception("Git operation failed for %s", project_name)
            return f"ERROR: {str(e)}"

    def _run_git_rw(self, project_name: str, command: str, timeout: int = 30) -> str:
        """Run a shell command with read-write access (for merges)."""
        cwd = self._project_path(project_name)
        try:
            result = subprocess.run(
                ["sh", "-c", command],
                cwd=cwd,
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if stderr:
                    logger.warning("Git RW command failed for %s: %s", project_name, stderr)
                    return f"ERROR: {stderr}"
            return result.stdout.strip()
        except FileNotFoundError:
            msg = f"Project directory not found: {cwd}"
            logger.warning(msg)
            return f"ERROR: {msg}"
        except subprocess.TimeoutExpired:
            logger.warning("Git RW command timed out for %s", project_name)
            return "ERROR: command timed out"
        except Exception as e:
            logger.exception("Git RW operation failed for %s", project_name)
            return f"ERROR: {str(e)}"

    def get_log(self, project_name: str, limit: int = 30) -> list[dict]:
        """Get recent commits for a project."""
        sep = "|||"
        fmt = f"%H{sep}%an{sep}%ae{sep}%aI{sep}%s"
        raw = self._run_git(project_name, f"log --format={fmt} -n {limit}")
        if raw.startswith("ERROR:"):
            return []

        commits = []
        for line in raw.splitlines():
            parts = line.split(sep)
            if len(parts) >= 5:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "email": parts[2],
                    "date": parts[3],
                    "message": parts[4],
                })
        return commits

    def get_branches(self, project_name: str) -> list[dict]:
        """Get branches for a project."""
        raw = self._run_git(project_name, "branch -a --format=%(refname:short)|||%(objectname:short)|||%(HEAD)")
        if raw.startswith("ERROR:"):
            return []

        branches = []
        for line in raw.splitlines():
            parts = line.split("|||")
            if len(parts) >= 3:
                branches.append({
                    "name": parts[0].strip(),
                    "commit": parts[1].strip(),
                    "current": parts[2].strip() == "*",
                })
            elif len(parts) >= 1:
                branches.append({"name": parts[0].strip(), "commit": "", "current": False})
        return branches

    def get_status(self, project_name: str) -> dict:
        """Get git status for a project: branch, staged, unstaged, untracked."""
        branch = self._run_git(project_name, "branch --show-current")
        if branch.startswith("ERROR:"):
            branch = "unknown"

        raw = self._run_git(project_name, "status --porcelain")
        if raw.startswith("ERROR:"):
            return {"branch": branch, "clean": True, "staged": [], "unstaged": [], "untracked": []}

        staged = []
        unstaged = []
        untracked = []
        for line in raw.splitlines():
            if len(line) < 3:
                continue
            x, y = line[0], line[1]
            path = line[3:]
            if x == "?":
                untracked.append(path)
            else:
                if x not in (" ", "?"):
                    staged.append({"status": x, "path": path})
                if y not in (" ", "?"):
                    unstaged.append({"status": y, "path": path})

        clean = len(staged) == 0 and len(unstaged) == 0 and len(untracked) == 0
        return {
            "branch": branch,
            "clean": clean,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        }

    def get_diff(self, project_name: str, ref: str = "HEAD") -> str:
        """Get diff for a ref."""
        return self._run_git(project_name, f"diff {ref}")

    def merge_branch(self, project_name: str, branch: str) -> dict:
        """Merge a branch into the current branch. Returns result dict."""
        current = self._run_git(project_name, "branch --show-current")
        if current.startswith("ERROR:"):
            return {"success": False, "error": current, "current_branch": "unknown"}

        cmd = (
            f"git config user.name 'CC Orchestrator' && "
            f"git config user.email 'cc-orchestrator@localhost' && "
            f"git merge {branch} --no-edit"
        )
        result = self._run_git_rw(project_name, cmd)

        if result.startswith("ERROR:"):
            if "CONFLICT" in result or "conflict" in result:
                self._run_git_rw(project_name, "git merge --abort")
                return {
                    "success": False,
                    "error": "Merge conflict — manual resolution required",
                    "detail": result,
                    "current_branch": current,
                    "merged_branch": branch,
                }
            return {"success": False, "error": result, "current_branch": current}

        return {
            "success": True,
            "message": result,
            "current_branch": current,
            "merged_branch": branch,
        }
