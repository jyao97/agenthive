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
} from "../lib/api";
import BotIcon from "../components/BotIcon";
import VoiceRecorder from "../components/VoiceRecorder";
import WaveformVisualizer from "../components/WaveformVisualizer";
import WorktreePicker from "../components/WorktreePicker";
import useVoiceRecorder from "../hooks/useVoiceRecorder";
import { relativeTime } from "../lib/formatters";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS, MODEL_OPTIONS, modelDisplayName } from "../lib/constants";
import FilterTabs from "../components/FilterTabs";

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

function AgentRow({ agent, onClick, starred, onToggleStar, project }) {
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
    } catch {
      // silently fail
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
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${statusDot}`} />
          <span className={`text-xs lowercase ${statusText}`}>{agent.status.toLowerCase().replace("_", " ")}</span>
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
    } catch {
      // silently fail
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

  const [project, setProject] = useState(null);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [agentTab, setAgentTab] = useState("active");

  // Sessions (lazy-loaded)
  const [sessions, setSessions] = useState(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  // Starred session IDs (eagerly loaded for agent rows)
  const [starredIds, setStarredIds] = useState(new Set());

  // Agent creation
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState(MODEL_OPTIONS[0].value);
  const [worktree, setWorktree] = useState(null);
  const [syncMode, setSyncMode] = useState(true);
  const [skipPermissions, setSkipPermissions] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);
  const textareaRef = useRef(null);

  const [refreshing, setRefreshing] = useState(false);

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

  // Rename handlers
  const startRename = () => {
    setNameDraft(project?.display_name || project?.name || "");
    setEditingName(true);
    setTimeout(() => nameInputRef.current?.select(), 0);
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
    el.style.height = Math.max(el.scrollHeight, 80) + "px";
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
    } catch {
      // silently retry
    } finally {
      setLoading(false);
    }
  }, [name, navigate]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try { await scanAgents(); } catch {}
    await loadData();
    setTimeout(() => setRefreshing(false), 400);
  }, [loadData]);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5000);
    return () => clearInterval(interval);
  }, [loadData]);

  // Fetch starred IDs on mount (for agent row stars)
  useEffect(() => {
    fetchProjectSessions(name)
      .then((data) => {
        setStarredIds(new Set(data.filter((s) => s.starred).map((s) => s.session_id)));
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

  // Submit new agent (or launch in tmux if syncMode)
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!prompt.trim()) { showToast("Enter a description.", "error"); return; }
    setSubmitting(true);
    try {
      if (syncMode) {
        const agent = await launchTmuxAgent({ project: name, prompt: prompt.trim(), model, skip_permissions: skipPermissions });
        navigate(`/agents/${agent.id}`);
      } else {
        const agent = await createAgent({ project: name, prompt: prompt.trim(), mode: "AUTO", model, worktree, skip_permissions: skipPermissions });
        showToast("Agent created!");
        setPrompt("");
        setModel(MODEL_OPTIONS[0].value);
        setWorktree(null);
        navigate(`/agents/${agent.id}`);
      }
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

  if (!project) return null;

  return (
    <div className="h-full flex flex-col">
      {/* Toast */}
      {toast && (
        <div className={`fixed left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium safe-area-toast ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      {/* Fixed Header */}
      <div className="shrink-0 bg-page border-b border-divider px-4 pt-3 pb-3 z-10 safe-area-pt">
        <div className="max-w-2xl mx-auto">
          <button
            type="button"
            onClick={() => navigate("/projects")}
            className="flex items-center gap-1 text-sm text-label hover:text-heading mb-2"
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
                <button
                  type="button"
                  onClick={handleRefresh}
                  title="Refresh"
                  className="ml-auto w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
                >
                  <svg className={`w-4 h-4 text-label ${refreshing ? "animate-spin" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                </button>
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
      <form onSubmit={handleSubmit} className="rounded-xl bg-surface shadow-card p-4 space-y-3">
        <label className="block text-sm font-medium text-label">New Agent</label>
        <div className="relative">
          <textarea
            ref={textareaRef}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="What should this agent do?"
            rows={3}
            className="w-full min-h-[80px] rounded-lg bg-input border border-edge px-3 py-3 text-heading placeholder-hint resize-none focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors"
          />
        </div>
        {voice.recording && voice.analyserNode && (
          <WaveformVisualizer analyserNode={voice.analyserNode} remainingSeconds={voice.remainingSeconds} className="w-full h-8" />
        )}
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <WorktreePicker value={worktree} onChange={setWorktree} project={name} />
          </div>
          <VoiceRecorder
            recording={voice.recording}
            voiceLoading={voice.voiceLoading}
            micError={voice.micError}
            onToggle={voice.toggleRecording}
          />
        </div>
        <div className="grid grid-cols-3 gap-3">
          {MODEL_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setModel(opt.value)}
              className={`min-h-[44px] rounded-lg text-sm font-medium transition-colors ${
                model === opt.value
                  ? "bg-cyan-600 text-white shadow-md shadow-cyan-600/20"
                  : "bg-elevated text-body hover:bg-hover"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div className="space-y-2">
          <label className="flex items-center gap-2.5 cursor-pointer py-1">
            <div
              role="switch"
              aria-checked={syncMode}
              onClick={() => setSyncMode(!syncMode)}
              className={`relative w-10 h-[22px] rounded-full transition-colors ${syncMode ? "bg-emerald-500" : "bg-elevated"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-[18px] h-[18px] rounded-full bg-white shadow transition-transform ${syncMode ? "translate-x-[18px]" : ""}`} />
            </div>
            <span className="text-sm text-label">Sync agent</span>
            <span className="text-xs text-dim">(tmux on host)</span>
          </label>
          <label className="flex items-center gap-2.5 cursor-pointer py-1">
            <div
              role="switch"
              aria-checked={skipPermissions}
              onClick={() => setSkipPermissions(!skipPermissions)}
              className={`relative w-10 h-[22px] rounded-full transition-colors ${skipPermissions ? "bg-amber-500" : "bg-elevated"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-[18px] h-[18px] rounded-full bg-white shadow transition-transform ${skipPermissions ? "translate-x-[18px]" : ""}`} />
            </div>
            <span className="text-sm text-label">Skip permissions</span>
            <span className="text-xs text-dim">(auto-approve tool use)</span>
          </label>
        </div>
        <button
          type="submit"
          disabled={submitting || !prompt.trim()}
          className={`w-full min-h-[48px] rounded-xl text-sm font-bold tracking-wide uppercase transition-all ${
            submitting || !prompt.trim()
              ? "bg-elevated text-dim cursor-not-allowed"
              : syncMode
                ? "bg-gradient-to-r from-emerald-500 to-cyan-500 hover:from-emerald-400 hover:to-cyan-400 text-white shadow-lg shadow-emerald-500/25"
                : "bg-gradient-to-r from-cyan-500 to-blue-500 hover:from-cyan-400 hover:to-blue-400 text-white shadow-lg shadow-cyan-500/25"
          }`}
        >
          {submitting ? "Creating..." : syncMode ? "Launch Sync Agent" : "Create Agent"}
        </button>
      </form>
      )}

      {/* Agent tabs */}
      <div>
        <div className="mb-3 -mx-4">
          <FilterTabs tabs={AGENT_TABS} active={agentTab} onChange={setAgentTab} />
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
                onClick={() => navigate(`/agents/${agent.id}`)}
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
    </div>
  );
}
