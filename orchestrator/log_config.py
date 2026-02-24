"""Logging configuration — file + console with daily rotation."""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def setup_logging():
    """Configure root logger with console + rotating file handlers."""
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # File handler — daily rotation, keep 7 days
    log_file = os.path.join(LOG_DIR, "orchestrator.log")
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=7,
        utc=True,
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Quiet down noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("docker").setLevel(logging.WARNING)

    logging.info("Logging configured: level=%s, file=%s", LOG_LEVEL, log_file)


def save_worker_log(task_id: str, log_content: str):
    """Persist a worker's stream log to the logs volume."""
    worker_log_dir = os.path.join(LOG_DIR, "workers")
    os.makedirs(worker_log_dir, exist_ok=True)
    path = os.path.join(worker_log_dir, f"worker-{task_id}.json")
    with open(path, "w") as f:
        f.write(log_content)


def get_recent_logs(level: str = "", limit: int = 100) -> list[str]:
    """Read recent log lines from the orchestrator log file, optionally filtered by level."""
    log_file = os.path.join(LOG_DIR, "orchestrator.log")
    if not os.path.isfile(log_file):
        return []

    with open(log_file) as f:
        lines = f.readlines()

    if level:
        level_upper = level.upper()
        lines = [l for l in lines if f"[{level_upper}]" in l]

    return [l.rstrip() for l in lines[-limit:]]
