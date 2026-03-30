"""Shared utility helpers — imported across the orchestrator package."""

from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n... [truncated]"


def is_interrupt_message(text: str | None) -> bool:
    """Check if the entire message is a Claude Code interrupt marker.

    Matches content like "[Request interrupted by user]" or
    "[Request interrupted by user for tool use]" — the whole
    message must be a single bracket-enclosed string containing
    "interrupt" (case-insensitive).
    """
    if not text:
        return False
    stripped = text.strip()
    return (
        stripped.startswith("[")
        and stripped.endswith("]")
        and "interrupt" in stripped.lower()
    )
