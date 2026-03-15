import { useState, useEffect, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { createAgent, launchTmuxAgent, createProject, createTaskV2, sendMessage, generateWorktreeName, uploadFile, dispatchTask } from "../lib/api";
import { MODEL_OPTIONS } from "../lib/constants";
import ProjectSelector from "../components/ProjectSelector";
import VoiceRecorder from "../components/VoiceRecorder";
import WaveformVisualizer from "../components/WaveformVisualizer";
import SendLaterPicker from "../components/SendLaterPicker";
import ImageLightbox from "../components/ImageLightbox";
import ModelSelector from "../components/ModelSelector";
import EffortSelector from "../components/EffortSelector";

import useDraft from "../hooks/useDraft";
import useVoiceRecorder from "../hooks/useVoiceRecorder";
import PageHeader from "../components/PageHeader";
import { useToast } from "../contexts/ToastContext";

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
  {
    key: "task",
    title: "New Task",
    desc: "Create a dispatch-and-review task",
    icon: (
      <svg className="w-8 h-8" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
      </svg>
    ),
  },
];

export default function NewPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [activeCard, setActiveCard] = useState(() => location.state?.card || null);

  const toast = useToast();
  const showToast = (message, type = "success") => type === "error" ? toast.error(message) : toast.success(message);

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
              onClick={() => card.key === "task" ? navigate("/new/task", { state: { backgroundLocation: location } }) : setActiveCard(card.key)}
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
      {/* Back header */}
      <div className="shrink-0 bg-page border-b border-divider px-2 pb-2 z-10 safe-area-pt">
        <button type="button" onClick={goBack} className="flex items-center gap-1 min-h-[44px] min-w-[44px] px-2 text-sm text-label hover:text-heading active:text-heading">
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
        {activeCard === "task" && (
          <NewTaskForm showToast={showToast} navigate={navigate} />
        )}
      </div>
      </div>
    </div>
  );
}

// ---------- New Task Form ----------

