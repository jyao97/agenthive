import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  fetchAllFolders,
  fetchProjectAgents,
  createAgent,
  createProject,
  deleteProject as deleteProjectApi,
  archiveProject as archiveProjectApi,
} from "../lib/api";
import BotIcon from "../components/BotIcon";
import VoiceRecorder from "../components/VoiceRecorder";
import ModePicker from "../components/ModePicker";
import WorktreePicker from "../components/WorktreePicker";
import useVoiceRecorder from "../hooks/useVoiceRecorder";
import { relativeTime } from "../lib/formatters";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS } from "../lib/constants";

const AGENT_TABS = [
  { key: "active", label: "Active" },
  { key: "stopped", label: "Stopped" },
];

function projectBotState(proj) {
  if (!proj.active) return "idle";
  if ((proj.agent_active || 0) > 0) return "running";
  if (proj.agent_count > 0) return "completed";
  return "idle";
}

function agentBotState(status) {
  if (status === "EXECUTING" || status === "PLANNING") return "running";
  if (status === "ERROR") return "error";
  if (status === "IDLE" || status === "PLAN_REVIEW") return "completed";
  return "idle";
}

function AgentRow({ agent, onClick }) {
  const statusDot = AGENT_STATUS_COLORS[agent.status] || "bg-gray-500";
  const statusText = AGENT_STATUS_TEXT_COLORS[agent.status] || "text-dim";
  const [copied, setCopied] = useState(false);

  const handleCopyId = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(agent.id).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
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

  // Agent creation
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState("AUTO");
  const [worktree, setWorktree] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);
  const textareaRef = useRef(null);

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

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5000);
    return () => clearInterval(interval);
  }, [loadData]);

  useEffect(() => {
    return () => { if (toastTimer.current) clearTimeout(toastTimer.current); };
  }, []);

  // Filter agents by tab
  const filtered =
    agentTab === "active"
      ? agents.filter((a) => a.status !== "STOPPED")
      : agents.filter((a) => a.status === "STOPPED");

  // Submit new agent
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!prompt.trim()) { showToast("Enter a description.", "error"); return; }
    setSubmitting(true);
    try {
      const agent = await createAgent({ project: name, prompt: prompt.trim(), mode, worktree });
      showToast("Agent created!");
      setPrompt("");
      setMode("AUTO");
      setWorktree(null);
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

  if (!project) return null;

  return (
    <div className="h-full overflow-y-auto overflow-x-hidden">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      {/* Sticky Header */}
      <div className="sticky top-0 z-10 bg-page border-b border-divider px-4 pt-3 pb-3">
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
                <h1 className="text-lg font-bold text-heading truncate">{project.display_name || project.name}</h1>
                {project.active ? (
                  <span className="shrink-0 px-2 py-0.5 text-[10px] font-bold uppercase rounded-full bg-emerald-500/15 text-emerald-400 tracking-wide">Active</span>
                ) : (
                  <span className="shrink-0 px-2 py-0.5 text-[10px] font-bold uppercase rounded-full bg-zinc-500/15 text-zinc-400 tracking-wide">Inactive</span>
                )}
              </div>
              <div className="flex items-center gap-3 text-xs">
                {project.container_running && (
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
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <WorktreePicker value={worktree} onChange={setWorktree} project={name} />
          </div>
          <VoiceRecorder
            recording={voice.recording}
            voiceLoading={voice.voiceLoading}
            analyserNode={voice.analyserNode}
            micError={voice.micError}
            onToggle={voice.toggleRecording}
          />
        </div>
        <ModePicker value={mode} onChange={setMode} />
        <button
          type="submit"
          disabled={submitting || !prompt.trim()}
          className={`w-full min-h-[44px] rounded-lg text-sm font-semibold transition-colors ${
            submitting || !prompt.trim()
              ? "bg-elevated text-dim cursor-not-allowed"
              : "bg-cyan-500 hover:bg-cyan-400 text-white shadow-md shadow-cyan-500/20"
          }`}
        >
          {submitting ? "Creating..." : "Create Agent"}
        </button>
      </form>
      )}

      {/* Agent tabs */}
      <div>
        <div className="flex gap-1 mb-3">
          {AGENT_TABS.map((tab) => {
            const isActive = agentTab === tab.key;
            const count =
              tab.key === "active"
                ? agents.filter((a) => a.status !== "STOPPED").length
                : agents.filter((a) => a.status === "STOPPED").length;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setAgentTab(tab.key)}
                className={`min-h-[36px] px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-cyan-600 text-white"
                    : "bg-surface text-label hover:bg-input"
                }`}
              >
                {tab.label}
                <span className={`ml-1.5 text-xs ${isActive ? "text-cyan-200" : "text-faint"}`}>
                  {count}
                </span>
              </button>
            );
          })}
        </div>

        {filtered.length === 0 ? (
          <div className="text-center py-8 text-faint text-sm">
            No {agentTab} agents
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((agent) => (
              <AgentRow
                key={agent.id}
                agent={agent}
                onClick={() => navigate(`/agents/${agent.id}`)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Project settings */}
      <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
        <h2 className="text-sm font-semibold text-label uppercase tracking-wider">Settings</h2>
        {project.active ? (
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-body">Archive Project</p>
              <p className="text-xs text-dim">Deactivate — code and history stay, re-activate anytime</p>
            </div>
            <button
              type="button"
              disabled={archiving}
              onClick={handleArchive}
              className="px-4 py-2 rounded-lg bg-amber-600/20 text-amber-400 text-sm font-medium hover:bg-amber-600/30 disabled:opacity-50 transition-colors"
            >
              {archiving ? "Archiving..." : "Archive"}
            </button>
          </div>
        ) : (
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-body">Activate Project</p>
              <p className="text-xs text-dim">Register this folder to create agents and run tasks</p>
            </div>
            <button
              type="button"
              disabled={activating}
              onClick={handleActivate}
              className="px-4 py-2 rounded-lg bg-cyan-600/20 text-cyan-400 text-sm font-medium hover:bg-cyan-600/30 disabled:opacity-50 transition-colors"
            >
              {activating ? "Activating..." : "Activate"}
            </button>
          </div>
        )}
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-body">Delete Project</p>
            <p className="text-xs text-dim">Move files to .trash</p>
          </div>
          <button
            type="button"
            onClick={() => setShowDelete(true)}
            className="px-4 py-2 rounded-lg bg-red-600/20 text-red-400 text-sm font-medium hover:bg-red-600/30 transition-colors"
          >
            Delete
          </button>
        </div>
      </div>

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
  );
}
