"""macOS (Darwin) implementation — uses psutil + lsof for portability."""
import logging
import os
import subprocess

import psutil

from ._base import PlatformBase

logger = logging.getLogger("orchestrator.platform.darwin")


class DarwinPlatform(PlatformBase):

    # ── Process inspection ───────────────────────────────────────────

    def pid_exists(self, pid: int) -> bool:
        return psutil.pid_exists(pid)

    def get_process_cmdline(self, pid: int) -> list[str]:
        try:
            return psutil.Process(pid).cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return []

    def get_process_cwd(self, pid: int) -> str:
        try:
            return psutil.Process(pid).cwd()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return ""

    def get_open_files(self, pid: int) -> list[str]:
        try:
            proc = psutil.Process(pid)
            return [f.path for f in proc.open_files()]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
            logger.debug("get_open_files for PID %d failed: %s", pid, e)
            return []

    def get_child_pids(self, ppid: int) -> list[tuple[int, str]]:
        try:
            parent = psutil.Process(ppid)
            result = []
            for c in parent.children(recursive=False):
                # psutil.name() on macOS returns the binary name (e.g. "2.1.87"
                # for Claude Code's versioned Node binary) instead of the
                # symlink name "claude".  Fall back to cmdline[0] which is the
                # actual argv[0] the user sees.
                name = c.name()
                try:
                    cmdline = c.cmdline()
                    if cmdline:
                        import os
                        name = os.path.basename(cmdline[0])
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    pass
                result.append((c.pid, name))
            return result
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return []

    def find_pids_by_name(self, name: str) -> list[int]:
        pids = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                info = proc.info
                # Check process name
                if info['name'] == name:
                    pids.append(info['pid'])
                    continue
                # Check cmdline[0] basename (handles versioned binaries)
                cmdline = info.get('cmdline')
                if cmdline and os.path.basename(cmdline[0]) == name:
                    pids.append(info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return pids

    def get_process_tty(self, pid: int) -> str:
        try:
            return psutil.Process(pid).terminal() or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return ""

    # ── System stats ─────────────────────────────────────────────────

    def get_cpu_load(self) -> dict | None:
        try:
            load1 = os.getloadavg()[0]
            cpu_count = os.cpu_count() or 1
            return {
                "load_1m": round(load1, 2),
                "cores": cpu_count,
                "usage_pct": round(min(load1 / cpu_count * 100, 100), 1),
            }
        except (OSError, AttributeError) as e:
            logger.warning("get_cpu_load failed: %s", e)
            return None

    def get_memory_info(self) -> dict | None:
        try:
            mem = psutil.virtual_memory()
            return {
                "total_gb": round(mem.total / (1024 ** 3), 1),
                "used_gb": round(mem.used / (1024 ** 3), 1),
                "usage_pct": round(mem.percent, 1),
            }
        except Exception as e:
            logger.warning("get_memory_info failed: %s", e)
            return None

    def get_process_memory_mb(self, pid: int) -> float:
        try:
            return psutil.Process(pid).memory_info().rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0

    def get_gpu_stats(self) -> list[dict] | None:
        # macOS: no nvidia-smi.  Apple Silicon GPU stats require
        # IOKit / Metal which is out of scope for now.
        return None

    # ── Network / ports ──────────────────────────────────────────────

    def find_port_listeners(self, port: int) -> list[int]:
        # Primary: lsof (macOS syntax with -nP)
        try:
            result = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                pids = set()
                for line in result.stdout.strip().splitlines()[1:]:  # skip header
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            pids.add(int(parts[1]))
                        except ValueError:
                            continue
                return list(pids)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        # Fallback: psutil
        try:
            pids = set()
            for conn in psutil.net_connections(kind="tcp"):
                if conn.laddr.port == port and conn.status == "LISTEN" and conn.pid:
                    pids.add(conn.pid)
            return list(pids)
        except (psutil.AccessDenied, OSError):
            pass
        return []

    # ── Utility ──────────────────────────────────────────────────────

    def get_lan_ip(self) -> str:
        try:
            result = subprocess.run(
                ["ipconfig", "getifaddr", "en0"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        # Fallback: UDP connect trick (works on any platform)
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"

    # ── Credentials ─────────────────────────────────────────────────

    def get_claude_credentials(self) -> dict | None:
        """Read credentials from macOS Keychain (Claude Code 2.x storage)."""
        import json
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        # Fallback: file-based (older Claude Code versions)
        import json
        creds_path = os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json")
        try:
            with open(creds_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
