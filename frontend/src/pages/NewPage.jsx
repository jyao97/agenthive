import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { createAgent, launchTmuxAgent, createProject } from "../lib/api";
import { MODEL_OPTIONS } from "../lib/constants";
import ProjectSelector from "../components/ProjectSelector";
import WorktreePicker from "../components/WorktreePicker";
import VoiceRecorder from "../components/VoiceRecorder";
import useVoiceRecorder from "../hooks/useVoiceRecorder";
import PageHeader from "../components/PageHeader";

const CARDS = [
  {
    key: "agent",
    title: "New Agent",
    desc: "Start a persistent Claude Code agent",
    icon: (
      <svg className="w-8 h-8" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
  },
  {
    key: "project",
    title: "New Project",
    desc: "Register a new code project",
    icon: (
      <svg className="w-8 h-8" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
      </svg>
    ),
  },
];

export default function NewPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const [activeCard, setActiveCard] = useState(null);

  // Shared toast
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);
  const showToast = useCallback((message, type = "success") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const goBack = () => setActiveCard(null);

  // ---------- Landing ----------
  if (!activeCard) {
    return (
      <div className="h-full flex flex-col">
        <PageHeader title="Create" theme={theme} onToggleTheme={onToggleTheme} />
        <div className="flex-1 overflow-y-auto overflow-x-hidden">
        <div className="pb-20 p-4 max-w-xl mx-auto w-full">
        <div className="space-y-3">
          {CARDS.map((card) => (
            <button
              key={card.key}
              type="button"
              onClick={() => setActiveCard(card.key)}
              className="w-full text-left rounded-xl bg-surface shadow-card p-5 flex items-center gap-4 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
            >
              <div className="shrink-0 text-cyan-400">{card.icon}</div>
              <div>
                <h3 className="text-base font-semibold text-heading">{card.title}</h3>
                <p className="text-sm text-label mt-0.5">{card.desc}</p>
              </div>
              <svg className="w-5 h-5 ml-auto text-faint shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            </button>
          ))}
        </div>
        </div>
        </div>
      </div>
    );
  }

  // ---------- Forms ----------
  return (
    <div className="h-full flex flex-col">
      {/* Toast */}
      {toast && (
        <div className={`fixed left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium safe-area-toast ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      {/* Back header */}
      <div className="shrink-0 bg-page border-b border-divider px-4 pt-4 pb-2 z-10 safe-area-pt">
        <button type="button" onClick={goBack} className="flex items-center gap-1 text-sm text-label hover:text-heading">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
          Back
        </button>
      </div>

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <div className="pb-20 p-4 max-w-xl mx-auto w-full">
        {activeCard === "agent" && (
          <NewAgentForm showToast={showToast} navigate={navigate} />
        )}
        {activeCard === "project" && (
          <NewProjectForm showToast={showToast} navigate={navigate} />
        )}
      </div>
      </div>
    </div>
  );
}

// ---------- New Agent Form ----------

function NewAgentForm({ showToast, navigate }) {
  const [project, setProject] = useState("");
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState(MODEL_OPTIONS[0].value);
  const [worktree, setWorktree] = useState(null);
  const [syncMode, setSyncMode] = useState(true);
  const [skipPermissions, setSkipPermissions] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const textareaRef = useRef(null);

  const voice = useVoiceRecorder({
    onTranscript: (text) => setPrompt((prev) => (prev ? prev + " " + text : text)),
    onError: (msg) => showToast(msg, "error"),
  });

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.max(el.scrollHeight, 120) + "px";
  }, [prompt]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!project) { showToast("Select a project.", "error"); return; }
    if (!prompt.trim()) { showToast("Enter a description.", "error"); return; }
    setSubmitting(true);
    try {
      if (syncMode) {
        const agent = await launchTmuxAgent({ project, prompt: prompt.trim(), model, skip_permissions: skipPermissions });
        navigate(`/agents/${agent.id}`);
      } else {
        const agent = await createAgent({ project, prompt: prompt.trim(), mode: "AUTO", model, worktree, skip_permissions: skipPermissions });
        showToast("Agent created!");
        setTimeout(() => navigate(`/agents/${agent.id}`), 400);
      }
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <h2 className="text-xl font-bold text-heading">New Agent</h2>

      <div className="rounded-xl bg-surface shadow-card p-4">
        <label className="block text-sm font-medium text-label mb-2">
          Project <span className="text-red-400">*</span>
        </label>
        <ProjectSelector value={project} onChange={setProject} />
      </div>

      <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
        <label className="block text-sm font-medium text-label">
          Initial Prompt <span className="text-red-400">*</span>
        </label>
        <textarea
          ref={textareaRef}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="What should this agent do?"
          rows={4}
          className="w-full min-h-[120px] rounded-lg bg-input border border-edge px-3 py-3 text-heading placeholder-hint resize-none focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors"
        />
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <WorktreePicker value={worktree} onChange={setWorktree} project={project} />
          </div>
          <VoiceRecorder
            recording={voice.recording}
            voiceLoading={voice.voiceLoading}
            analyserNode={voice.analyserNode}
            micError={voice.micError}
            onToggle={voice.toggleRecording}
          />
        </div>
      </div>

      <div className="rounded-xl bg-surface shadow-card p-4 space-y-4">
        <div>
          <label className="block text-sm font-medium text-label mb-3">Model</label>
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
      </div>

      <button
        type="submit"
        disabled={submitting || !project || !prompt.trim()}
        className={`w-full min-h-[52px] rounded-xl text-base font-bold tracking-wide uppercase transition-all ${
          submitting || !project || !prompt.trim()
            ? "bg-elevated text-dim cursor-not-allowed"
            : syncMode
              ? "bg-gradient-to-r from-emerald-500 to-cyan-500 hover:from-emerald-400 hover:to-cyan-400 text-white shadow-lg shadow-emerald-500/25"
              : "bg-gradient-to-r from-cyan-500 to-blue-500 hover:from-cyan-400 hover:to-blue-400 text-white shadow-lg shadow-cyan-500/25"
        }`}
      >
        {submitting ? "Creating..." : syncMode ? "Launch Sync Agent" : "Create Agent"}
      </button>
    </form>
  );
}

// ---------- New Project Form ----------

function NewProjectForm({ showToast, navigate }) {
  const [name, setName] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim()) { showToast("Enter a project name.", "error"); return; }
    setSubmitting(true);
    try {
      const body = { name: name.trim() };
      if (gitUrl.trim()) body.git_url = gitUrl.trim();
      if (description.trim()) body.description = description.trim();
      await createProject(body);
      showToast("Project created!");
      setTimeout(() => navigate("/projects"), 600);
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <h2 className="text-xl font-bold text-heading">New Project</h2>

      <div className="rounded-xl bg-surface shadow-card p-4 space-y-4">
        <div>
          <label className="block text-sm font-medium text-label mb-2">
            Name <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, ""))}
            placeholder="my-project"
            className="w-full min-h-[44px] rounded-lg bg-input border border-edge px-3 py-2 text-heading placeholder-hint font-mono focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors"
          />
          <p className="text-xs text-dim mt-1">Lowercase letters, numbers, hyphens, underscores, dots</p>
        </div>

        <div>
          <label className="block text-sm font-medium text-label mb-2">Git URL</label>
          <input
            type="text"
            value={gitUrl}
            onChange={(e) => setGitUrl(e.target.value)}
            placeholder="https://github.com/user/repo.git"
            className="w-full min-h-[44px] rounded-lg bg-input border border-edge px-3 py-2 text-heading placeholder-hint focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-label mb-2">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What does this project do?"
            rows={2}
            className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading placeholder-hint resize-none focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors"
          />
        </div>
      </div>

      <button
        type="submit"
        disabled={submitting || !name.trim()}
        className={`w-full min-h-[48px] rounded-lg text-base font-semibold transition-colors ${
          submitting || !name.trim()
            ? "bg-elevated text-dim cursor-not-allowed"
            : "bg-cyan-500 hover:bg-cyan-400 text-white shadow-md shadow-cyan-500/20"
        }`}
      >
        {submitting ? "Creating Project..." : "Create Project"}
      </button>
    </form>
  );
}
