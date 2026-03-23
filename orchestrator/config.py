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

# Valid model names — keep in sync with frontend MODEL_OPTIONS
VALID_MODELS = {
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
}

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
VOICE_REFINE_MODEL = os.getenv("VOICE_REFINE_MODEL", "gpt-4o-mini")

# Backup
BACKUP_ENABLED = os.getenv("BACKUP_ENABLED", "0").strip().lower() in ("1", "true", "yes")
BACKUP_INTERVAL_HOURS = int(os.getenv("BACKUP_INTERVAL_HOURS", "24"))
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "30"))

# Auth
AUTH_TIMEOUT_MINUTES = int(os.getenv("AUTH_TIMEOUT_MINUTES", "30"))

# Database
DB_PATH = _resolve(os.getenv("DB_PATH", "data/orchestrator.db"))

# Logs and backups directories
LOG_DIR = _resolve(os.getenv("LOG_DIR", "logs"))
BACKUP_DIR = _resolve(os.getenv("BACKUP_DIR", "backups"))  # also configurable via .env BACKUP_DIR=/your/path

# Delay (seconds) before waking sync after a hook returns, giving Claude
# time to flush the JSONL entry to disk.
JSONL_FLUSH_DELAY = float(os.getenv("JSONL_FLUSH_DELAY", "0.15"))

# Session cache
SESSION_CACHE_INTERVAL = int(os.getenv("SESSION_CACHE_INTERVAL", "30"))

# Project configs
PROJECT_CONFIGS_PATH = _resolve(os.getenv("PROJECT_CONFIGS_PATH", "project-configs"))

# VAPID (Web Push)
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:agenthive@example.com")

# Uploads
UPLOADS_DIR = os.path.expanduser(os.getenv("UPLOADS_DIR", "~/.agenthive/uploads"))

# CORS
CORS_ORIGINS = [
    o.strip() for o in
    os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8080").split(",")
    if o.strip()
]
