"""AgentHive configuration — loaded from environment variables."""

import os

# Project root: one level up from this file (cc-orchestrator/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load .env so the backend works regardless of how it's started
# (run.sh, bare uvicorn, systemd, etc.)
from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"), override=False)

# run.sh maps HOST_PROJECTS_DIR → PROJECTS_DIR; replicate that here
# so direct starts work identically.
if not os.getenv("PROJECTS_DIR") and os.getenv("HOST_PROJECTS_DIR"):
    os.environ["PROJECTS_DIR"] = os.environ["HOST_PROJECTS_DIR"]


def _resolve(path: str) -> str:
    """Resolve a path: absolute paths stay as-is, relative paths are
    resolved against the project root (not the CWD)."""
    if os.path.isabs(path):
        return path
    return os.path.join(_PROJECT_ROOT, path)


# Worker config
MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", "5"))
TASK_TIMEOUT_SECONDS = int(os.getenv("TASK_TIMEOUT_SECONDS", "1800"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MAX_IDLE_AGENTS = int(os.getenv("MAX_IDLE_AGENTS", "20"))
CC_MODEL = os.getenv("CC_MODEL", "claude-opus-4-6")

# Claude CLI binary
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")

# Claude home directory (single source of truth for all ~/.claude paths)
CLAUDE_HOME = os.path.expanduser(os.getenv("CLAUDE_HOME", "~/.claude"))

# Claude history file (all past conversations)
CLAUDE_HISTORY_PATH = os.getenv("CLAUDE_HISTORY_PATH", os.path.join(CLAUDE_HOME, "history.jsonl"))

# Claude credentials file (for OAuth token usage queries)
CLAUDE_CREDENTIALS_PATH = os.path.expanduser(
    os.getenv("CLAUDE_CREDENTIALS_PATH", os.path.join(CLAUDE_HOME, ".credentials.json"))
)

# Projects directory (host path)
PROJECTS_DIR = os.getenv("PROJECTS_DIR", os.getenv("HOST_PROJECTS_DIR", ""))

# Voice
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Backup
BACKUP_INTERVAL_HOURS = int(os.getenv("BACKUP_INTERVAL_HOURS", "1"))
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "48"))

# Auth
AUTH_TIMEOUT_MINUTES = int(os.getenv("AUTH_TIMEOUT_MINUTES", "30"))

# Database
DB_PATH = _resolve(os.getenv("DB_PATH", "data/orchestrator.db"))

# Logs and backups directories
LOG_DIR = _resolve(os.getenv("LOG_DIR", "logs"))
BACKUP_DIR = _resolve(os.getenv("BACKUP_DIR", "backups"))

# Session cache
SESSION_CACHE_INTERVAL = int(os.getenv("SESSION_CACHE_INTERVAL", "30"))

# Project configs
PROJECT_CONFIGS_PATH = _resolve(os.getenv("PROJECT_CONFIGS_PATH", "project-configs"))

# VAPID (Web Push)
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:agenthive@example.com")

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
