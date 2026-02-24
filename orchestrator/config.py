"""CC Orchestrator configuration — loaded from environment variables."""

import os


# Worker config
MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", "5"))
WORKER_CPU_LIMIT = int(os.getenv("WORKER_CPU_LIMIT", "2"))
WORKER_MEM_LIMIT = os.getenv("WORKER_MEM_LIMIT", "4g")
TASK_TIMEOUT_SECONDS = int(os.getenv("TASK_TIMEOUT_SECONDS", "600"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MAX_IDLE_AGENTS = int(os.getenv("MAX_IDLE_AGENTS", "20"))
CC_MODEL = os.getenv("CC_MODEL", "claude-sonnet-4-5-20250514")

# Plan mode
AUTO_APPROVE_TIMEOUT = int(os.getenv("AUTO_APPROVE_TIMEOUT", "0"))  # 0 = disabled

# Voice
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Backup
BACKUP_INTERVAL_HOURS = int(os.getenv("BACKUP_INTERVAL_HOURS", "1"))
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "48"))

# Database
DB_PATH = os.getenv("DB_PATH", "/app/db/orchestrator.db")

# Project configs
PROJECT_CONFIGS_PATH = os.getenv("PROJECT_CONFIGS_PATH", "/app/project-configs")

# Worker image
WORKER_IMAGE = os.getenv("WORKER_IMAGE", "cc-worker:latest")
WORKER_NETWORK = os.getenv("WORKER_NETWORK", "cc-worker-net")

# Host paths (must be HOST paths, not container paths — used when creating worker containers)
# Projects directory on the host (bind-mounted into workers as /projects)
HOST_PROJECTS_DIR = os.getenv("HOST_PROJECTS_DIR", "")

# Claude auth — 1-year OAuth token from `claude setup-token`
CLAUDE_CODE_OAUTH_TOKEN = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "")

# UID for non-root worker user inside containers
HOST_USER_UID = os.getenv("HOST_USER_UID", "1000")
