"""Shared utility helpers — imported across the orchestrator package."""

from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n... [truncated]"
