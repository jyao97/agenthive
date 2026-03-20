"""Unified notification gateway — three-tier channel model.

Channels:
  notify_at      — user-initiated reminder, always sends
  permission     — permission requests, always sends
  message        — conversational content, respects global toggle + per-agent mute + in-use
"""

import logging

from push import send_push_notification, is_notification_enabled

logger = logging.getLogger("orchestrator.notify")


def notify(
    channel: str,
    agent_id: str,
    title: str,
    body: str,
    url: str = "/",
    *,
    in_use: bool = False,
    muted: bool = False,
) -> str:
    """Send a notification through the appropriate channel.

    Returns decision string: "SEND", "SKIP (reason)", or "DROP (unknown)".
    """
    if channel == "permission":
        # Permission requests are urgent — always send, never suppressed
        logger.info("notify: %s agent=%s → SEND (always)", channel, agent_id[:8])
        _send(title, body, url)
        return "SEND"

    if channel == "notify_at":
        logger.info("notify: %s → SEND (always)", channel)
        _send(title, body, url)
        return "SEND"

    if channel == "task_complete":
        if not is_notification_enabled("tasks"):
            logger.info("notify: %s agent=%s → SKIP (global toggle off)", channel, agent_id[:8])
            return "SKIP (global off)"
        logger.info("notify: %s agent=%s → SEND", channel, agent_id[:8])
        _send(title, body, url)
        return "SEND"

    if channel == "message":
        if not is_notification_enabled("agents"):
            logger.info("notify: %s agent=%s → SKIP (global toggle off)", channel, agent_id[:8])
            return "SKIP (global off)"
        if muted:
            logger.info("notify: %s agent=%s → SKIP (muted)", channel, agent_id[:8])
            return "SKIP (muted)"
        if in_use:
            logger.info("notify: %s agent=%s → SKIP (in-use)", channel, agent_id[:8])
            return "SKIP (in-use)"
        logger.info("notify: %s agent=%s → SEND", channel, agent_id[:8])
        _send(title, body, url)
        return "SEND"

    logger.warning("notify: unknown channel %r — dropping", channel)
    return "DROP (unknown)"


def _send(title: str, body: str, url: str) -> None:
    """Best-effort send via all backends."""
    try:
        send_push_notification(title, body, url)
    except Exception:
        logger.warning("notify: send failed", exc_info=True)
