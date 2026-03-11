"""Tests for the unified notify() gateway."""

from unittest.mock import patch

from notify import notify


# --- notify_at: always sends, no guards ---

@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=True)
def test_notify_at_always_sends(mock_enabled, mock_send):
    notify("notify_at", "", "Reminder", "Check task", url="/tasks/1")
    mock_send.assert_called_once_with("Reminder", "Check task", "/tasks/1")
    mock_enabled.assert_not_called()


@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=False)
def test_notify_at_ignores_global_toggle(mock_enabled, mock_send):
    notify("notify_at", "", "Reminder", "body")
    mock_send.assert_called_once()


# --- task_complete: only global toggle ---

@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=True)
def test_task_complete_sends(mock_enabled, mock_send):
    notify("task_complete", "agent1", "Done", "body", "/tasks/1")
    mock_enabled.assert_called_once_with("tasks")
    mock_send.assert_called_once()


@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=False)
def test_task_complete_respects_global_toggle(mock_enabled, mock_send):
    notify("task_complete", "agent1", "Done", "body")
    mock_send.assert_not_called()


@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=True)
def test_task_complete_ignores_muted(mock_enabled, mock_send):
    """task_complete is not affected by per-agent mute."""
    notify("task_complete", "agent1", "Done", "body", muted=True)
    mock_send.assert_called_once()


@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=True)
def test_task_complete_ignores_in_use(mock_enabled, mock_send):
    """task_complete is not affected by in-use detection."""
    notify("task_complete", "agent1", "Done", "body", in_use=True)
    mock_send.assert_called_once()


# --- message: full guard chain ---

@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=True)
def test_message_sends_when_clear(mock_enabled, mock_send):
    notify("message", "agent1", "Agent", "hello", muted=False, in_use=False)
    mock_send.assert_called_once()


@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=True)
def test_message_suppressed_by_mute(mock_enabled, mock_send):
    notify("message", "agent1", "Agent", "hello", muted=True, in_use=False)
    mock_send.assert_not_called()


@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=True)
def test_message_suppressed_by_in_use(mock_enabled, mock_send):
    notify("message", "agent1", "Agent", "hello", muted=False, in_use=True)
    mock_send.assert_not_called()


@patch("notify.send_push_notification")
@patch("notify.is_notification_enabled", return_value=False)
def test_message_suppressed_by_global_toggle(mock_enabled, mock_send):
    notify("message", "agent1", "Agent", "hello", muted=False, in_use=False)
    mock_send.assert_not_called()


# --- unknown channel ---

@patch("notify.send_push_notification")
def test_unknown_channel_does_not_send(mock_send):
    notify("unknown", "agent1", "Title", "body")
    mock_send.assert_not_called()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
