"""Unified notification gateway — three-tier channel model.

Channels:
  notify_at      — user-initiated reminder, always sends
  task_complete  — agent/task lifecycle terminal event, only respects global toggle
  message        — conversational content, respects global toggle + per-agent mute + in-use

Task-linked agents default to muted=True (set at agent creation), so they only
receive task_complete notifications.  If a user manually unmutes a task agent,
both channels fire independently — no dedup needed.
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
) -> None:
    """Send a notification through the appropriate channel.

    Args:
        channel: "notify_at" | "task_complete" | "message"
        agent_id: agent ID (empty string for task-only reminders)
        title: notification title
        body: notification body
        url: deep link URL
        in_use: whether the user is actively viewing this agent (message only)
        muted: whether this agent's message notifications are muted (message only)
    """
    if channel == "notify_at":
        logger.info("notify: %s → SEND (always)", channel)
        _send(title, body, url)
        return

    if channel == "task_complete":
        if not is_notification_enabled("tasks"):
            logger.info("notify: %s agent=%s → SKIP (global toggle off)", channel, agent_id[:8])
            return
        logger.info("notify: %s agent=%s → SEND", channel, agent_id[:8])
        _send(title, body, url)
        return

    if channel == "message":
        if not is_notification_enabled("agents"):
            logger.info("notify: %s agent=%s → SKIP (global toggle off)", channel, agent_id[:8])
            return
        if muted:
            logger.info("notify: %s agent=%s → SKIP (muted)", channel, agent_id[:8])
            return
        if in_use:
            logger.info("notify: %s agent=%s → SKIP (in-use)", channel, agent_id[:8])
            return
        logger.info("notify: %s agent=%s → SEND", channel, agent_id[:8])
        _send(title, body, url)
        return

    logger.warning("notify: unknown channel %r — dropping", channel)


def _send(title: str, body: str, url: str) -> None:
    """Best-effort send via all backends."""
    try:
        send_push_notification(title, body, url)
    except Exception:
        logger.warning("notify: send failed", exc_info=True)
