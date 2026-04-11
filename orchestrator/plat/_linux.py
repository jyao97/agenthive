"""Linux implementation — uses /proc for maximum speed, no external deps."""
import logging
import os
import re
import subprocess

from ._base import PlatformBase

logger = logging.getLogger("orchestrator.platform.linux")


class LinuxPlatform(PlatformBase):

    # ── Process inspection ───────────────────────────────────────────

    def pid_exists(self, pid: int) -> bool:
        return os.path.exists(f"/proc/{pid}")

    def get_process_cmdline(self, pid: int) -> list[str]:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
            return raw.decode("utf-8", errors="replace").split("\0")
        except OSError:
            return []

    def get_process_cwd(self, pid: int) -> str:
        try:
            return os.path.realpath(os.readlink(f"/proc/{pid}/cwd"))
        except OSError:
            return ""

    def get_open_files(self, pid: int) -> list[str]:
        paths = []
        try:
            fd_dir = f"/proc/{pid}/fd"
            for entry in os.listdir(fd_dir):
                try:
                    target = os.readlink(os.path.join(fd_dir, entry))
                    if os.path.isabs(target):
                        paths.append(target)
                except OSError:
                    continue
        except OSError as e:
            logger.debug("get_open_files: /proc/%d/fd scan failed: %s", pid, e)
        return paths

    def get_child_pids(self, ppid: int) -> list[tuple[int, str]]:
        try:
            result = subprocess.run(
                ["ps", "--ppid", str(ppid), "-o", "pid=,comm="],
                capture_output=True, text=True, timeout=5,
            )
            children = []
            for line in result.stdout.strip().splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    pid = int(parts[0])
                    comm = parts[1]
                    # comm may be a versioned binary name (e.g. "2.1.87")
                    # instead of the actual command (e.g. "claude").  Fall
                    # back to /proc/pid/cmdline argv[0] basename.
                    if re.match(r'^[\d.]+$', comm):
                        try:
                            with open(f"/proc/{pid}/cmdline", "rb") as f:
                                raw = f.read()
                            argv0 = raw.decode("utf-8", errors="replace").split("\0")[0]
                            if argv0:
                                comm = os.path.basename(argv0)
                        except OSError:
                            pass
                    children.append((pid, comm))
            return children
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return []

    def find_pids_by_name(self, name: str) -> list[int]:
        try:
            result = subprocess.run(
                ["pgrep", "-f", name],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return [int(p) for p in result.stdout.strip().splitlines() if p.strip()]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
            pass
        return []

    def get_process_tty(self, pid: int) -> str:
        try:
            result = subprocess.run(
                ["ps", "-o", "tty=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            tty = result.stdout.strip()
            if tty and tty != "?":
                return tty if tty.startswith("/") else f"/dev/{tty}"
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return ""

    # ── System stats ─────────────────────────────────────────────────

    def get_cpu_load(self) -> dict | None:
        try:
            with open("/proc/loadavg") as f:
                load1 = float(f.read().split()[0])
            cpu_count = os.cpu_count() or 1
            return {
                "load_1m": round(load1, 2),
                "cores": cpu_count,
                "usage_pct": round(min(load1 / cpu_count * 100, 100), 1),
            }
        except (OSError, ValueError, IndexError) as e:
            logger.warning("get_cpu_load failed: %s", e)
            return None

    def get_memory_info(self) -> dict | None:
        try:
            meminfo = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
            total = meminfo.get("MemTotal", 0)
            avail = meminfo.get("MemAvailable", 0)
            used = total - avail
            return {
                "total_gb": round(total / 1048576, 1),
                "used_gb": round(used / 1048576, 1),
                "usage_pct": round(used / total * 100, 1) if total else 0,
            }
        except (OSError, ValueError, IndexError, ZeroDivisionError) as e:
            logger.warning("get_memory_info failed: %s", e)
            return None

    def get_process_memory_mb(self, pid: int) -> float:
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
        except (OSError, ValueError):
            pass
        return 0.0

    def get_gpu_stats(self) -> list[dict] | None:
        try:
            result = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            gpus = []
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 6:
                    gpus.append({
                        "index": int(parts[0]),
                        "name": parts[1],
                        "gpu_pct": int(parts[2]),
                        "mem_used_mb": int(parts[3]),
                        "mem_total_mb": int(parts[4]),
                        "mem_pct": round(int(parts[3]) / int(parts[4]) * 100, 1) if int(parts[4]) else 0,
                        "temp_c": int(parts[5]),
                    })
            return gpus
        except FileNotFoundError:
            return None
        except (subprocess.TimeoutExpired, OSError, ValueError) as e:
            logger.warning("get_gpu_stats failed: %s", e)
            return None

    # ── Network / ports ──────────────────────────────────────────────

    def find_port_listeners(self, port: int) -> list[int]:
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return [int(p) for p in result.stdout.strip().splitlines() if p.strip()]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
            pass
        return []

    # ── Utility ──────────────────────────────────────────────────────

    def get_lan_ip(self) -> str:
        try:
            result = subprocess.run(
                ["hostname", "-I"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip().split()[0]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, IndexError):
            pass
        return "127.0.0.1"

    # ── Credentials ─────────────────────────────────────────────────

    def get_claude_credentials(self) -> dict | None:
        """Read credentials from ~/.claude/.credentials.json (Linux file-based storage)."""
        import json
        creds_path = os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json")
        try:
            with open(creds_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