function NewTaskForm({ showToast, navigate }) {
  const [project, setProject, clearProject] = useDraft("create-task:project", "");
  const [title, setTitle, clearTitle] = useDraft("create-task:title", "");
  const [description, setDescription, clearDesc] = useDraft("create-task:description", "");
  const [priority, setPriority] = useState(0);
  const [model, setModel] = useState(MODEL_OPTIONS[0].value);
  const [effort, setEffort] = useState("high");
  const [autoDispatch, setAutoDispatch] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const clearAllDrafts = () => { clearProject(); clearTitle(); clearDesc(); };

  const voice = useVoiceRecorder({
    onTranscript: (text) => setDescription((prev) => (prev ? prev + " " + text : text)),
    onError: (msg) => showToast(msg, "error"),
  });

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!title.trim()) { showToast("Enter a title.", "error"); return; }
    setSubmitting(true);
    try {
      const body = {
        title: title.trim(),
        description: description.trim() || undefined,
        project_name: project || undefined,
        priority,
        model: model || undefined,
        effort: effort || undefined,
      };
      const task = await createTaskV2(body);
      if (autoDispatch && project && task.id) {
        try {
          await dispatchTask(task.id);
        } catch (err) {
          showToast("Created but dispatch failed: " + err.message, "error");
        }
      }
      clearAllDrafts();
      navigate(`/tasks/${task.id}`);
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <h2 className="text-xl font-bold text-heading">New Task</h2>

      <div className="rounded-xl bg-surface shadow-card p-4 space-y-4">
        <div>
          <label className="block text-sm font-medium text-label mb-2">
            Title <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="What should be done?"
            className="w-full min-h-[44px] rounded-lg bg-input border border-edge px-3 py-2 text-heading placeholder-hint focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-label mb-2">Project</label>
          <ProjectSelector value={project} onChange={setProject} />
        </div>

        <div>
          <label className="block text-sm font-medium text-label mb-2">Description</label>
          <div className="flex items-end gap-2">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Detailed requirements (optional)"
              rows={4}
              className="flex-1 rounded-lg bg-input border border-edge px-3 py-2 text-heading placeholder-hint resize-none focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors"
            />
            <VoiceRecorder
              recording={voice.recording}
              voiceLoading={voice.voiceLoading}
              micError={voice.micError}
              onToggle={voice.toggleRecording}
            />
          </div>
        </div>

        <div className="grid grid-cols-[auto_auto_1fr] gap-2 items-center">
          <ModelSelector value={model} onChange={setModel} />
          <EffortSelector value={effort} onChange={setEffort} />
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setPriority(priority === 1 ? 0 : 1)}
              className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                priority === 1
                  ? "bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30"
                  : "bg-elevated text-dim hover:text-label"
              }`}
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
              </svg>
              High Priority
            </button>
          </div>
        </div>

        {project && description.trim() && (
          <label className="flex items-center gap-2 cursor-pointer">
            <div
              role="switch"
              aria-checked={autoDispatch}
              onClick={() => setAutoDispatch(!autoDispatch)}
              className={`relative w-9 h-[20px] rounded-full transition-colors ${autoDispatch ? "bg-cyan-500" : "bg-elevated"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${autoDispatch ? "translate-x-[16px]" : ""}`} />
            </div>
            <span className="text-sm text-label">Auto-dispatch after creation</span>
          </label>
        )}
      </div>

      <button
        type="submit"
        disabled={submitting || !title.trim()}
        className={`w-full min-h-[48px] rounded-lg text-base font-semibold transition-colors ${
          submitting || !title.trim()
            ? "bg-elevated text-dim cursor-not-allowed"
            : "bg-cyan-500 hover:bg-cyan-400 text-white shadow-md shadow-cyan-500/20"
        }`}
      >
        {submitting ? "Creating Task..." : "Create Task"}
      </button>
    </form>
  );
}

// ---------- New Agent Form ----------

function NewAgentForm({ showToast, navigate }) {
  const [project, setProject, clearProject] = useDraft("create-agent:project", "");
  const [prompt, setPrompt, clearPrompt] = useDraft("create-agent:prompt", "");
  const [model, setModel, clearModel] = useDraft("create-agent:model", MODEL_OPTIONS[0].value);
  const [effort, setEffort, clearEffort] = useDraft("create-agent:effort", "high");
  const [worktree, setWorktree] = useState(null);
  const [syncMode, setSyncMode] = useState(true);
  const [skipPermissions, setSkipPermissions] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const attachmentCacheKey = "draft:create-agent:attachments";
  const [attPreviewIndex, setAttPreviewIndex] = useState(null);
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
    } catch { // Expected: localStorage data may be corrupt or invalid JSON
    }
    return [];
  });
  const [dragOver, setDragOver] = useState(false);
  const dragCountRef = useRef(0);
  const clearAllDrafts = () => { clearProject(); clearPrompt(); clearModel(); clearEffort(); };
  const [showSchedulePicker, setShowSchedulePicker] = useState(false);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);

  const voice = useVoiceRecorder({
    onTranscript: (text) => setPrompt((prev) => (prev ? prev + " " + text : text)),
    onError: (msg) => showToast(msg, "error"),
  });

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  }, [prompt]);

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
      try { localStorage.setItem(attachmentCacheKey, JSON.stringify(toCache)); } catch { /* Expected: localStorage quota may be exceeded */ }
    } else {
      try { localStorage.removeItem(attachmentCacheKey); } catch { /* Expected: localStorage may be unavailable */ }
    }
  }, [attachments]);

  const buildPromptText = (baseText, atts) => {
    let msg = baseText;
    for (const a of atts) {
      if (a.uploadedPath) msg += `\n[Attached file: ${a.uploadedPath}]`;
    }
    return msg;
  };

  const clearAttachments = () => {
    setAttachments((prev) => {
      prev.forEach((a) => { if (a.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl); });
      return [];
    });
    try { localStorage.removeItem(attachmentCacheKey); } catch { /* Expected: localStorage may be unavailable */ }
  };

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

  const handleDragEnter = (e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCountRef.current++;
    if (e.dataTransfer?.types?.includes("Files")) setDragOver(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCountRef.current--;
    if (dragCountRef.current <= 0) { dragCountRef.current = 0; setDragOver(false); }
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCountRef.current = 0;
    setDragOver(false);
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length > 0) addFiles(files);
  };

  const handlePaste = (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      addFiles(files);
    }
  };

  const removeAttachment = (id) => {
    setAttachments((prev) => {
      const att = prev.find((a) => a.id === id);
      if (att?.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(att.previewUrl);
      return prev.filter((a) => a.id !== id);
    });
  };

  const anyUploading = attachments.some((a) => a.uploading);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!project) { showToast("Select a project.", "error"); return; }
    if (!prompt.trim() && attachments.length === 0) { showToast("Enter a description.", "error"); return; }
    if (anyUploading) { showToast("Uploads still in progress...", "error"); return; }
    const uploaded = attachments.filter((a) => a.uploadedPath);
    const fullPrompt = buildPromptText(prompt.trim(), uploaded);
    setSubmitting(true);
    try {
      if (syncMode) {
        const agent = await launchTmuxAgent({ project, prompt: fullPrompt, model, effort, worktree, skip_permissions: skipPermissions });
        clearAllDrafts();
        clearAttachments();
        navigate(`/agents/${agent.id}`);
      } else {
        const agent = await createAgent({ project, prompt: fullPrompt, mode: "AUTO", model, effort, worktree, skip_permissions: skipPermissions });
        clearAllDrafts();
        clearAttachments();
        navigate(`/agents/${agent.id}`);
      }
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleSchedule = async (scheduledAt) => {
    if (!project) { showToast("Select a project.", "error"); return; }
    if (!prompt.trim() && attachments.length === 0) { showToast("Enter a description.", "error"); return; }
    if (anyUploading) { showToast("Uploads still in progress...", "error"); return; }
    const uploaded = attachments.filter((a) => a.uploadedPath);
    const fullPrompt = buildPromptText(prompt.trim(), uploaded);
    setShowSchedulePicker(false);
    setSubmitting(true);
    try {
      const agent = await createAgent({ project, prompt: fullPrompt, mode: "AUTO", model, effort, worktree, skip_permissions: skipPermissions });
      await sendMessage(agent.id, fullPrompt, { queue: true, scheduled_at: scheduledAt });
      clearAllDrafts();
      clearAttachments();
      const when = new Date(scheduledAt);
      navigate(`/agents/${agent.id}`);
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setSubmitting(false);
    }
  };

  const hasContent = prompt.trim() || attachments.some((a) => a.uploadedPath);

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <h2 className="text-xl font-bold text-heading">New Agent</h2>

      <div className="rounded-xl bg-surface shadow-card p-4">
        <label className="block text-sm font-medium text-label mb-2">
          Project <span className="text-red-400">*</span>
        </label>
        <ProjectSelector value={project} onChange={setProject} />
      </div>

      <div className="rounded-xl bg-surface shadow-card p-4">
        <label className="block text-sm font-medium text-label mb-2">
          Initial Prompt <span className="text-red-400">*</span>
        </label>
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
              {attachments.map((att, i) => (
                <div key={att.id} className="flex items-center gap-1 px-2 py-1 rounded-lg bg-elevated text-xs max-w-[140px] cursor-pointer"
                  onClick={() => { if (!att.uploading) setAttPreviewIndex(i); }}>
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
                disabled={submitting || !project || !hasContent || anyUploading}
                className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                  submitting || !project || !hasContent || anyUploading
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
              disabled={submitting || !project || !hasContent || anyUploading}
              className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                submitting || !project || !hasContent || anyUploading
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
        <div className="grid grid-cols-[auto_auto_1fr_auto] gap-y-2 gap-x-1.5 items-center">
          <ModelSelector value={model} onChange={setModel} />
          <EffortSelector value={effort} onChange={setEffort} />
          <div />
          <label className="flex items-center gap-1.5 cursor-pointer whitespace-nowrap">
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
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors ${
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
          <label className="flex items-center gap-1.5 cursor-pointer whitespace-nowrap">
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
      </div>

      {attPreviewIndex != null && attachments.length > 0 && (
        <ImageLightbox
          media={attachments.filter(a => !a.uploading).map(a => ({
            src: a.previewUrl || `/api/uploads/${a.uploadedPath?.split("/").pop()}`,
            filename: a.originalName,
            type: "image",
          }))}
          initialIndex={Math.min(attPreviewIndex, attachments.filter(a => !a.uploading).length - 1)}
          onClose={() => setAttPreviewIndex(null)}
        />
      )}
    </form>
  );
}

// ---------- New Project Form ----------

function NewProjectForm({ showToast, navigate }) {
  const [name, setName, clearName] = useDraft("create-project:name", "");
  const [gitUrl, setGitUrl, clearGitUrl] = useDraft("create-project:gitUrl", "");
  const [description, setDescription, clearDesc] = useDraft("create-project:description", "");
  const [submitting, setSubmitting] = useState(false);
  const clearAllDrafts = () => { clearName(); clearGitUrl(); clearDesc(); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim()) { showToast("Enter a project name.", "error"); return; }
    setSubmitting(true);
    try {
      const body = { name: name.trim() };
      if (gitUrl.trim()) body.git_url = gitUrl.trim();
      if (description.trim()) body.description = description.trim();
      await createProject(body);
      clearAllDrafts();
      navigate("/projects");
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
