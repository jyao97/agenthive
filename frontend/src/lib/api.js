/** Centralized API wrapper for AgentHive. */

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

async function request(url, opts = {}) {
  const headers = { "Content-Type": "application/json", ...opts.headers };
  const token = getAuthToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${url}`, { ...opts, headers });

  if (res.status === 401) {
    clearAuthToken();
    // Redirect to login if not already there
    if (window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new Error("Not authenticated");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `HTTP ${res.status}`);
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

// --- Tasks (agent-sourced: each USER message = one task) ---
export const fetchTasks = (params = "") =>
  request(`/api/tasks${params ? `?${params}` : ""}`);
export const fetchTask = (id) => request(`/api/tasks/${id}`);

// --- Agents ---
export const fetchAgents = (params = "") =>
  request(`/api/agents${params ? `?${params}` : ""}`);
export const fetchAgent = (id) => request(`/api/agents/${id}`);
export const fetchUnreadCount = () => request("/api/agents/unread");
export const createAgent = (data) =>
  request("/api/agents", { method: "POST", body: JSON.stringify(data) });
export const renameAgent = (id, name) =>
  request(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify({ name }) });
export const launchTmuxAgent = (data) =>
  request("/api/agents/launch-tmux", { method: "POST", body: JSON.stringify(data) });
export const scanAgents = () =>
  request("/api/agents/scan", { method: "POST" });
export const stopAgent = (id) =>
  request(`/api/agents/${id}`, { method: "DELETE" });
export const resumeAgent = (id) =>
  request(`/api/agents/${id}/resume`, { method: "POST" });
export const fetchMessages = (agentId, limit = 100) =>
  request(`/api/agents/${agentId}/messages?limit=${limit}`);
export const sendMessage = (agentId, content, { queue = false, scheduled_at = null } = {}) =>
  request(`/api/agents/${agentId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content, queue, scheduled_at }),
  });
export const markAgentRead = (agentId) =>
  request(`/api/agents/${agentId}/read`, { method: "PUT" });
export const cancelMessage = (agentId, messageId) =>
  request(`/api/agents/${agentId}/messages/${messageId}`, { method: "DELETE" });
export const updateMessage = (agentId, messageId, data) =>
  request(`/api/agents/${agentId}/messages/${messageId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
// --- Health ---
export const fetchHealth = () => request("/api/health");

// --- Git ---
export const fetchGitLog = (project, limit = 30) =>
  request(`/api/git/${e(project)}/log?limit=${limit}`);
export const fetchGitBranches = (project) =>
  request(`/api/git/${e(project)}/branches`);
export const fetchGitStatus = (project) =>
  request(`/api/git/${e(project)}/status`);
export const mergeGitBranch = (project, branch) =>
  request(`/api/git/${e(project)}/merge/${e(branch)}`, { method: "POST" });

// --- System ---
export const fetchSystemStats = () => request("/api/system/stats");
export const fetchProcesses = () => request("/api/processes");

// --- Voice ---
export async function transcribeVoice(audioBlob, mimeType) {
  // Pick file extension matching actual format (Safari = mp4, Chrome = webm)
  const ext = mimeType && mimeType.includes("mp4") ? "mp4"
    : mimeType && mimeType.includes("ogg") ? "ogg"
    : "webm";
  const formData = new FormData();
  formData.append("file", audioBlob, `recording.${ext}`);
  const headers = {};
  const token = getAuthToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${BASE}/api/voice`, {
    method: "POST",
    body: formData,
    headers,
  });
  if (res.status === 401) {
    clearAuthToken();
    if (window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `Voice API error (${res.status})`);
  }
  return res.json();
}
