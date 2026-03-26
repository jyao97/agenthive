"""Git Manager — git operations via host subprocess."""

import logging
import os
import subprocess

logger = logging.getLogger("orchestrator.git")


class GitManager:
    """Git operations executed as host subprocesses."""

    def _run_git(self, project_path: str, git_args: list[str], timeout: int = 30) -> str:
        """Run a git command against a project directory.

        Args:
            project_path: absolute path to the project directory.
            git_args: list of git arguments (e.g. ["log", "-n", "5"]).
        """
        cwd = project_path
        try:
            result = subprocess.run(
                ["git"] + git_args,
                cwd=cwd,
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if stderr:
                    logger.warning("Git command failed for %s: %s", cwd, stderr)
                    return f"ERROR: {stderr}"
            return result.stdout.rstrip()
        except FileNotFoundError:
            msg = f"Project directory not found: {cwd}"
            logger.warning(msg)
            return f"ERROR: {msg}"
        except subprocess.TimeoutExpired:
            logger.warning("Git command timed out for %s", cwd)
            return "ERROR: command timed out"

    def get_log(self, project_path: str, limit: int = 30) -> list[dict]:
        """Get recent commits for a project."""
        sep = "|||"
        fmt = f"%H{sep}%an{sep}%ae{sep}%aI{sep}%s"
        raw = self._run_git(project_path, ["log", f"--format={fmt}", "-n", str(limit)])
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

    def get_branches(self, project_path: str) -> list[dict]:
        """Get branches for a project."""
        raw = self._run_git(
            project_path,
            ["branch", "-a", "--format=%(refname:short)|||%(objectname:short)|||%(HEAD)"],
        )
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

    def get_status(self, project_path: str) -> dict:
        """Get git status for a project: branch, staged, unstaged, untracked."""
        branch = self._run_git(project_path, ["branch", "--show-current"])
        if branch.startswith("ERROR:"):
            branch = "unknown"

        raw = self._run_git(project_path, ["status", "--porcelain"])
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

    def get_worktrees(self, project_path: str) -> list[dict]:
        """List git worktrees for a project."""
        raw = self._run_git(project_path, ["worktree", "list", "--porcelain"])
        if raw.startswith("ERROR:"):
            return []

        worktrees = []
        current: dict = {}
        for line in raw.splitlines():
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[len("worktree "):]}
            elif line.startswith("HEAD "):
                current["commit"] = line[len("HEAD "):][:7]
            elif line.startswith("branch "):
                ref = line[len("branch "):]
                current["branch"] = ref.replace("refs/heads/", "")
            elif line == "bare":
                current["bare"] = True
            elif line == "detached":
                current["detached"] = True
        if current:
            worktrees.append(current)
        return worktrees

    def get_head(self, project_path: str) -> str | None:
        """Get current HEAD commit hash."""
        result = self._run_git(project_path, ["rev-parse", "HEAD"])
        if result.startswith("ERROR:"):
            return None
        return result.strip()

    def get_current_branch(self, project_path: str) -> str | None:
        """Get the current branch name."""
        result = self._run_git(project_path, ["branch", "--show-current"])
        if result.startswith("ERROR:"):
            return None
        return result.strip() or None

    def checkout(self, project_path: str, ref: str) -> str:
        """Checkout a branch or commit."""
        return self._run_git(project_path, ["checkout", ref])

    def reset_hard(self, project_path: str, commit: str) -> str:
        """Reset current branch to a specific commit.

        Stashes uncommitted PROGRESS.md changes before reset to prevent
        auto-summary data loss.
        """
        # Guard: stash uncommitted PROGRESS.md before destructive reset
        status = self._run_git(project_path, ["status", "--porcelain", "--", "PROGRESS.md"])
        if status.strip() and not status.startswith("ERROR:"):
            logger.warning("reset_hard: stashing uncommitted PROGRESS.md in %s", project_path)
            self._run_git(project_path, ["stash", "push", "-m", "auto-stash PROGRESS.md before reset", "--", "PROGRESS.md"])
        return self._run_git(project_path, ["reset", "--hard", commit])

    def get_diff(self, project_path: str, ref: str = "HEAD") -> str:
        """Get diff for a ref."""
        return self._run_git(project_path, ["diff", ref])

    def merge_branch(self, project_path: str, branch: str, *,
                     no_ff: bool = False, message: str | None = None) -> dict:
        """Merge a branch into the current branch. Returns result dict."""
        current = self._run_git(project_path, ["branch", "--show-current"])
        if current.startswith("ERROR:"):
            return {"success": False, "error": current, "current_branch": "unknown"}

        self._run_git(project_path, ["config", "user.name", "AgentHive"])
        self._run_git(project_path, ["config", "user.email", os.getenv("GIT_USER_EMAIL", "agenthive@localhost")])

        merge_args = ["merge", branch]
        if no_ff:
            merge_args.append("--no-ff")
        if message:
            merge_args += ["-m", message]
        else:
            merge_args.append("--no-edit")
        result = self._run_git(project_path, merge_args)

        if result.startswith("ERROR:"):
            if "CONFLICT" in result or "conflict" in result:
                self._run_git(project_path, ["merge", "--abort"])
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

    def get_main_branch(self, project_path: str) -> str:
        """Detect the main branch name (main, master, etc.)."""
        ref = self._run_git(project_path, ["symbolic-ref", "refs/remotes/origin/HEAD"])
        if not ref.startswith("ERROR:"):
            return ref.replace("refs/remotes/origin/", "").strip()
        # Fallback: check if main or master exists
        for name in ("main", "master"):
            result = self._run_git(project_path, ["rev-parse", "--verify", name])
            if not result.startswith("ERROR:"):
                return name
        return "main"  # last resort default

    def remove_worktree(self, project_path: str, worktree_path: str) -> str:
        """Remove a git worktree (force)."""
        return self._run_git(project_path, ["worktree", "remove", worktree_path, "--force"])

    def delete_branch(self, project_path: str, branch: str, *, force: bool = False) -> str:
        """Delete a local branch (-d, or -D if force=True)."""
        flag = "-D" if force else "-d"
        return self._run_git(project_path, ["branch", flag, branch])
