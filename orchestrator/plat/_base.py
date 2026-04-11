"""Abstract base for platform-specific implementations."""
from abc import ABC, abstractmethod


class PlatformBase(ABC):
    """Unified API for OS-level process and system queries."""

    # ── Process inspection ───────────────────────────────────────────

    @abstractmethod
    def pid_exists(self, pid: int) -> bool:
        """Return True if *pid* is alive."""

    @abstractmethod
    def get_process_cmdline(self, pid: int) -> list[str]:
        """Return the argv list for *pid*, or [] on failure."""

    @abstractmethod
    def get_process_cwd(self, pid: int) -> str:
        """Return the working directory of *pid*, or '' on failure."""

    @abstractmethod
    def get_open_files(self, pid: int) -> list[str]:
        """Return absolute paths of files opened by *pid*."""

    @abstractmethod
    def get_child_pids(self, ppid: int) -> list[tuple[int, str]]:
        """Return [(pid, comm), ...] for direct children of *ppid*."""

    @abstractmethod
    def find_pids_by_name(self, name: str) -> list[int]:
        """Return PIDs of all processes whose command matches *name*."""

    @abstractmethod
    def get_process_tty(self, pid: int) -> str:
        """Return the TTY device path for *pid*, or '' if unknown."""

    # ── System stats ─────────────────────────────────────────────────

    @abstractmethod
    def get_cpu_load(self) -> dict | None:
        """Return {"load_1m": float, "cores": int, "usage_pct": float} or None."""

    @abstractmethod
    def get_memory_info(self) -> dict | None:
        """Return {"total_gb": float, "used_gb": float, "usage_pct": float} or None."""

    @abstractmethod
    def get_process_memory_mb(self, pid: int) -> float:
        """Return RSS in MB for *pid*, or 0.0 on failure."""

    @abstractmethod
    def get_gpu_stats(self) -> list[dict] | None:
        """Return list of GPU info dicts, or None if unavailable."""

    # ── Network / ports ──────────────────────────────────────────────

    @abstractmethod
    def find_port_listeners(self, port: int) -> list[int]:
        """Return PIDs listening on *port*."""

    # ── Utility ──────────────────────────────────────────────────────

    @abstractmethod
    def get_lan_ip(self) -> str:
        """Return the primary LAN IP address."""

    # ── Credentials ─────────────────────────────────────────────────

    @abstractmethod
    def get_claude_credentials(self) -> dict | None:
        """Return Claude OAuth credentials dict, or None if unavailable."""
