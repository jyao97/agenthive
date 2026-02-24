import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { createAgent, createProject } from "../lib/api";
import { MODEL_OPTIONS } from "../lib/constants";
import ProjectSelector from "../components/ProjectSelector";
import ModePicker from "../components/ModePicker";
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
        <div className={`fixed top-4 left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      {/* Back header */}
      <div className="shrink-0 bg-page border-b border-divider px-4 pt-4 pb-2 z-10">
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
  const [mode, setMode] = useState("AUTO");
  const [model, setModel] = useState(MODEL_OPTIONS[0].value);
  const [worktree, setWorktree] = useState(null);
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
      const agent = await createAgent({ project, prompt: prompt.trim(), mode, model, worktree });
      showToast("Agent created!");
      setTimeout(() => navigate(`/agents/${agent.id}`), 400);
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

      <div className="rounded-xl bg-surface shadow-card p-4">
        <label className="block text-sm font-medium text-label mb-3">Mode</label>
        <ModePicker value={mode} onChange={setMode} />
        <p className="text-xs text-dim mt-2">Interview: chat only. Plan: review before executing. Auto: execute immediately.</p>
      </div>

      <div className="rounded-xl bg-surface shadow-card p-4">
        <label className="block text-sm font-medium text-label mb-3">Model</label>
        <div className="flex gap-2 flex-wrap">
          {MODEL_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setModel(opt.value)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                model === opt.value
                  ? "bg-cyan-600 text-white"
                  : "bg-input text-label hover:bg-elevated hover:text-body"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <button
        type="submit"
        disabled={submitting || !project || !prompt.trim()}
        className={`w-full min-h-[48px] rounded-lg text-base font-semibold transition-colors ${
          submitting || !project || !prompt.trim()
            ? "bg-elevated text-dim cursor-not-allowed"
            : "bg-cyan-500 hover:bg-cyan-400 text-white shadow-md shadow-cyan-500/20"
        }`}
      >
        {submitting ? "Creating Agent..." : "Create Agent"}
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
            onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
            placeholder="my-project"
            className="w-full min-h-[44px] rounded-lg bg-input border border-edge px-3 py-2 text-heading placeholder-hint font-mono focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors"
          />
          <p className="text-xs text-dim mt-1">Lowercase letters, numbers, hyphens, underscores</p>
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
