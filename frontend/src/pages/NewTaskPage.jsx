import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { createTaskV2, launchTmuxAgent, uploadFile, generateWorktreeName } from "../lib/api";
import { MODEL_OPTIONS } from "../lib/constants";
import ProjectSelector from "../components/ProjectSelector";
import VoiceRecorder from "../components/VoiceRecorder";
import WaveformVisualizer from "../components/WaveformVisualizer";
import SendLaterPicker from "../components/SendLaterPicker";
import useDraft from "../hooks/useDraft";
import useVoiceRecorder from "../hooks/useVoiceRecorder";

function deriveTitle(description) {
  if (!description) return "";
  const text = description.trim();
  if (text.length <= 60) return text;
  const cut = text.slice(0, 60);
  const spaceIdx = cut.lastIndexOf(" ");
  return (spaceIdx > 20 ? cut.slice(0, spaceIdx) : cut) + "...";
}

export default function NewTaskPage() {
  const navigate = useNavigate();
  const [autoVoice, setAutoVoice] = useState(() => {
    try { return localStorage.getItem("pref:autoVoice") !== "false"; } catch { return true; }
  });
  const [title, setTitle, clearTitle] = useDraft("new-task:title", "");
  const [description, setDescription, clearDesc] = useDraft("new-task:description", "");
  const [project, setProject, clearProject] = useDraft("new-task:project", "");
  const [model, setModel, clearModel] = useDraft("new-task:model", MODEL_OPTIONS[0].value);
  const [effort, setEffort, clearEffort] = useDraft("new-task:effort", "high");
  const [priority, setPriority] = useState(0);
  const [autoDispatch, setAutoDispatch] = useState(() => {
    try { return localStorage.getItem("pref:autoDispatch") !== "false"; } catch { return true; }
  });
  const [skipPermissions, setSkipPermissions] = useState(() => {
    try { return localStorage.getItem("pref:skipPermissions") !== "false"; } catch { return true; }
  });
  const [worktree, setWorktree] = useState(() => {
    try { const v = localStorage.getItem("pref:worktree"); return v !== null ? (v === "" ? null : v) : "auto"; } catch { return "auto"; }
  });
  const [syncMode, setSyncMode] = useState(() => {
    try { return localStorage.getItem("pref:syncMode") === "true"; } catch { return false; }
  });
  const [submitting, setSubmitting] = useState(false);
  const [showSchedulePicker, setShowSchedulePicker] = useState(false);
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const voiceAutoStarted = useRef(false);

  // Sheet animation state
  const [mounted, setMounted] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [sheetY, setSheetY] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const touchStartRef = useRef(null);

  useEffect(() => {
    requestAnimationFrame(() => requestAnimationFrame(() => setMounted(true)));
  }, []);

  // Attachments
  const attachmentCacheKey = "draft:new-task:attachments";
  const [attachments, setAttachments] = useState(() => {
    try {
      const cached = localStorage.getItem(attachmentCacheKey);
      if (cached) {
        return JSON.parse(cached).map((a) => ({
          ...a, uploading: false, file: null, previewUrl: a.thumbnailUrl || null,
        }));
      }
    } catch { /* corrupt cache */ }
    return [];
  });
  const [dragOver, setDragOver] = useState(false);
  const dragCountRef = useRef(0);

  const clearAllDrafts = () => { clearTitle(); clearDesc(); clearProject(); clearModel(); clearEffort(); };

  const showToast = useCallback((message, type = "success") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const voice = useVoiceRecorder({
    onTranscript: (text) => setDescription((prev) => (prev ? prev + " " + text : text)),
    onError: (msg) => showToast(msg, "error"),
  });

  // Auto-start voice on mount if preference is on and no draft exists
  useEffect(() => {
    if (!autoVoice) return;
    if (description || title) return;
    voiceAutoStarted.current = true;
    const timer = setTimeout(() => { voice.toggleRecording(); }, 400);
    return () => {
      clearTimeout(timer);
      voiceAutoStarted.current = false; // reset for StrictMode double-mount
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [description]);

  // Cleanup blob URLs on unmount
  useEffect(() => {
    return () => { attachments.forEach((a) => { if (a.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl); }); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync completed attachments to localStorage
  useEffect(() => {
    const completed = attachments.filter((a) => !a.uploading && a.uploadedPath);
    if (completed.length > 0) {
      const toCache = completed.map((a) => ({
        id: a.id, uploadedPath: a.uploadedPath, originalName: a.originalName,
        size: a.size, mimeType: a.mimeType || null,
        thumbnailUrl: (a.mimeType || "").startsWith("image/") ? `/api/uploads/${a.uploadedPath.split("/").pop()}` : null,
      }));
      try { localStorage.setItem(attachmentCacheKey, JSON.stringify(toCache)); } catch { /* quota */ }
    } else {
      try { localStorage.removeItem(attachmentCacheKey); } catch { /* unavailable */ }
    }
  }, [attachments]);

  const addFiles = (files) => {
    for (const file of files) {
      if (file.size > 50 * 1024 * 1024) { showToast(`${file.name} exceeds 50 MB limit`, "error"); continue; }
      const id = Math.random().toString(36).slice(2, 10);
      const isImage = file.type.startsWith("image/");
      const previewUrl = isImage ? URL.createObjectURL(file) : null;
      setAttachments((prev) => [...prev, { id, file, previewUrl, uploading: true, uploadedPath: null, originalName: file.name, size: file.size, mimeType: file.type }]);
      uploadFile(file).then((result) => {
        setAttachments((prev) => prev.map((a) => a.id === id ? { ...a, uploading: false, uploadedPath: result.path } : a));
      }).catch((err) => {
        setAttachments((prev) => prev.filter((a) => a.id !== id));
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        showToast(`Upload failed: ${err.message}`, "error");
      });
    }
  };

  const removeAttachment = (id) => {
    setAttachments((prev) => {
      const att = prev.find((a) => a.id === id);
      if (att?.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(att.previewUrl);
      return prev.filter((a) => a.id !== id);
    });
  };

  const clearAttachments = () => {
    setAttachments((prev) => { prev.forEach((a) => { if (a.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl); }); return []; });
    try { localStorage.removeItem(attachmentCacheKey); } catch { /* unavailable */ }
  };

  const handleDragEnter = (e) => { e.preventDefault(); e.stopPropagation(); dragCountRef.current++; if (e.dataTransfer?.types?.includes("Files")) setDragOver(true); };
  const handleDragLeave = (e) => { e.preventDefault(); e.stopPropagation(); dragCountRef.current--; if (dragCountRef.current <= 0) { dragCountRef.current = 0; setDragOver(false); } };
  const handleDragOver = (e) => { e.preventDefault(); e.stopPropagation(); };
  const handleDrop = (e) => { e.preventDefault(); e.stopPropagation(); dragCountRef.current = 0; setDragOver(false); const files = Array.from(e.dataTransfer?.files || []); if (files.length > 0) addFiles(files); };
  const handlePaste = (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) { if (item.kind === "file") { const f = item.getAsFile(); if (f) files.push(f); } }
    if (files.length > 0) { e.preventDefault(); addFiles(files); }
  };
  const handleFileSelect = (e) => { const files = Array.from(e.target.files || []); e.target.value = ""; if (files.length > 0) addFiles(files); };

  const anyUploading = attachments.some((a) => a.uploading);

  const buildDescriptionText = (baseText, atts) => {
    let msg = baseText;
    for (const a of atts) { if (a.uploadedPath) msg += `\n[Attached file: ${a.uploadedPath}]`; }
    return msg;
  };

  // ---- Dismiss (swipe down / backdrop tap) → save to inbox ----
  const dismissClosingRef = useRef(false);
  const dismiss = async () => {
    if (dismissClosingRef.current) return;
    dismissClosingRef.current = true;
    const hasContent = description.trim() || title.trim() || attachments.some((a) => a.uploadedPath);
    if (hasContent) {
      try {
        const uploaded = attachments.filter((a) => a.uploadedPath);
        const fullDescription = buildDescriptionText(description.trim(), uploaded);
        const finalTitle = title.trim() || deriveTitle(description);
        await createTaskV2({
          title: finalTitle,
          description: fullDescription || undefined,
          project_name: project || undefined,
          priority,
          model: model || undefined,
          effort: effort || undefined,
          skip_permissions: skipPermissions,
          sync_mode: syncMode,
          auto_dispatch: false, // inbox only
        });
        clearAllDrafts();
        clearAttachments();
        showToast("Saved to inbox");
      } catch (err) {
        showToast("Failed to save: " + err.message, "error");
        dismissClosingRef.current = false;
        return;
      }
    }
    setIsClosing(true);
    setTimeout(() => navigate(-1), 250);
  };

  // ---- Submit (send button / enter) → create & dispatch ----
  const doCreateAndDispatch = async (scheduledAt) => {
    if (!description.trim() && !title.trim() && attachments.length === 0) { showToast("Enter a description.", "error"); return; }
    if (anyUploading) { showToast("Uploads still in progress...", "error"); return; }
    const uploaded = attachments.filter((a) => a.uploadedPath);
    const fullDescription = buildDescriptionText(description.trim(), uploaded);
    setSubmitting(true);
    try {
      const finalTitle = title.trim() || deriveTitle(description);
      const wtName = worktree === "auto" && description.trim()
        ? await generateWorktreeName(description).catch(() => null) || "auto"
        : worktree;
      const isTmuxDispatch = syncMode && autoDispatch && project;
      const body = {
        title: finalTitle,
        description: fullDescription || undefined,
        project_name: project || undefined,
        priority,
        model: model || undefined,
        effort: effort || undefined,
        skip_permissions: skipPermissions,
        sync_mode: syncMode,
        scheduled_at: scheduledAt || undefined,
        auto_dispatch: !isTmuxDispatch && autoDispatch && !!project && !scheduledAt,
      };
      if (scheduledAt && autoDispatch && project) {
        body.auto_dispatch = true;
      }
      const task = await createTaskV2(body);

      if (isTmuxDispatch && !scheduledAt) {
        const agent = await launchTmuxAgent({
          project, prompt: fullDescription, model, effort,
          worktree: wtName || undefined,
          skip_permissions: skipPermissions,
          task_id: task.id,
        });
        clearAllDrafts();
        clearAttachments();
        setIsClosing(true);
        setTimeout(() => navigate(`/agents/${agent.id}`, { replace: true }), 250);
      } else if (autoDispatch && project) {
        clearAllDrafts();
        clearAttachments();
        setIsClosing(true);
        setTimeout(() => navigate("/tasks", { replace: true }), 250);
      } else {
        clearAllDrafts();
        clearAttachments();
        setIsClosing(true);
        setTimeout(() => navigate(`/tasks/${task.id}`, { replace: true }), 250);
      }
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = async (e) => {
    if (e) e.preventDefault();
    await doCreateAndDispatch(null);
  };

  const handleSchedule = async (scheduledAt) => {
    setShowSchedulePicker(false);
    await doCreateAndDispatch(scheduledAt);
  };

  const hasContent = description.trim() || title.trim() || attachments.some((a) => a.uploadedPath);
  const canSubmit = hasContent && !submitting && !anyUploading;

  // ---- Swipe-down gesture on drag handle ----
  const handleTouchStart = (e) => {
    touchStartRef.current = { y: e.touches[0].clientY };
    setIsDragging(true);
  };
  const handleTouchMove = (e) => {
    if (!touchStartRef.current) return;
    const dy = e.touches[0].clientY - touchStartRef.current.y;
    if (dy > 0) setSheetY(dy);
  };
  const handleTouchEnd = () => {
    if (!touchStartRef.current) return;
    setIsDragging(false);
    if (sheetY > 120) {
      dismiss();
    } else {
      setSheetY(0);
    }
    touchStartRef.current = null;
  };

  const sheetTranslate = isClosing ? "translateY(100%)" : `translateY(${sheetY}px)`;
  const sheetTransition = isDragging ? "none" : "transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)";

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end">
      {/* Toast */}
      {toast && (
        <div className={`fixed left-1/2 -translate-x-1/2 top-12 z-[60] px-5 py-3 rounded-lg shadow-lg text-sm font-medium ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      {/* Backdrop */}
      <div
        className="absolute inset-0 transition-opacity duration-300"
        style={{ backgroundColor: "rgba(0,0,0,0.4)", opacity: mounted && !isClosing ? 1 : 0 }}
        onClick={() => dismiss()}
      />

      {/* Bottom sheet card */}
      <div
        className="relative z-10 bg-page rounded-t-[20px] shadow-2xl flex flex-col"
        style={{
          maxHeight: "92vh",
          transform: mounted ? sheetTranslate : "translateY(100%)",
          transition: sheetTransition,
        }}
      >
        {/* Drag handle */}
        <div
          className="flex justify-center pt-3 pb-1 cursor-grab active:cursor-grabbing shrink-0"
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
        >
          <div className="w-10 h-1 rounded-full bg-dim/40" />
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden px-4 pb-6">
          <h2 className="text-lg font-bold text-heading mb-3">New Task</h2>

          <div className="space-y-3">
            {/* Title (optional) */}
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Title (auto-generated if blank)"
              className="w-full min-h-[44px] rounded-lg bg-input border border-edge px-3 py-2 text-heading placeholder-hint focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors"
            />

            {/* Project */}
            <ProjectSelector value={project} onChange={setProject} />

            {/* Input card — matches project detail page layout */}
            <form onSubmit={handleSubmit} className="rounded-xl bg-surface shadow-card p-4">
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
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(e); } }}
                  onPaste={handlePaste}
                  placeholder="Describe what needs to be done..."
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
                <div className="grid grid-cols-[auto_1fr_auto_auto_auto_auto] gap-1.5 items-center px-1">
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
                      disabled={!canSubmit}
                      className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                        !canSubmit
                          ? "bg-elevated text-dim cursor-not-allowed"
                          : "bg-amber-500 hover:bg-amber-400 text-white"
                      }`}
                      title="Schedule dispatch"
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
                    disabled={!canSubmit}
                    className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                      !canSubmit
                        ? "bg-elevated text-dim cursor-not-allowed"
                        : "bg-cyan-500 hover:bg-cyan-400 text-white"
                    }`}
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    onClick={() => dismiss()}
                    disabled={!hasContent || submitting}
                    className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                      !hasContent || submitting
                        ? "bg-elevated text-dim cursor-not-allowed"
                        : "bg-indigo-500 hover:bg-indigo-400 text-white"
                    }`}
                    title="Save to inbox"
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-2.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
                    </svg>
                  </button>
                </div>
              </div>
              {/* Controls grid — matches project page */}
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
                    onClick={() => { const next = !skipPermissions; setSkipPermissions(next); try { localStorage.setItem("pref:skipPermissions", String(next)); } catch {} }}
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
                      if (worktree) { setWorktree(null); try { localStorage.setItem("pref:worktree", ""); } catch {} return; }
                      setWorktree("...");
                      const name = description.trim() ? await generateWorktreeName(description).catch(() => null) : null;
                      const val = name || "auto";
                      setWorktree(val);
                      try { localStorage.setItem("pref:worktree", val); } catch {}
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
                    onClick={() => { const next = !syncMode; setSyncMode(next); try { localStorage.setItem("pref:syncMode", String(next)); } catch {} }}
                    className={`relative w-9 h-[20px] rounded-full transition-colors ${syncMode ? "bg-emerald-500" : "bg-elevated"}`}
                  >
                    <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${syncMode ? "translate-x-[16px]" : ""}`} />
                  </div>
                  <span className="text-sm text-label">Tmux</span>
                </label>
                <label className="col-span-2 flex items-center gap-2 cursor-pointer mt-1">
                  <div
                    role="switch"
                    aria-checked={autoDispatch}
                    onClick={() => { const next = !autoDispatch; setAutoDispatch(next); try { localStorage.setItem("pref:autoDispatch", String(next)); } catch {} }}
                    className={`relative w-9 h-[20px] rounded-full transition-colors ${autoDispatch ? "bg-cyan-500" : "bg-elevated"}`}
                  >
                    <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${autoDispatch ? "translate-x-[16px]" : ""}`} />
                  </div>
                  <span className="text-sm text-label">Auto-dispatch</span>
                </label>
                <label className="col-span-2 flex items-center gap-2 cursor-pointer mt-1 justify-end">
                  <div
                    role="switch"
                    aria-checked={autoVoice}
                    onClick={() => {
                      const next = !autoVoice;
                      setAutoVoice(next);
                      try { localStorage.setItem("pref:autoVoice", String(next)); } catch {}
                    }}
                    className={`relative w-9 h-[20px] rounded-full transition-colors ${autoVoice ? "bg-cyan-500" : "bg-elevated"}`}
                  >
                    <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${autoVoice ? "translate-x-[16px]" : ""}`} />
                  </div>
                  <span className="text-sm text-label">Voice</span>
                </label>
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
