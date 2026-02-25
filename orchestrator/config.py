"""AgentHive configuration — loaded from environment variables."""

import os


# Worker config
MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", "5"))
TASK_TIMEOUT_SECONDS = int(os.getenv("TASK_TIMEOUT_SECONDS", "1800"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MAX_IDLE_AGENTS = int(os.getenv("MAX_IDLE_AGENTS", "20"))
CC_MODEL = os.getenv("CC_MODEL", "claude-opus-4-6")

# Claude CLI binary
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")

# Claude history file (all past conversations)
CLAUDE_HISTORY_PATH = os.getenv("CLAUDE_HISTORY_PATH", os.path.expanduser("~/.claude/history.jsonl"))

# Projects directory (host path)
PROJECTS_DIR = os.getenv("PROJECTS_DIR", os.getenv("HOST_PROJECTS_DIR", ""))

# Plan mode
AUTO_APPROVE_TIMEOUT = int(os.getenv("AUTO_APPROVE_TIMEOUT", "0"))  # 0 = disabled

# Voice
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Backup
BACKUP_INTERVAL_HOURS = int(os.getenv("BACKUP_INTERVAL_HOURS", "1"))
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "48"))

# Auth
AUTH_TIMEOUT_MINUTES = int(os.getenv("AUTH_TIMEOUT_MINUTES", "30"))

# Database
DB_PATH = os.getenv("DB_PATH", "./data/orchestrator.db")

# Logs and backups directories
LOG_DIR = os.getenv("LOG_DIR", "./logs")
BACKUP_DIR = os.getenv("BACKUP_DIR", "./backups")

# Session cache
SESSION_CACHE_INTERVAL = int(os.getenv("SESSION_CACHE_INTERVAL", "30"))
CLAUDE_HOME = os.path.expanduser("~/.claude")

# Project configs
PROJECT_CONFIGS_PATH = os.getenv("PROJECT_CONFIGS_PATH", "./project-configs")

# VAPID (Web Push)
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:agenthive@example.com")

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
