/** Centralized API wrapper for AgentHive. */

import { calibrate } from "./serverTime";

const BASE = "";
const TOKEN_KEY = "cc-auth-token";

export function getAuthToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken() {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * Low-level fetch with auth headers. Returns the raw Response object.
 * Use this when you need to inspect status codes or handle non-JSON responses.
 */
export async function authedFetch(url, opts = {}) {
  const headers = { ...opts.headers };
  const token = getAuthToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return fetch(`${BASE}${url}`, { ...opts, headers });
}

/** Handle 401 by clearing auth and emitting event. */
function handle401() {
  clearAuthToken();
  if (window.location.pathname !== "/login") {
    window.dispatchEvent(new Event("auth-expired"));
  }
  throw new Error("Not authenticated");
}

async function request(url, opts = {}) {
  const headers = { "Content-Type": "application/json", ...opts.headers };
  const token = getAuthToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${url}`, { ...opts, headers });

  if (res.status === 401) handle401();

  // Calibrate clock offset from HTTP Date header (works before WS connects)
  const serverDate = res.headers.get("Date");
  if (serverDate) calibrate(serverDate);

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    const msg = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map(e => e.msg || JSON.stringify(e)).join("; ")
      : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return res.json();
}

// --- Auth ---
export const authCheck = () =>
  request("/api/auth/check", { method: "POST" });
export const authLogin = (password) =>
  request("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
export const authSetPassword = (password) =>
  request("/api/auth/set-password", { method: "POST", body: JSON.stringify({ password }) });
export const authChangePassword = (current_password, new_password) =>
  request("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });

// --- Projects ---
const e = encodeURIComponent;
export const fetchProjects = () => request("/api/projects");
export const createProject = (data) =>
  request("/api/projects", { method: "POST", body: JSON.stringify(data) });
export const fetchAllFolders = () => request("/api/projects/folders");
export const scanProjects = () =>
  request("/api/projects/scan", { method: "POST" });
export const renameProject = (name, newName, displayName) =>
  request(`/api/projects/${e(name)}/rename`, { method: "PUT", body: JSON.stringify({ new_name: newName, display_name: displayName || undefined }) });
export const archiveProject = (name) =>
  request(`/api/projects/${e(name)}/archive`, { method: "POST" });
export const fetchTrashFolders = () => request("/api/projects/trash");
export const deleteProject = (name) =>
  request(`/api/projects/${e(name)}`, { method: "DELETE" });
export const deleteTrashFolder = (name) =>
  request(`/api/projects/trash/${e(name)}`, { method: "DELETE" });
export const restoreTrashFolder = (name) =>
  request(`/api/projects/trash/${e(name)}/restore`, { method: "POST" });
export const fetchProjectAgents = (name, params = "") =>
  request(`/api/projects/${e(name)}/agents${params ? `?${params}` : ""}`);
export const fetchProjectWorktrees = (name) =>
  request(`/api/projects/${e(name)}/worktrees`);
export const fetchProjectSessions = (name) =>
  request(`/api/projects/${e(name)}/sessions`);
export const starSession = (project, sessionId) =>
  request(`/api/projects/${e(project)}/sessions/${e(sessionId)}/star`, { method: "PUT" });
export const unstarSession = (project, sessionId) =>
  request(`/api/projects/${e(project)}/sessions/${e(sessionId)}/star`, { method: "DELETE" });
export const fetchProjectFile = (project, path) =>
  request(`/api/projects/${e(project)}/file?path=${e(path)}`);
export const updateProjectFile = (project, path, content) =>
  request(`/api/projects/${e(project)}/file`, { method: "PUT", body: JSON.stringify({ path, content }) });
export const fetchProjectTree = (project, depth = 3) =>
  request(`/api/projects/${e(project)}/tree?depth=${depth}`);
export const browseProjectFile = (project, path) =>
  request(`/api/projects/${e(project)}/browse?path=${e(path)}`);
export const refreshClaudeMd = (project) =>
  request(`/api/projects/${e(project)}/refresh-claudemd`, { method: "POST" });
export const refreshClaudeMdStatus = (project) =>
  request(`/api/projects/${e(project)}/refresh-claudemd/status`);
export const discardClaudeMd = (project) =>
  request(`/api/projects/${e(project)}/refresh-claudemd`, { method: "DELETE" });
export const applyClaudeMd = (project, payload) =>
  request(`/api/projects/${e(project)}/apply-claudemd`, { method: "POST", body: JSON.stringify(payload) });
export const fetchClaudeMdPending = () =>
  request("/api/projects/claudemd-pending");
export const summarizeProgress = (project) =>
  request(`/api/projects/${e(project)}/summarize-progress`, { method: "POST" });
export const summarizeProgressStatus = (project) =>
  request(`/api/projects/${e(project)}/summarize-progress/status`);
export const discardProgressSummary = (project) =>
  request(`/api/projects/${e(project)}/summarize-progress`, { method: "DELETE" });
export const applyProgressSummary = (project) =>
  request(`/api/projects/${e(project)}/apply-progress`, { method: "POST" });
export const updateProjectSettings = (project, settings) =>
  request(`/api/projects/${e(project)}/settings`, { method: "PATCH", body: JSON.stringify(settings) });
export const rebuildInsights = (project) =>
  request(`/api/projects/${e(project)}/rebuild-insights`, { method: "POST" });

// --- Tasks ---
export const fetchTasksV2 = (params = "") =>
  request(`/api/v2/tasks${params ? `?${params}` : ""}`);
export const fetchTaskCounts = (project) =>
  request(`/api/v2/tasks/counts${project ? `?project=${encodeURIComponent(project)}` : ""}`);
export const fetchTaskV2 = (id) => request(`/api/v2/tasks/${id}`);
export const createTaskV2 = (data) =>
  request("/api/v2/tasks", { method: "POST", body: JSON.stringify(data) });
export const updateTaskV2 = (id, data) =>
  request(`/api/v2/tasks/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const reorderTasks = (taskIds) =>
  request("/api/v2/tasks/reorder", { method: "PUT", body: JSON.stringify({ task_ids: taskIds }) });
export const dispatchTask = (id) =>
  request(`/api/v2/tasks/${id}/dispatch`, { method: "POST" });
export const cancelTask = (id) =>
  request(`/api/v2/tasks/${id}/cancel`, { method: "POST" });
export const completeTask = (id) =>
  request(`/api/v2/tasks/${id}/complete`, { method: "POST" });
export const fetchQueueStatus = () =>
  request(`/api/v2/tasks/queue?tz_offset=${new Date().getTimezoneOffset()}`);

// --- Agents ---
export const fetchAgents = (params = "") =>
  request(`/api/agents${params ? `?${params}` : ""}`);
export const fetchAgent = (id) => request(`/api/agents/${id}`);
export const fetchUnreadCount = () => request("/api/agents/unread");
export const createAgent = (data) =>
  request("/api/agents", { method: "POST", body: JSON.stringify(data) });
export const renameAgent = (id, name) =>
  request(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify({ name }) });
export const updateAgent = (id, data) =>
  request(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const launchTmuxAgent = (data) =>
  request("/api/agents/launch-tmux", { method: "POST", body: JSON.stringify(data) });
export const scanAgents = () =>
  request("/api/agents/scan", { method: "POST" });
export const fetchUnlinkedSessions = () =>
  request("/api/unlinked-sessions");
export const adoptUnlinkedSession = (sessionId, data) =>
  request(`/api/unlinked-sessions/${sessionId}/adopt`, {
    method: "POST",
    body: JSON.stringify(data),
  });
export const stopAgent = (id, { generateSummary = false, taskComplete = true, incompleteReason = null } = {}) => {
  const params = new URLSearchParams();
  if (generateSummary) params.set("generate_summary", "true");
  if (!taskComplete) params.set("task_complete", "false");
  if (incompleteReason) params.set("incomplete_reason", incompleteReason);
  const qs = params.toString();
  return request(`/api/agents/${id}${qs ? "?" + qs : ""}`, { method: "DELETE" });
};
export const deleteAgent = (id) =>
  request(`/api/agents/${id}/permanent`, { method: "DELETE" });
export const resumeAgent = (id, body = null) =>
  request(`/api/agents/${id}/resume`, {
    method: "POST",
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
export const fetchMessages = (agentId, { limit = 50, before, after } = {}) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (before) params.set("before", before);
  if (after) params.set("after", after);
  return request(`/api/agents/${agentId}/messages?${params}`);
};
export const sendMessage = (agentId, content, { queue = false, scheduled_at = null } = {}) =>
  request(`/api/agents/${agentId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content, queue, scheduled_at }),
  });
export const fetchToolActivities = (agentId) =>
  request(`/api/agents/${agentId}/tool-activities`);
export const markAgentRead = (agentId) =>
  request(`/api/agents/${agentId}/read`, { method: "PUT" });
export const markAllAgentsRead = () =>
  request("/api/agents/read-all", { method: "PUT" });
export const cancelMessage = (agentId, messageId) =>
  request(`/api/agents/${agentId}/messages/${messageId}`, { method: "DELETE" });
export const updateMessage = (agentId, messageId, data) =>
  request(`/api/agents/${agentId}/messages/${messageId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
// --- Interactive Answer (AskUserQuestion / ExitPlanMode) ---
export const answerAgent = (agentId, payload) =>
  request(`/api/agents/${agentId}/answer`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const escapeAgent = (agentId) =>
  request(`/api/agents/${agentId}/escape`, { method: "POST" });
// --- Tool Permission Approval ---
export const respondPermission = (agentId, requestId, payload) =>
  request(`/api/agents/${agentId}/permission/${requestId}/respond`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const fetchPendingPermissions = (agentId) =>
  request(`/api/agents/${agentId}/permissions/pending`);
// --- Agent Insight Suggestions ---
export const fetchAgentSuggestions = (agentId) =>
  request(`/api/agents/${agentId}/suggestions`);
export const applyAgentSuggestions = (agentId, payload) =>
  request(`/api/agents/${agentId}/apply-suggestions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const discardAgentSuggestions = (agentId) =>
  request(`/api/agents/${agentId}/suggestions`, { method: "DELETE" });

// --- Message search ---
export const searchMessages = (query, { project, role, limit } = {}) => {
  const params = new URLSearchParams({ q: query });
  if (project) params.set("project", project);
  if (role) params.set("role", role);
  if (limit) params.set("limit", String(limit));
  return request(`/api/messages/search?${params}`);
};

// --- Health ---
export const fetchHealth = () => request("/api/health");

// --- Git ---
export const fetchGitLog = (project, limit = 30) =>
  request(`/api/git/${e(project)}/log?limit=${limit}`);
export const fetchGitBranches = (project) =>
  request(`/api/git/${e(project)}/branches`);
export const fetchGitStatus = (project) =>
  request(`/api/git/${e(project)}/status`);
export const fetchGitWorktrees = (project) =>
  request(`/api/git/${e(project)}/worktrees`);
export const checkoutBranch = (project, branch) =>
  request(`/api/git/${e(project)}/checkout/${e(branch)}`, { method: "POST" });
// --- System ---
export const fetchSystemStats = () => request("/api/system/stats");
export const fetchStorageStats = () => request("/api/system/storage");
export const fetchTokenUsage = () => request("/api/system/token-usage");
export const restartServer = () => request("/api/system/restart", { method: "POST" });
export const scanOrphans = () => request("/api/system/orphans/scan");
export const cleanOrphans = () => request("/api/system/orphans/clean", { method: "POST" });
export const fetchBackupStatus = () => request("/api/system/backup");
export const purgeBackups = () => request("/api/system/backup", { method: "DELETE" });
export const fetchProcesses = () => request("/api/processes");

// --- Notification Settings ---
export const fetchNotificationSettings = () => request("/api/settings/notifications");
export const updateNotificationSettings = (data) =>
  request("/api/settings/notifications", { method: "PUT", body: JSON.stringify(data) });

// --- FormData helper (shared by voice + upload) ---
async function formDataRequest(url, formData, errorLabel = "Request") {
  const headers = {};
  const token = getAuthToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${BASE}${url}`, {
    method: "POST",
    body: formData,
    headers,
  });
  if (res.status === 401) handle401();
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `${errorLabel} (${res.status})`);
  }
  return res.json();
}

// --- Voice ---
export async function transcribeVoice(audioBlob, mimeType) {
  // Pick file extension matching actual format (Safari = mp4, Chrome = webm)
  const ext = mimeType && mimeType.includes("mp4") ? "mp4"
    : mimeType && mimeType.includes("ogg") ? "ogg"
    : "webm";
  const formData = new FormData();
  formData.append("file", audioBlob, `recording.${ext}`);
  return formDataRequest("/api/voice", formData, "Voice API error");
}

export async function uploadFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  return formDataRequest("/api/upload", formData, "Upload failed");
}

/**
 * Download a file reliably across platforms including iOS Safari PWA.
 * Returns: "shared" | "downloaded" | "cancelled" — or throws on network error.
 */
export async function downloadFile(url, filename) {
  const dlUrl = url + (url.includes("?") ? "&" : "?") + "download=1";

  // Only use Web Share API on mobile PWA standalone mode (iOS needs it; desktop should direct-download)
  const isStandalonePWA = window.matchMedia("(display-mode: standalone)").matches
    || window.navigator.standalone === true;
  const isTouchDevice = "ontouchend" in document;
  const useShareAPI = isStandalonePWA && isTouchDevice;

  const resp = await authedFetch(dlUrl);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const blob = await resp.blob();

  // Strategy 1: Web Share API — the only reliable method in iOS Safari PWA standalone mode
  if (useShareAPI) {
    const file = new File([blob], filename, { type: blob.type || "application/octet-stream" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file] });
        return "shared";
      } catch (err) {
        if (err.name === "AbortError") return "cancelled";
        throw err;
      }
    }
  }

  // Strategy 2: blob URL + anchor click (desktop browsers, Android)
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
  return "downloaded";
}

export async function generateWorktreeName(prompt) {
  const res = await authedFetch("/api/worktree-name", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.name || null;
}
