const MUTED_KEY = "agenthive-muted-agents";
const AGENTS_NOTIF_KEY = "agenthive-agents-notifications-enabled";
const TASKS_NOTIF_KEY = "agenthive-tasks-notifications-enabled";

/** Get the set of muted agent IDs from localStorage. */
function getMutedAgents() {
  try {
    const v = localStorage.getItem(MUTED_KEY);
    return v ? new Set(JSON.parse(v)) : new Set();
  } catch (err) {
    console.warn("getMutedAgents: failed to read muted agents:", err);
    return new Set();
  }
}

/** Check if a specific agent is muted. */
export function isAgentMuted(agentId) {
  return getMutedAgents().has(agentId);
}

/** Set mute state for a specific agent. */
export function setAgentMuted(agentId, muted) {
  const set = getMutedAgents();
  if (muted) set.add(agentId);
  else set.delete(agentId);
  localStorage.setItem(MUTED_KEY, JSON.stringify([...set]));
  window.dispatchEvent(new CustomEvent("agent-mute-changed", { detail: { agentId, muted } }));
}

/** Check if agent notifications are globally enabled. */
export function isAgentNotificationsEnabled() {
  const v = localStorage.getItem(AGENTS_NOTIF_KEY);
  return v !== "0";
}

/** Set global agent notifications enabled state. */
export function setAgentNotificationsEnabled(enabled) {
  localStorage.setItem(AGENTS_NOTIF_KEY, enabled ? "1" : "0");
}

/** Check if task notifications are globally enabled. */
export function isTaskNotificationsEnabled() {
  const v = localStorage.getItem(TASKS_NOTIF_KEY);
  return v !== "0";
}

/** Set global task notifications enabled state. */
export function setTaskNotificationsEnabled(enabled) {
  localStorage.setItem(TASKS_NOTIF_KEY, enabled ? "1" : "0");
}

/** Agents currently being viewed (across all panes / tabs). */
const _viewingAgents = new Set();
/** Number of active TasksPage / TaskDetailPage instances viewing tasks. */
let _viewingTasksCount = 0;

export function registerViewing(agentId) { if (agentId) _viewingAgents.add(agentId); }
export function unregisterViewing(agentId) { if (agentId) _viewingAgents.delete(agentId); }
export function registerViewingTasks() { _viewingTasksCount++; }
export function unregisterViewingTasks() { _viewingTasksCount = Math.max(0, _viewingTasksCount - 1); }

const _notifiedAgents = new Set();
const _streamingAgents = new Set();
const _pendingMessageNotifications = new Map();
const _pendingMessageTimers = new Map();
const PENDING_FLUSH_DELAY_MS = 12000;
const PENDING_FLUSH_RETRY_MS = 5000;
const PENDING_FLUSH_MAX_WAIT_MS = 45000;

export function clearAgentNotified(agentId) {
  _notifiedAgents.delete(agentId);
  clearPendingNotification(agentId);
}

function shouldSuppressNotification(agentId, allowRepeat = false) {
  if (!agentId) return false;
  if (_viewingAgents.has(agentId) && document.visibilityState === "visible") return true;
  if (isAgentMuted(agentId)) return true;
  if (!allowRepeat && _notifiedAgents.has(agentId)) return true;
  return false;
}

function showNativeNotification(eventType, agentId, title, body, allowRepeat = false) {
  if (shouldSuppressNotification(agentId, allowRepeat)) return;
  if (agentId) _notifiedAgents.add(agentId);

  try {
    const tag = `${eventType}-${agentId || "unknown"}`;
    const n = new Notification(title, { body, tag, renotify: true });
    n.onclick = () => { window.focus(); n.close(); };
    setTimeout(() => n.close(), 8000);
  } catch (err) {
    console.warn("showNativeNotification: failed:", err);
  }
}

