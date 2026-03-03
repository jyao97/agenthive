import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  fetchAllFolders,
  fetchProjectAgents,
  fetchProjectSessions,
  createAgent,
  launchTmuxAgent,
  createProject,
  deleteProject as deleteProjectApi,
  archiveProject as archiveProjectApi,
  renameProject as renameProjectApi,
  starSession,
  unstarSession,
  scanAgents,
  sendMessage,
  generateWorktreeName,
  uploadFile,
  fetchProjectFile,
  refreshClaudeMd,
  refreshClaudeMdStatus,
  discardClaudeMd,
} from "../lib/api";
import BotIcon from "../components/BotIcon";
import VoiceRecorder from "../components/VoiceRecorder";
import WaveformVisualizer from "../components/WaveformVisualizer";
import SendLaterPicker from "../components/SendLaterPicker";
import useDraft from "../hooks/useDraft";
import useVoiceRecorder from "../hooks/useVoiceRecorder";
import { relativeTime } from "../lib/formatters";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS, MODEL_OPTIONS, modelDisplayName } from "../lib/constants";
import FilterTabs from "../components/FilterTabs";
import ProjectFileModal from "../components/ProjectFileModal";
import ProjectBrowserModal from "../components/ProjectBrowserModal";
import ClaudeMdDiffModal from "../components/ClaudeMdDiffModal";
import useWebSocket from "../hooks/useWebSocket";
import usePageVisible from "../hooks/usePageVisible";

const AGENT_TABS = [
  { key: "starred", label: "Starred" },
  { key: "syncing", label: "Syncing" },
  { key: "active", label: "Active" },
  { key: "stopped", label: "Stopped" },
  { key: "sessions", label: "Sessions" },
];

function projectBotState(proj) {
  if (!proj.active) return "idle";
  if ((proj.agent_active || 0) > 0) return "running";
  if (proj.agent_count > 0) return "completed";
  return "idle";
}

function agentBotState(status) {
  if (status === "EXECUTING" || status === "SYNCING") return "running";
  if (status === "ERROR") return "error";
  if (status === "IDLE") return "completed";
  return "idle";
}