function clearPendingNotification(agentId) {
  _pendingMessageNotifications.delete(agentId);
  const timer = _pendingMessageTimers.get(agentId);
  if (timer) {
    clearTimeout(timer);
    _pendingMessageTimers.delete(agentId);
  }
}

function flushPendingNotification(agentId) {
  const pending = _pendingMessageNotifications.get(agentId);
  if (!pending) return;
  clearPendingNotification(agentId);
  showNativeNotification("new_message", agentId, pending.title, pending.body);
}

function schedulePendingFlush(agentId, delayMs) {
  const prevTimer = _pendingMessageTimers.get(agentId);
  if (prevTimer) clearTimeout(prevTimer);
  const timer = setTimeout(() => {
    _pendingMessageTimers.delete(agentId);
    const pending = _pendingMessageNotifications.get(agentId);
    if (!pending) return;
    const elapsed = Date.now() - (pending.deferredAt || Date.now());
    if (_streamingAgents.has(agentId) && elapsed < PENDING_FLUSH_MAX_WAIT_MS) {
      schedulePendingFlush(agentId, PENDING_FLUSH_RETRY_MS);
      return;
    }
    _streamingAgents.delete(agentId);
    flushPendingNotification(agentId);
  }, delayMs);
  _pendingMessageTimers.set(agentId, timer);
}

function deferPendingNotification(agentId, payload) {
  const existing = _pendingMessageNotifications.get(agentId);
  _pendingMessageNotifications.set(agentId, {
    ...payload,
    deferredAt: existing?.deferredAt || Date.now(),
  });
  schedulePendingFlush(agentId, PENDING_FLUSH_DELAY_MS);
}

export function showBrowserNotification(event) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;

  const d = event.data || {};
  const agentId = d.agent_id;

  if (event.type === "generating_agents") {
    const ids = d.agent_ids || [];
    for (const id of ids) _streamingAgents.add(id);
    return;
  }

  if (event.type === "agent_stream") {
    if (agentId) _streamingAgents.add(agentId);
    return;
  }

  if (event.type === "agent_stream_end") {
    if (!agentId) return;
    _streamingAgents.delete(agentId);
    flushPendingNotification(agentId);
    return;
  }

  if (event.type === "new_message") {
    if (!isAgentNotificationsEnabled()) return;
    if (shouldSuppressNotification(agentId)) return;
    const title = d.agent_name || `Agent ${agentId?.slice(0, 8)}`;
    const body = d.project ? `New message (${d.project})` : "New message";
    if (agentId && _streamingAgents.has(agentId)) {
      deferPendingNotification(agentId, { title, body });
      return;
    }
    showNativeNotification(event.type, agentId, title, body);
    return;
  }

  if (event.type === "agent_update" && d.status === "ERROR") {
    if (!isAgentNotificationsEnabled()) return;
    if (agentId) {
      _streamingAgents.delete(agentId);
      clearPendingNotification(agentId);
      _notifiedAgents.delete(agentId);
    }
    const title = "Agent error";
    const body = d.agent_name || agentId?.slice(0, 8);
    showNativeNotification(event.type, agentId, title, body, true);
    return;
  }

  if (event.type === "task_update") {
    if (!isTaskNotificationsEnabled()) return;
    const status = d.status;
    if (!status || !["COMPLETE", "FAILED", "TIMEOUT"].includes(status)) return;
    if (_viewingTasksCount > 0 && document.visibilityState === "visible") return;
    const emoji = status === "COMPLETE" ? "\u2705" : "\u274c";
    const title = `${emoji} Task ${status.charAt(0) + status.slice(1).toLowerCase()}`;
    const body = d.title || d.task_id?.slice(0, 8) || "Task update";
    try {
      const tag = `task_update-${d.task_id || "unknown"}`;
      const n = new Notification(title, { body, tag, renotify: true });
      n.onclick = () => { window.focus(); n.close(); };
      setTimeout(() => n.close(), 8000);
    } catch (err) {
      console.warn("showBrowserNotification: failed:", err);
    }
    return;
  }
}