function AgentRow({ agent, onClick, starred, onToggleStar, onError, project, isStreaming }) {
  const statusDot = AGENT_STATUS_COLORS[agent.status] || "bg-gray-500";
  const statusText = AGENT_STATUS_TEXT_COLORS[agent.status] || "text-dim";
  const [copied, setCopied] = useState(false);
  const [starLoading, setStarLoading] = useState(false);

  const handleCopyId = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(agent.id).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const handleStarClick = async (e) => {
    e.stopPropagation();
    if (starLoading) return;
    setStarLoading(true);
    const sessionId = agent.session_id || agent.id;
    try {
      if (starred) {
        await unstarSession(project, sessionId);
      } else {
        await starSession(project, sessionId);
      }
      if (onToggleStar) onToggleStar(sessionId, !starred);
    } catch (err) {
      console.error("Star toggle failed:", err);
      if (onError) onError(err.message || "Failed to update star");
    } finally {
      setStarLoading(false);
    }
  };

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left rounded-xl bg-surface shadow-card p-4 flex items-center gap-3 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      <div className="relative shrink-0" onClick={handleCopyId} title={`Copy ID: ${agent.id}`}>
        <BotIcon state={agentBotState(agent.status)} className="w-9 h-9 cursor-pointer hover:opacity-70 transition-opacity" />
        {copied && (
          <span className="absolute -bottom-5 left-1/2 -translate-x-1/2 text-[10px] text-cyan-400 font-medium whitespace-nowrap">
            Copied!
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-heading truncate flex-1">{agent.name}</h3>
          {agent.last_message_at && (
            <span className="text-xs text-dim shrink-0">{relativeTime(agent.last_message_at)}</span>
          )}
        </div>
        <p className="text-xs text-label truncate mt-0.5">
          {agent.last_message_preview || "No messages yet"}
        </p>
        <div className="flex items-center gap-1.5 mt-1 flex-wrap">
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${statusDot}${isStreaming ? " animate-pulse" : ""}`} />
          <span className={`text-xs lowercase ${statusText}`}>{isStreaming ? "streaming" : agent.status.toLowerCase().replace("_", " ")}</span>
          {agent.model && (
            <span className="text-[10px] text-faint font-medium px-1.5 py-0.5 rounded bg-elevated">
              {modelDisplayName(agent.model)}
            </span>
          )}
          {agent.branch && (
            <span className="inline-flex items-center gap-1 text-xs text-violet-400 bg-violet-500/10 px-1.5 py-0.5 rounded font-mono">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
              </svg>
              {agent.branch}
            </span>
          )}
          {agent.unread_count > 0 && (
            <span className="ml-auto inline-flex items-center justify-center min-w-[18px] h-4.5 px-1 rounded-full bg-cyan-500 text-white text-xs font-bold">
              {agent.unread_count}
            </span>
          )}
        </div>
      </div>
      <div
        role="button"
        tabIndex={-1}
        onClick={handleStarClick}
        className="shrink-0 p-1.5 rounded-lg hover:bg-input transition-colors disabled:opacity-50"
        title={starred ? "Unstar" : "Star"}
      >
        {starred ? (
          <svg className="w-5 h-5 text-amber-400" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
          </svg>
        ) : (
          <svg className="w-5 h-5 text-label hover:text-amber-400 transition-colors" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
          </svg>
        )}
      </div>
    </button>
  );
}

function formatSessionTime(unixMs) {
  if (!unixMs) return "";
  const d = new Date(unixMs);
  const now = new Date();
  const diffMs = now - d;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return d.toLocaleDateString();
}

function SessionRow({ session, project, projectActive, onResume, onError, onToggleStar }) {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [starLoading, setStarLoading] = useState(false);

  const handleCopyId = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(session.session_id).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const handleStarClick = async (e) => {
    e.stopPropagation();
    if (starLoading) return;
    setStarLoading(true);
    try {
      if (session.starred) {
        await unstarSession(project, session.session_id);
      } else {
        await starSession(project, session.session_id);
      }
      if (onToggleStar) onToggleStar(session.session_id, !session.starred);
    } catch (err) {
      console.error("Star toggle failed:", err);
      if (onError) onError(err.message || "Failed to update star");
    } finally {
      setStarLoading(false);
    }
  };

  const handleSync = async (e) => {
    e.stopPropagation();
    if (syncing || resuming || !projectActive) return;
    setSyncing(true);
    try {
      const agent = await createAgent({
        project,
        prompt: session.first_message || "Synced CLI session",
        mode: "AUTO",
        resume_session_id: session.session_id,
        sync_session: true,
      });
      if (onResume) onResume();
      navigate(`/agents/${agent.id}`);
    } catch (err) {
      setSyncing(false);
      if (onError) onError(err.message);
    }
  };

  const handleClick = async () => {
    if (resuming || syncing) return;
    // Block resume for inactive projects
    if (!projectActive && !session.linked_agent_id) {
      if (onError) onError("Please activate this project first");
      return;
    }
    // If already linked to an agent, navigate directly
    if (session.linked_agent_id) {
      navigate(`/agents/${session.linked_agent_id}`);
      return;
    }
    // Otherwise, create a new agent that resumes this session
    setResuming(true);
    try {
      const agent = await createAgent({
        project,
        prompt: session.first_message || "Continue previous conversation",
        mode: "AUTO",
        resume_session_id: session.session_id,
      });
      if (onResume) onResume();
      navigate(`/agents/${agent.id}`);
    } catch (err) {
      setResuming(false);
      if (onError) onError(err.message);
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={resuming}
      className="w-full text-left rounded-xl bg-surface shadow-card p-4 flex items-center gap-3 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover disabled:opacity-60"
    >
      <div
        className="relative shrink-0 cursor-pointer hover:opacity-70 transition-opacity"
        onClick={handleCopyId}
        title={`Copy session ID: ${session.session_id}`}
      >
        {/* Clock icon */}
        <svg className="w-9 h-9 text-label" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="9" />
          <path strokeLinecap="round" d="M12 7v5l3 3" />
        </svg>
        {copied && (
          <span className="absolute -bottom-5 left-1/2 -translate-x-1/2 text-[10px] text-cyan-400 font-medium whitespace-nowrap">
            Copied!
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-heading truncate flex-1">
            {session.first_message || "Untitled session"}
          </h3>
          <span className="text-xs text-dim shrink-0">
            {formatSessionTime(session.last_activity_at)}
          </span>
        </div>
        <div className="flex items-center gap-2 mt-1 flex-wrap">
          <span className="text-xs text-label">
            {session.message_count} message{session.message_count !== 1 ? "s" : ""}
          </span>
          {session.linked_agent_id ? (
            <span className="inline-flex items-center gap-1 text-xs text-cyan-400 bg-cyan-500/10 px-1.5 py-0.5 rounded font-medium">
              Linked to agent
            </span>
          ) : resuming ? (
            <span className="inline-flex items-center gap-1 text-xs text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded font-medium animate-pulse">
              Resuming...
            </span>
          ) : syncing ? (
            <span className="inline-flex items-center gap-1 text-xs text-violet-400 bg-violet-500/10 px-1.5 py-0.5 rounded font-medium animate-pulse">
              Syncing...
            </span>
          ) : !projectActive ? (
            <span className="inline-flex items-center gap-1 text-xs text-dim bg-elevated px-1.5 py-0.5 rounded font-medium">
              Activate to resume
            </span>
          ) : (
            <>
              <span className="inline-flex items-center gap-1 text-xs text-violet-400 bg-violet-500/10 px-1.5 py-0.5 rounded font-medium">
                Click to resume
              </span>
              <button
                type="button"
                onClick={handleSync}
                className="inline-flex items-center gap-1 text-xs text-violet-400 bg-violet-500/10 px-1.5 py-0.5 rounded font-medium hover:bg-violet-500/20 transition-colors"
                title="Import CLI history and live-sync"
              >
                <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Sync
              </button>
            </>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={handleStarClick}
        disabled={starLoading}
        className="shrink-0 p-1.5 rounded-lg hover:bg-input transition-colors disabled:opacity-50"
        title={session.starred ? "Unstar session" : "Star session"}
      >
        {session.starred ? (
          <svg className="w-5 h-5 text-amber-400" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
          </svg>
        ) : (
          <svg className="w-5 h-5 text-label hover:text-amber-400 transition-colors" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
          </svg>
        )}
      </button>
    </button>
  );
}

export default function ProjectDetailPage({ theme, onToggleTheme }) {
  const { name } = useParams();
  const navigate = useNavigate();
  const visible = usePageVisible();

  // Remember last-viewed project so the tab bar can auto-navigate back.
  // Clear returnedFrom since the user is actively viewing a project.
  useEffect(() => {
    if (name) localStorage.setItem("lastViewed:projects", name);
    sessionStorage.removeItem("returnedFrom:projects");
  }, [name]);

  const [project, setProject] = useState(null);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [agentTab, setAgentTab] = useDraft(`ui:project:${name}:tab`, "active");

  // Sessions (lazy-loaded)
  const [sessions, setSessions] = useState(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  // Starred session IDs (eagerly loaded for agent rows)
  const [starredIds, setStarredIds] = useState(new Set());

  // Agent creation (draft persisted per project)
  const [prompt, setPrompt, clearPrompt] = useDraft(`project-agent:${name}:prompt`, "");
  const [model, setModel, clearModel] = useDraft(`project-agent:${name}:model`, MODEL_OPTIONS[0].value);
  const [effort, setEffort, clearEffort] = useDraft(`project-agent:${name}:effort`, "high");
  const [worktree, setWorktree] = useState(null);
  const [syncMode, setSyncMode] = useState(true);
  const [skipPermissions, setSkipPermissions] = useState(true);
  const clearAllDrafts = () => { clearPrompt(); clearModel(); clearEffort(); };
  const [submitting, setSubmitting] = useState(false);
  const [showSchedulePicker, setShowSchedulePicker] = useState(false);
  const attachmentCacheKey = `draft:project-agent:${name}:attachments`;
  const [attachments, setAttachments] = useState(() => {
    try {
      const cached = localStorage.getItem(attachmentCacheKey);
      if (cached) {
        return JSON.parse(cached).map((a) => ({
          ...a,
          uploading: false,
          file: null,
          previewUrl: a.thumbnailUrl || null,
        }));
      }
    } catch { /* ignore */ }
    return [];
  });
  const [dragOver, setDragOver] = useState(false);
  const dragCountRef = useRef(0);
  const fileInputRef = useRef(null);
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);
  const textareaRef = useRef(null);

  const [refreshing, setRefreshing] = useState(false);
  const [fileModal, setFileModal] = useState(null); // "CLAUDE.md" | "PROGRESS.md" | null
  const [showBrowser, setShowBrowser] = useState(false);
  const [fileExists, setFileExists] = useState({ "CLAUDE.md": null, "PROGRESS.md": null });
  const [refreshingClaudeMd, setRefreshingClaudeMd] = useState(false);
  const [claudeMdReady, setClaudeMdReady] = useState(false); // completed result available
  const [diffData, setDiffData] = useState(null); // response from refresh-claudemd

  // Track which agents are actively streaming via WebSocket events + API is_generating
  const { lastEvent } = useWebSocket();
  const [streamingAgents, setStreamingAgents] = useState(new Set());

  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.type === "agent_stream" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      setStreamingAgents((prev) => {
        if (prev.has(aid)) return prev;
        const next = new Set(prev);
        next.add(aid);
        return next;
      });
    }
    // Deterministic end signal from backend
    if (lastEvent.type === "agent_stream_end" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      setStreamingAgents((prev) => {
        if (!prev.has(aid)) return prev;
        const next = new Set(prev);
        next.delete(aid);
        return next;
      });
    }
    if (lastEvent.type === "agent_update" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      const s = lastEvent.data.status;
      if (s !== "EXECUTING" && s !== "SYNCING") {
        setStreamingAgents((prev) => {
          if (!prev.has(aid)) return prev;
          const next = new Set(prev);
          next.delete(aid);
          return next;
        });
      }
    }
  }, [lastEvent]);

  // Seed streaming state from API is_generating on poll
  useEffect(() => {
    if (!agents.length) return;
    setStreamingAgents((prev) => {
      const apiGenerating = new Set(agents.filter((a) => a.is_generating).map((a) => a.id));
      const next = new Set([...prev, ...apiGenerating]);
      for (const aid of prev) {
        if (!apiGenerating.has(aid)) {
          const ag = agents.find((a) => a.id === aid);
          if (!ag || (ag.status !== "EXECUTING" && ag.status !== "SYNCING")) {
            next.delete(aid);
          }
        }
      }
      if (next.size === prev.size && [...next].every((a) => prev.has(a))) return prev;
      return next;
    });
  }, [agents]);

  // Rename
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [showRenameConfirm, setShowRenameConfirm] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const nameInputRef = useRef(null);

  // Activate / Archive / Delete
  const [activating, setActivating] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const showToast = useCallback((message, type = "success") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  // Auto-select name input when rename starts (useEffect runs after DOM commit)
  useEffect(() => {
    if (editingName) nameInputRef.current?.select();
  }, [editingName]);

  // Rename handlers
  const startRename = () => {
    setNameDraft(project?.display_name || project?.name || "");
    setEditingName(true);
  };

  const requestRename = () => {
    const trimmed = nameDraft.trim();
    if (!trimmed || trimmed === (project?.display_name || project?.name)) {
      setEditingName(false);
      return;
    }
    setEditingName(false);
    setShowRenameConfirm(true);
  };

  const deriveSlug = (text) =>
    text.trim().toLowerCase().replace(/[^a-z0-9._-]/g, "-").replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "");

  const confirmRename = async () => {
    const slug = deriveSlug(nameDraft);
    if (!slug) {
      showToast("Invalid project name", "error");
      setShowRenameConfirm(false);
      return;
    }
    setRenaming(true);
    try {
      const displayName = nameDraft.trim() !== slug ? nameDraft.trim() : undefined;
      await renameProjectApi(name, slug, displayName);
      showToast("Project renamed!");
      setShowRenameConfirm(false);

      // Clean up old localStorage keys that embed the project name
      try {
        // Draft keys from useDraft (prefixed with "draft:")
        localStorage.removeItem(`draft:project-agent:${name}:prompt`);
        localStorage.removeItem(`draft:project-agent:${name}:model`);
        localStorage.removeItem(`draft:project-agent:${name}:effort`);
        // Attachment cache (already has "draft:" prefix)
        localStorage.removeItem(`draft:project-agent:${name}:attachments`);
        // Tab state from useDraft
        localStorage.removeItem(`draft:ui:project:${name}:tab`);
        // Update lastViewed to new name
        const lastViewed = localStorage.getItem("lastViewed:projects");
        if (lastViewed === name) {
          localStorage.setItem("lastViewed:projects", slug);
        }
        // Update custom order array
        const orderRaw = localStorage.getItem("projects-custom-order");
        if (orderRaw) {
          const order = JSON.parse(orderRaw);
          const idx = order.indexOf(name);
          if (idx !== -1) {
            order[idx] = slug;
            localStorage.setItem("projects-custom-order", JSON.stringify(order));
          }
        }
      } catch { /* ignore localStorage errors */ }

      navigate(`/projects/${encodeURIComponent(slug)}`, { replace: true });
    } catch (err) {
      showToast("Rename failed: " + err.message, "error");
      setShowRenameConfirm(false);
    } finally {
      setRenaming(false);
    }
  };

  const voice = useVoiceRecorder({
    onTranscript: (text) => setPrompt((prev) => (prev ? prev + " " + text : text)),
    onError: (msg) => showToast(msg, "error"),
  });

  // Auto-expand textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  }, [prompt]);

  // Fetch project + agents
  const loadData = useCallback(async () => {
    try {
      const [folders, agentList] = await Promise.all([
        fetchAllFolders(),
        fetchProjectAgents(name),
      ]);
      const folder = folders.find((f) => f.name === name);
      if (!folder) {
        navigate("/projects", { replace: true });
        return;
      }
      setProject(folder);
      setAgents(agentList);
      setLoadError(null);
    } catch (err) {
      console.error("Failed to load project data:", err);
      setLoadError(err.message || "Failed to load project data");
    } finally {
      setLoading(false);
    }
  }, [name, navigate]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    scanAgents().catch(() => {});
    await loadData();
    // Minimum 400ms spinner display to prevent jarring sub-frame flicker
    setTimeout(() => setRefreshing(false), 400);
  }, [loadData]);

  const pollRef = useRef(null);
  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    setRefreshingClaudeMd(true);
    pollRef.current = setInterval(async () => {
      try {
        const res = await refreshClaudeMdStatus(name);
        if (res.status === "complete") {
          stopPolling();
          setRefreshingClaudeMd(false);
          setClaudeMdReady(true);
        } else if (res.status === "error") {
          stopPolling();
          setRefreshingClaudeMd(false);
          showToast(res.message || "Failed to analyze project — try again", "error");
        }
      } catch (err) {
        console.error("CLAUDE.md refresh poll failed:", err);
        stopPolling();
        setRefreshingClaudeMd(false);
        showToast("Failed to check refresh status", "error");
      }
    }, 2000);
  }, [name, stopPolling, showToast]);

  // Check for pending/complete job on mount
  useEffect(() => {
    if (!name) return;
    refreshClaudeMdStatus(name).then((res) => {
      if (res.status === "running") startPolling();
      else if (res.status === "complete") setClaudeMdReady(true);
    }).catch(() => {});
    return stopPolling;
  }, [name, startPolling, stopPolling]);

  const handleRefreshClaudeMd = useCallback(async () => {
    setRefreshingClaudeMd(true);
    setClaudeMdReady(false);
    try {
      await refreshClaudeMd(name);
      startPolling();
    } catch (err) {
      setRefreshingClaudeMd(false);
      showToast(err.message || "Failed to analyze project — try again", "error");
    }
  }, [name, showToast, startPolling]);

  const handleReviewUpdates = useCallback(async () => {
    try {
      const res = await refreshClaudeMdStatus(name);
      if (res.status === "complete") {
        setDiffData(res.data);
      } else {
        showToast("Update expired — run refresh again", "error");
        setClaudeMdReady(false);
      }
    } catch {
      showToast("Failed to load updates", "error");
    }
  }, [name, showToast]);

  useEffect(() => {
    if (!visible) return;
    loadData();
    const interval = setInterval(loadData, 5000);
    return () => clearInterval(interval);
  }, [loadData, visible]);

  // Check CLAUDE.md / PROGRESS.md existence
  useEffect(() => {
    if (!name) return;
    Promise.all([
      fetchProjectFile(name, "CLAUDE.md").catch(() => ({ exists: false })),
      fetchProjectFile(name, "PROGRESS.md").catch(() => ({ exists: false })),
    ]).then(([c, p]) => {
      setFileExists({ "CLAUDE.md": c.exists, "PROGRESS.md": p.exists });
    });
  }, [name]);

  // Fetch sessions on mount (for starred IDs + counts)
  useEffect(() => {
    fetchProjectSessions(name)
      .then((data) => {
        setStarredIds(new Set(data.filter((s) => s.starred).map((s) => s.session_id)));
        setSessions(data);
      })
      .catch(() => {});
  }, [name]);

  // Lazy-fetch sessions when starred or sessions tab is selected
  useEffect(() => {
    if ((agentTab !== "sessions" && agentTab !== "starred") || sessions !== null) return;
    let cancelled = false;
    setSessionsLoading(true);
    fetchProjectSessions(name)
      .then((data) => { if (!cancelled) setSessions(data); })
      .catch(() => { if (!cancelled) setSessions([]); })
      .finally(() => { if (!cancelled) setSessionsLoading(false); });
    return () => { cancelled = true; };
  }, [agentTab, name, sessions]);

  // Poll sessions while sessions/starred tab is visible
  useEffect(() => {
    if (!visible || (agentTab !== "sessions" && agentTab !== "starred")) return;
    const timer = setInterval(() => {
      fetchProjectSessions(name)
        .then((data) => {
          setSessions(data);
          setStarredIds(new Set(data.filter((s) => s.starred).map((s) => s.session_id)));
        })
        .catch(() => {});
    }, 10000);
    return () => clearInterval(timer);
  }, [visible, agentTab, name]);

  useEffect(() => {
    return () => { if (toastTimer.current) clearTimeout(toastTimer.current); };
  }, []);

  // Filter agents by tab
  const filtered =
    agentTab === "syncing"
      ? agents.filter((a) => a.status === "SYNCING")
      : agentTab === "active"
        ? agents.filter((a) => a.status !== "STOPPED" && a.status !== "SYNCING")
        : agents.filter((a) => a.status === "STOPPED");

  // Tab counts
  const tabCounts = {
    starred: (sessions || []).filter((s) => s.starred).length,
    syncing: agents.filter((a) => a.status === "SYNCING").length,
    active: agents.filter((a) => a.status !== "STOPPED" && a.status !== "SYNCING").length,
    stopped: agents.filter((a) => a.status === "STOPPED").length,
    sessions: sessions != null ? sessions.length : 0,
  };

  // Cleanup blob URLs on unmount (only revoke actual blob: URLs, not server URLs)
  useEffect(() => {
    return () => {
      attachments.forEach((a) => { if (a.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl); });
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync completed attachments to localStorage cache
  useEffect(() => {
    const completed = attachments.filter((a) => !a.uploading && a.uploadedPath);
    if (completed.length > 0) {
      const toCache = completed.map((a) => ({
        id: a.id,
        uploadedPath: a.uploadedPath,
        originalName: a.originalName,
        size: a.size,
        mimeType: a.mimeType || a.file?.type || null,
        thumbnailUrl: a.thumbnailUrl || (
          (a.mimeType || a.file?.type || "").startsWith("image/")
            ? `/api/uploads/${a.uploadedPath.split("/").pop()}`
            : null
        ),
      }));
      try { localStorage.setItem(attachmentCacheKey, JSON.stringify(toCache)); } catch { /* ignore */ }
    } else {
      try { localStorage.removeItem(attachmentCacheKey); } catch { /* ignore */ }
    }
  }, [attachments, attachmentCacheKey]);

  const addFiles = (files) => {
    for (const file of files) {
      if (file.size > 50 * 1024 * 1024) {
        showToast(`${file.name} exceeds 50 MB limit`, "error");
        continue;
      }
      const id = Math.random().toString(36).slice(2, 10);
      const isImage = file.type.startsWith("image/");
      const previewUrl = isImage ? URL.createObjectURL(file) : null;
      setAttachments((prev) => [...prev, {
        id, file, previewUrl, uploading: true, uploadedPath: null,
        originalName: file.name, size: file.size, mimeType: file.type,
      }]);
      uploadFile(file).then((result) => {
        setAttachments((prev) => prev.map((a) =>
          a.id === id ? { ...a, uploading: false, uploadedPath: result.path } : a
        ));
      }).catch((err) => {
        setAttachments((prev) => prev.filter((a) => a.id !== id));
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        showToast(`Upload failed: ${err.message}`, "error");
      });
    }
  };

  const handleFileSelect = (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (files.length > 0) addFiles(files);
  };

  const handleDragEnter = (e) => { e.preventDefault(); e.stopPropagation(); dragCountRef.current++; if (e.dataTransfer?.types?.includes("Files")) setDragOver(true); };
  const handleDragLeave = (e) => { e.preventDefault(); e.stopPropagation(); dragCountRef.current--; if (dragCountRef.current <= 0) { dragCountRef.current = 0; setDragOver(false); } };
  const handleDragOver = (e) => { e.preventDefault(); e.stopPropagation(); };
  const handleDrop = (e) => { e.preventDefault(); e.stopPropagation(); dragCountRef.current = 0; setDragOver(false); const files = Array.from(e.dataTransfer?.files || []); if (files.length > 0) addFiles(files); };
  const handlePaste = (e) => { const items = e.clipboardData?.items; if (!items) return; const files = []; for (const item of items) { if (item.kind === "file") { const f = item.getAsFile(); if (f) files.push(f); } } if (files.length > 0) { e.preventDefault(); addFiles(files); } };

  const removeAttachment = (id) => {
    setAttachments((prev) => {
      const att = prev.find((a) => a.id === id);
      if (att?.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(att.previewUrl);
      return prev.filter((a) => a.id !== id);
    });
  };

  const clearAttachments = () => {
    setAttachments((prev) => {
      prev.forEach((a) => { if (a.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl); });
      return [];
    });
    try { localStorage.removeItem(attachmentCacheKey); } catch { /* ignore */ }
  };

  const buildPromptText = (baseText, atts) => {
    let msg = baseText;
    for (const a of atts) {
      if (a.uploadedPath) msg += `\n[Attached file: ${a.uploadedPath}]`;
    }
    return msg;
  };

  const anyUploading = attachments.some((a) => a.uploading);
  const hasContent = prompt.trim() || attachments.some((a) => a.uploadedPath);

  // Submit new agent (or launch in tmux if syncMode)
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!prompt.trim() && attachments.length === 0) { showToast("Enter a description.", "error"); return; }
    if (anyUploading) { showToast("Uploads still in progress...", "error"); return; }
    const uploaded = attachments.filter((a) => a.uploadedPath);
    const fullPrompt = buildPromptText(prompt.trim(), uploaded);
    setSubmitting(true);
    try {
      if (syncMode) {
        const agent = await launchTmuxAgent({ project: name, prompt: fullPrompt, model, effort, worktree, skip_permissions: skipPermissions });
        clearAllDrafts();
        clearAttachments();
        navigate(`/agents/${agent.id}`);
      } else {
        const agent = await createAgent({ project: name, prompt: fullPrompt, mode: "AUTO", model, effort, worktree, skip_permissions: skipPermissions });
        clearAllDrafts();
        clearAttachments();
        showToast("Agent created!");
        setWorktree(null);
        navigate(`/agents/${agent.id}`);
      }
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setSubmitting(false);
    }
  };

  // Schedule agent creation for later
  const handleSchedule = async (scheduledAt) => {
    if (!prompt.trim() && attachments.length === 0) { showToast("Enter a description.", "error"); return; }
    if (anyUploading) { showToast("Uploads still in progress...", "error"); return; }
    const uploaded = attachments.filter((a) => a.uploadedPath);
    const fullPrompt = buildPromptText(prompt.trim(), uploaded);
    setShowSchedulePicker(false);
    setSubmitting(true);
    try {
      const agent = await createAgent({ project: name, prompt: fullPrompt, mode: "AUTO", model, effort, worktree, skip_permissions: skipPermissions });
      await sendMessage(agent.id, fullPrompt, { queue: true, scheduled_at: scheduledAt });
      clearAllDrafts();
      clearAttachments();
      const when = new Date(scheduledAt);
      showToast(`Scheduled for ${when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
      navigate(`/agents/${agent.id}`);
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setSubmitting(false);
    }
  };

  // Activate project
  const handleActivate = async () => {
    setActivating(true);
    try {
      await createProject({ name });
      showToast("Project activated!");
      await loadData();
    } catch (err) {
      showToast("Activate failed: " + err.message, "error");
    } finally {
      setActivating(false);
    }
  };

  // Archive project
  const handleArchive = async () => {
    setArchiving(true);
    try {
      await archiveProjectApi(name);
      showToast("Project archived");
      await loadData();
    } catch (err) {
      showToast("Archive failed: " + err.message, "error");
    } finally {
      setArchiving(false);
    }
  };

  // Delete project
  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteProjectApi(name);
      navigate("/projects", { replace: true });
    } catch (err) {
      showToast("Delete failed: " + err.message, "error");
    } finally {
      setDeleting(false);
      setShowDelete(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <span className="text-dim text-sm animate-pulse">Loading...</span>
      </div>
    );
  }

  if (!project && loadError) {
    return (
      <div className="px-4 py-10">
        <div className="bg-red-950/40 border border-red-800 rounded-xl p-4">
          <p className="text-red-400 text-sm">Failed to load project: {loadError}</p>
          <button type="button" onClick={loadData} className="mt-2 text-xs text-red-300 underline hover:text-red-200">
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!project) return null;

  return (
    <div className="h-full flex flex-col">
      {/* Toast */}
      {toast && (
        <div className={`fixed left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium pointer-events-none safe-area-toast ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      {/* Fixed Header */}
      <div className="shrink-0 bg-page border-b border-divider px-4 pt-3 pb-3 relative z-10 safe-area-pt">
        <div className="max-w-2xl mx-auto">
          <button
            type="button"
            onClick={() => { localStorage.removeItem("lastViewed:projects"); navigate("/projects", { replace: true }); }}
            className="flex items-center gap-1 min-h-[44px] text-sm text-label hover:text-heading active:text-heading mb-2"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
            Projects
          </button>
          <div className="flex items-center gap-3">
            <BotIcon state={projectBotState(project)} className="w-10 h-10 shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                {editingName ? (
                  <input
                    ref={nameInputRef}
                    value={nameDraft}
                    onChange={(e) => setNameDraft(e.target.value)}
                    onBlur={requestRename}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") requestRename();
                      if (e.key === "Escape") setEditingName(false);
                    }}
                    maxLength={100}
                    className="text-lg font-bold text-heading min-w-0 flex-1 bg-input border border-cyan-500 rounded px-1.5 py-0.5 outline-none"
                  />
                ) : (
                  <h1
                    onDoubleClick={startRename}
                    title="Double-tap to rename"
                    className="text-lg font-bold text-heading truncate select-none"
                  >
                    {project.display_name || project.name}
                  </h1>
                )}
                {project.active ? (
                  <span className="shrink-0 px-2 py-0.5 text-[10px] font-bold uppercase rounded-full bg-emerald-500/15 text-emerald-400 tracking-wide">Active</span>
                ) : (
                  <span className="shrink-0 px-2 py-0.5 text-[10px] font-bold uppercase rounded-full bg-zinc-500/15 text-zinc-400 tracking-wide">Inactive</span>
                )}
                <div className="ml-auto flex items-center gap-1">
                  {["CLAUDE.md", "PROGRESS.md"].map((fn) => {
                    const letter = fn === "CLAUDE.md" ? "C" : "P";
                    const exists = fileExists[fn];
                    const color = exists === false ? "text-zinc-500 hover:text-zinc-400" : "text-cyan-400 hover:text-cyan-300";
                    return (
                      <button
                        key={fn}
                        type="button"
                        onClick={() => setFileModal(fn)}
                        title={fn}
                        className={`relative shrink-0 w-6 h-6 flex items-center justify-center rounded-md hover:bg-white/5 transition-colors ${color}`}
                      >
                        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                          <path strokeLinecap="round" strokeLinejoin="round" d="M14 2v6h6" />
                          <text x="12" y="17" textAnchor="middle" fill="currentColor" stroke="none" fontSize="7" fontWeight="700" fontFamily="system-ui">{letter}</text>
                        </svg>
                        {fn === "CLAUDE.md" && claudeMdReady && (
                          <span className="absolute -top-1 -right-1 flex h-3 w-3 items-center justify-center rounded-full bg-amber-500 text-[7px] font-bold text-white">1</span>
                        )}
                      </button>
                    );
                  })}
                  <button
                    type="button"
                    onClick={() => setShowBrowser(true)}
                    title="Browse files"
                    className="shrink-0 w-6 h-6 flex items-center justify-center rounded-md text-zinc-400 hover:text-zinc-300 hover:bg-white/5 transition-colors"
                  >
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    onClick={handleRefresh}
                    title="Refresh"
                    className="w-6 h-6 flex items-center justify-center rounded-md hover:bg-white/5 transition-colors"
                  >
                    <svg className={`w-4 h-4 text-label ${refreshing ? "animate-spin" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                  </button>
                </div>
              </div>
              <div className="flex items-center gap-3 text-xs">
                {project.process_running && (
                  <span className="inline-flex items-center gap-1 text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    Processes active
                  </span>
                )}
                {project.agent_count > 0 && (
                  <span className="text-label">
                    <span className="font-medium text-heading">{project.agent_count}</span> agent{project.agent_count !== 1 ? "s" : ""}
                  </span>
                )}
                {project.active && (project.agent_active || 0) > 0 && (
                  <span className="text-cyan-400">{project.agent_active} active</span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <div className="pb-20 p-4 max-w-2xl mx-auto w-full space-y-5">

      {/* Inactive project banner */}
      {!project.active && (
        <div className="rounded-xl bg-amber-500/10 border border-amber-500/20 p-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-amber-300">This project is inactive</p>
            <p className="text-xs text-amber-400/70 mt-0.5">Activate to create new agents and run tasks</p>
          </div>
          <button
            type="button"
            disabled={activating}
            onClick={handleActivate}
            className="shrink-0 px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-semibold hover:bg-cyan-500 disabled:opacity-50 transition-colors"
          >
            {activating ? "Activating..." : "Activate"}
          </button>
        </div>
      )}

      {/* New agent form — active projects only */}
      {project.active && (
      <form onSubmit={handleSubmit} className="rounded-xl bg-surface shadow-card p-4">
        <label className="block text-sm font-medium text-label mb-2">New Agent</label>
        <div
          className="glass-bar-nav rounded-[22px] px-3 pt-2 pb-2.5 flex flex-col gap-2 relative mb-5"
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
        >
          {dragOver && (
            <div className="absolute inset-0 z-30 rounded-[22px] bg-cyan-500/15 border-2 border-dashed border-cyan-500 flex items-center justify-center pointer-events-none">
              <span className="text-sm font-medium text-cyan-400">Drop files here</span>
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(e); } }}
            onPaste={handlePaste}
            placeholder="What should this agent do?"
            rows={3}
            className="w-full min-h-[72px] max-h-[180px] rounded-xl bg-transparent px-3 py-2 text-sm text-heading placeholder-hint resize-none focus:outline-none transition-colors"
          />
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5 px-1">
              {attachments.map((att) => (
                <div key={att.id} className="flex items-center gap-1 px-2 py-1 rounded-lg bg-elevated text-xs max-w-[140px]">
                  {att.previewUrl ? (
                    <img src={att.previewUrl} alt="" className="w-8 h-8 rounded object-cover shrink-0" />
                  ) : (
                    <svg className="w-4 h-4 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                    </svg>
                  )}
                  <span className="truncate text-label flex-1 min-w-0">{att.originalName}</span>
                  {att.uploading ? (
                    <svg className="w-3.5 h-3.5 text-cyan-400 animate-spin shrink-0" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : (
                    <button type="button" onClick={() => removeAttachment(att.id)} className="text-dim hover:text-heading shrink-0">
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
          <input ref={fileInputRef} type="file" accept="image/*,video/*,.pdf,.txt,.csv,.json,.md,.py,.js,.ts,.jsx,.tsx,.html,.css,.yaml,.yml,.xml,.log,.zip,.tar,.gz" multiple className="hidden" onChange={handleFileSelect} />
          <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-1.5 items-center px-1">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              title="Attach files"
              className="shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors bg-elevated hover:bg-hover text-label"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
            </button>
            <div className="min-w-0">
              {voice.recording && voice.analyserNode && (
                <WaveformVisualizer analyserNode={voice.analyserNode} remainingSeconds={voice.remainingSeconds} onTap={voice.toggleRecording} className="h-8" />
              )}
            </div>
            <VoiceRecorder
              recording={voice.recording}
              voiceLoading={voice.voiceLoading}
              micError={voice.micError}
              onToggle={voice.toggleRecording}
            />
            <div className="relative">
              <button
                type="button"
                onClick={() => setShowSchedulePicker((v) => !v)}
                disabled={submitting || !hasContent || anyUploading}
                className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                  submitting || !hasContent || anyUploading
                    ? "bg-elevated text-dim cursor-not-allowed"
                    : "bg-amber-500 hover:bg-amber-400 text-white"
                }`}
                title="Send later"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6l4 2m6-2a10 10 0 11-20 0 10 10 0 0120 0z" />
                </svg>
              </button>
              {showSchedulePicker && (
                <SendLaterPicker
                  onSelect={handleSchedule}
                  onClose={() => setShowSchedulePicker(false)}
                />
              )}
            </div>
            <button
              type="submit"
              disabled={submitting || !hasContent || anyUploading}
              className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                submitting || !hasContent || anyUploading
                  ? "bg-elevated text-dim cursor-not-allowed"
                  : "bg-cyan-500 hover:bg-cyan-400 text-white"
              }`}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
              </svg>
            </button>
          </div>
        </div>
        <div className="grid grid-cols-[auto_auto_1fr_auto] gap-y-2 gap-x-2 items-center">
          <div className="flex rounded-lg bg-elevated p-0.5">
            {MODEL_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setModel(opt.value)}
                className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  model === opt.value
                    ? "bg-cyan-600 text-white shadow-sm"
                    : "text-body hover:text-heading"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <div className="flex rounded-lg bg-elevated p-0.5">
            {[["low", "L"], ["medium", "M"], ["high", "H"]].map(([lvl, label]) => (
              <button
                key={lvl}
                type="button"
                onClick={() => setEffort(lvl)}
                className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  effort === lvl
                    ? "bg-cyan-600 text-white shadow-sm"
                    : "text-body hover:text-heading"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div />
          <label className="flex items-center gap-1.5 cursor-pointer">
            <div
              role="switch"
              aria-checked={skipPermissions}
              onClick={() => setSkipPermissions(!skipPermissions)}
              className={`relative w-9 h-[20px] rounded-full transition-colors ${skipPermissions ? "bg-amber-500" : "bg-elevated"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${skipPermissions ? "translate-x-[16px]" : ""}`} />
            </div>
            <span className="text-sm text-label">Auto</span>
          </label>
          <div className="col-span-2 flex items-center gap-1.5">
            <button
              type="button"
              onClick={async () => {
                if (worktree) { setWorktree(null); return; }
                setWorktree("...");
                const name = prompt.trim() ? await generateWorktreeName(prompt) : null;
                setWorktree(name || "auto");
              }}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                worktree
                  ? "bg-purple-500/15 text-purple-400 ring-1 ring-purple-500/30"
                  : "bg-elevated text-dim hover:text-label"
              }`}
              title={worktree ? "Disable worktree" : "Enable worktree"}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
              </svg>
              Worktree
            </button>
            {worktree && (
              <input
                type="text"
                value={worktree === "auto" || worktree === "..." ? "" : worktree}
                onChange={(e) => setWorktree(e.target.value || "auto")}
                className="flex-1 min-w-0 rounded-lg bg-elevated px-2.5 py-1.5 text-xs text-heading placeholder:text-faint outline-none focus:ring-1 focus:ring-purple-500/40"
                placeholder={worktree === "..." ? "generating..." : "worktree name"}
              />
            )}
          </div>
          <div />
          <label className="flex items-center gap-1.5 cursor-pointer">
            <div
              role="switch"
              aria-checked={syncMode}
              onClick={() => setSyncMode(!syncMode)}
              className={`relative w-9 h-[20px] rounded-full transition-colors ${syncMode ? "bg-emerald-500" : "bg-elevated"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${syncMode ? "translate-x-[16px]" : ""}`} />
            </div>
            <span className="text-sm text-label">Tmux</span>
          </label>
        </div>
      </form>
      )}

      {/* Agent tabs */}
      <div className="mt-7">
        <div className="mb-3 -mx-4">
          <FilterTabs tabs={AGENT_TABS} active={agentTab} onChange={setAgentTab} counts={tabCounts} />
        </div>

        {agentTab === "sessions" || agentTab === "starred" ? (
          sessionsLoading ? (
            <div className="text-center py-8 text-faint text-sm animate-pulse">Loading sessions...</div>
          ) : (() => {
            const list = agentTab === "starred"
              ? (sessions || []).filter((s) => s.starred)
              : sessions || [];
            return list.length === 0 ? (
              <div className="text-center py-8 text-faint text-sm">
                {agentTab === "starred" ? "No starred sessions" : "No sessions found"}
              </div>
            ) : (
              <div className="space-y-2">
                {list.map((s) => (
                  <SessionRow
                    key={s.session_id}
                    session={s}
                    project={name}
                    projectActive={project?.active}
                    onResume={() => { setSessions(null); loadData(); }}
                    onError={(msg) => showToast(msg, "error")}
                    onToggleStar={(sid, starred) => {
                      setSessions((prev) =>
                        prev ? prev.map((ss) =>
                          ss.session_id === sid ? { ...ss, starred } : ss
                        ) : prev
                      );
                    }}
                  />
                ))}
              </div>
            );
          })()
        ) : filtered.length === 0 ? (
          <div className="text-center py-8 text-faint text-sm">
            No {agentTab} agents
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((agent) => (
              <AgentRow
                key={agent.id}
                agent={agent}
                project={name}
                starred={starredIds.has(agent.session_id || agent.id)}
                isStreaming={streamingAgents.has(agent.id)}
                onClick={() => navigate(`/agents/${agent.id}`)}
                onError={(msg) => showToast(msg, "error")}
                onToggleStar={(sid, newStarred) => {
                  setStarredIds((prev) => {
                    const next = new Set(prev);
                    newStarred ? next.add(sid) : next.delete(sid);
                    return next;
                  });
                  setSessions((prev) =>
                    prev ? prev.map((ss) =>
                      ss.session_id === sid ? { ...ss, starred: newStarred } : ss
                    ) : prev
                  );
                }}
              />
            ))}
          </div>
        )}
      </div>

      {/* Project settings */}
      <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
        <h2 className="text-sm font-semibold text-label uppercase tracking-wider">Settings</h2>
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm text-body">Refresh CLAUDE.md</p>
            <p className="text-xs text-dim">AI-analyze project and propose updates</p>
          </div>
          {claudeMdReady ? (
            <button
              type="button"
              onClick={handleReviewUpdates}
              className="shrink-0 px-3 py-1.5 rounded-lg bg-amber-500 text-white text-xs font-medium hover:bg-amber-400 transition-colors flex items-center gap-1.5"
            >
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-white" />
              </span>
              Review Updates
            </button>
          ) : (
            <button
              type="button"
              disabled={refreshingClaudeMd}
              onClick={handleRefreshClaudeMd}
              className="shrink-0 px-3 py-1.5 rounded-lg bg-cyan-600 text-white text-xs font-medium hover:bg-cyan-500 disabled:opacity-50 transition-colors flex items-center gap-1.5"
            >
              {refreshingClaudeMd ? (
                <>
                  <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="31.4" strokeLinecap="round" /></svg>
                  Analyzing...
                </>
              ) : (
                <>
                  <svg className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clipRule="evenodd" /></svg>
                  Refresh CLAUDE.md
                </>
              )}
            </button>
          )}
        </div>
        {project.active ? (
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm text-body">Archive Project</p>
              <p className="text-xs text-dim">Deactivate — code and history stay, re-activate anytime</p>
            </div>
            <button
              type="button"
              disabled={archiving}
              onClick={handleArchive}
              className="shrink-0 px-3 py-1.5 rounded-lg bg-amber-600/20 text-amber-400 text-xs font-medium hover:bg-amber-600/30 disabled:opacity-50 transition-colors"
            >
              {archiving ? "Archiving..." : "Archive"}
            </button>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm text-body">Activate Project</p>
              <p className="text-xs text-dim">Register this folder to create agents and run tasks</p>
            </div>
            <button
              type="button"
              disabled={activating}
              onClick={handleActivate}
              className="shrink-0 px-3 py-1.5 rounded-lg bg-cyan-600/20 text-cyan-400 text-xs font-medium hover:bg-cyan-600/30 disabled:opacity-50 transition-colors"
            >
              {activating ? "Activating..." : "Activate"}
            </button>
          </div>
        )}
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm text-body">Delete Project</p>
            <p className="text-xs text-dim">Move files to .trash</p>
          </div>
          <button
            type="button"
            onClick={() => setShowDelete(true)}
            className="shrink-0 px-3 py-1.5 rounded-lg bg-red-600/20 text-red-400 text-xs font-medium hover:bg-red-600/30 transition-colors"
          >
            Delete
          </button>
        </div>
      </div>

      {/* Rename confirmation modal */}
      {showRenameConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-surface rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-card">
            <h3 className="text-lg font-bold text-heading">Rename Project?</h3>
            <p className="text-sm text-label">
              This will rename <span className="font-semibold text-heading">"{name}"</span> to{" "}
              <span className="font-semibold text-heading">"{deriveSlug(nameDraft)}"</span>.
            </p>
            <p className="text-xs text-dim">
              All agents, tasks, session references, and local files will be updated. This cannot be undone.
            </p>
            <div className="flex gap-3">
              <button
                type="button"
                disabled={renaming}
                onClick={confirmRename}
                className="flex-1 min-h-[44px] rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-semibold text-sm transition-colors disabled:opacity-50"
              >
                {renaming ? "Renaming..." : "Rename"}
              </button>
              <button
                type="button"
                onClick={() => setShowRenameConfirm(false)}
                className="flex-1 min-h-[44px] rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation modal */}
      {showDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-surface rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-card">
            <h3 className="text-lg font-bold text-heading">Delete "{project.display_name}"?</h3>
            <p className="text-sm text-label">
              This will remove the project record. Agents will remain in history. This cannot be undone.
            </p>
            <div className="flex gap-3">
              <button
                type="button"
                disabled={deleting}
                onClick={handleDelete}
                className="flex-1 min-h-[44px] rounded-lg bg-red-600 hover:bg-red-500 text-white font-semibold text-sm transition-colors disabled:opacity-50"
              >
                {deleting ? "Deleting..." : "Delete"}
              </button>
              <button
                type="button"
                onClick={() => setShowDelete(false)}
                className="flex-1 min-h-[44px] rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
      </div>

      {fileModal && (
        <ProjectFileModal
          project={name}
          filename={fileModal}
          onClose={() => {
            setFileModal(null);
            // Refresh existence state after modal closes
            Promise.all([
              fetchProjectFile(name, "CLAUDE.md").catch(() => ({ exists: false })),
              fetchProjectFile(name, "PROGRESS.md").catch(() => ({ exists: false })),
            ]).then(([c, p]) => {
              setFileExists({ "CLAUDE.md": c.exists, "PROGRESS.md": p.exists });
            });
          }}
        />
      )}

      {showBrowser && (
        <ProjectBrowserModal
          project={name}
          onClose={() => setShowBrowser(false)}
        />
      )}

      {diffData && (
        <ClaudeMdDiffModal
          data={diffData}
          project={name}
          onClose={() => { setDiffData(null); setClaudeMdReady(false); discardClaudeMd(name).catch(() => {}); }}
          onApplied={(lines, error) => {
            setDiffData(null);
            setClaudeMdReady(false);
            if (error) {
              showToast("Apply failed: " + error, "error");
            } else {
              showToast(`CLAUDE.md updated (${lines} lines)`);
            }
          }}
        />
      )}
    </div>
  );
}
