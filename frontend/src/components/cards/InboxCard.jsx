import { memo, useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from "react";
import { modelDisplayName, MODEL_OPTIONS } from "../../lib/constants";
import { relativeTime } from "../../lib/formatters";
import { updateTaskV2, uploadFile, cancelTask, dispatchTask, regenerateTaskSummary } from "../../lib/api";
import useVoiceRecorder from "../../hooks/useVoiceRecorder";
import useProjects from "../../hooks/useProjects";
import CardShell, { cardPadding } from "./CardShell";
import TagPicker from "./TagPicker";
import ImageLightbox from "../ImageLightbox";

const MODEL_PICKER = MODEL_OPTIONS.map(m => ({ value: m.value, label: m.label }));
const EFFORT_PICKER = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "max", label: "Max" },
];
const WT_PICKER = [
  { value: true, label: "On" },
  { value: false, label: "Off" },
];
const AUTO_PICKER = [
  { value: true, label: "On" },
  { value: false, label: "Off" },
];

const ATTACH_RE = /\[Attached file: ([^\]]+)\]/g;

/** Split description into { text, files[] } */
function parseDesc(desc) {
  if (!desc) return { text: "", files: [] };
  const files = [];
  let m;
  while ((m = ATTACH_RE.exec(desc)) !== null) files.push(m[1]);
  ATTACH_RE.lastIndex = 0;
  const text = desc.replace(ATTACH_RE, "").replace(/\n{2,}/g, "\n").trim();
  return { text, files };
}

function fileName(path) {
  return path.split("/").pop() || path;
}

function isImagePath(path) {
  return /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(path);
}

export default memo(function InboxCard({ task, selecting, selected, onToggle, expanded, onExpand, onRefresh, dragHandleProps }) {
  const projColor = "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400";
  const isExpanded = expanded && !selecting;
  const { projects } = useProjects();
  const projectPicker = useMemo(() => [
    { value: "", label: "None" },
    ...projects.map(p => ({ value: p.name, label: p.name })),
  ], [projects]);

  const savedDesc = task.description || "";
  const parsed = useMemo(() => parseDesc(savedDesc), [savedDesc]);

  const [dispatching, setDispatching] = useState(false);
  const [previewIndex, setPreviewIndex] = useState(null);

  // --- inline title editing ---
  const [titleEditing, setTitleEditing] = useState(false);
  const titleRef = useRef(null);

  const startTitleEditing = (e) => {
    e.stopPropagation();
    if (!isExpanded || titleEditing) return;
    setTitleEditing(true);
    requestAnimationFrame(() => {
      const el = titleRef.current;
      if (!el) return;
      el.focus();
      const sel = window.getSelection();
      sel.selectAllChildren(el);
      sel.collapseToEnd();
    });
  };

  const saveTitle = useCallback(async () => {
    const el = titleRef.current;
    if (!el) return;
    const text = el.innerText.trim();
    setTitleEditing(false);
    if (text && text !== task.title) {
      await updateTaskV2(task.id, { title: text });
      onRefresh?.();
    }
    try { localStorage.removeItem(`draft:inbox-title:${task.id}`); } catch {}
  }, [task.id, task.title, onRefresh]);

  // --- inline description editing ---
  const [editing, setEditing] = useState(false);
  const editRef = useRef(null);
  const fileInputRef = useRef(null);
  const filePickerOpenRef = useRef(false);

  useEffect(() => { if (!isExpanded) { setEditing(false); setTitleEditing(false); } }, [isExpanded]);

  // --- draft persistence (survives app close/restart) ---
  useLayoutEffect(() => {
    if (!isExpanded) return;
    try {
      const td = localStorage.getItem(`draft:inbox-title:${task.id}`);
      if (td && titleRef.current) titleRef.current.innerText = td;
      const dd = localStorage.getItem(`draft:inbox-desc:${task.id}`);
      if (dd != null && editRef.current) editRef.current.innerText = dd;
    } catch {}
  }, [isExpanded, task.id]);

  useEffect(() => {
    if (!isExpanded) return;
    const titleEl = titleRef.current;
    const descEl = editRef.current;
    const saveTitleDraft = () => {
      try {
        const t = titleEl?.innerText?.trim() || "";
        if (t && t !== task.title) localStorage.setItem(`draft:inbox-title:${task.id}`, t);
        else localStorage.removeItem(`draft:inbox-title:${task.id}`);
      } catch {}
    };
    const saveDescDraft = () => {
      try {
        const t = descEl?.innerText?.trim() || "";
        if (t !== (parsed.text || "").trim()) localStorage.setItem(`draft:inbox-desc:${task.id}`, t);
        else localStorage.removeItem(`draft:inbox-desc:${task.id}`);
      } catch {}
    };
    titleEl?.addEventListener("input", saveTitleDraft);
    descEl?.addEventListener("input", saveDescDraft);
    const flush = () => { saveTitleDraft(); saveDescDraft(); };
    const onVis = () => { if (document.visibilityState === "hidden") flush(); };
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("pagehide", flush);
    return () => {
      titleEl?.removeEventListener("input", saveTitleDraft);
      descEl?.removeEventListener("input", saveDescDraft);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("pagehide", flush);
    };
  }, [isExpanded, task.id, task.title, parsed.text]);

  // Clear filePickerOpen flag when the file picker is cancelled (no file selected)
  useEffect(() => {
    const el = fileInputRef.current;
    if (!el) return;
    const onCancel = () => { filePickerOpenRef.current = false; };
    el.addEventListener("cancel", onCancel);
    return () => el.removeEventListener("cancel", onCancel);
  }, []);

  const startEditing = (e) => {
    e.stopPropagation();
    if (editing) return;
    setEditing(true);
    requestAnimationFrame(() => {
      const el = editRef.current;
      if (!el) return;
      el.focus();
      const sel = window.getSelection();
      sel.selectAllChildren(el);
      sel.collapseToEnd();
    });
  };

  /** Rebuild full description from text + attached files */
  const buildFullDesc = useCallback((text, files) => {
    const parts = [text.trim()];
    for (const f of files) parts.push(`[Attached file: ${f}]`);
    return parts.filter(Boolean).join("\n") || null;
  }, []);

  const saveDesc = useCallback(async () => {
    const el = editRef.current;
    if (!el) return;
    const text = el.innerText.trim();
    setEditing(false);
    if (text !== parsed.text.trim()) {
      await updateTaskV2(task.id, { description: buildFullDesc(text, parsed.files) });
      onRefresh?.();
    }
    try { localStorage.removeItem(`draft:inbox-desc:${task.id}`); } catch {}
  }, [task.id, parsed, buildFullDesc, onRefresh]);

  // --- voice recording ---
  const appendToDesc = useCallback((text) => {
    const el = editRef.current;
    const currentText = el ? el.innerText.trim() : parsed.text;
    const updated = currentText ? currentText + "\n" + text : text;
    if (el) el.innerText = updated;
    try { localStorage.removeItem(`draft:inbox-desc:${task.id}`); } catch {}
    updateTaskV2(task.id, { description: buildFullDesc(updated, parsed.files) }).then(() => onRefresh?.());
  }, [task.id, parsed, buildFullDesc, onRefresh]);

  const voice = useVoiceRecorder({
    onTranscript: appendToDesc,
    onError: () => {},
  });

  // --- file upload ---
  const addFiles = useCallback(async (fileList) => {
    const uploadedPaths = [];
    for (const file of fileList) {
      if (file.size > 50 * 1024 * 1024) continue;
      try {
        const result = await uploadFile(file);
        uploadedPaths.push(result.path);
      } catch { /* skip */ }
    }
    if (uploadedPaths.length === 0) return;
    const currentText = editRef.current?.innerText?.trim() || parsed.text;
    const newFiles = [...parsed.files, ...uploadedPaths];
    await updateTaskV2(task.id, { description: buildFullDesc(currentText, newFiles) });
    onRefresh?.();
  }, [task.id, parsed, buildFullDesc, onRefresh]);

  const handleFileSelect = async (e) => {
    e.stopPropagation();
    filePickerOpenRef.current = false;
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    await addFiles(files);
  };

  const handlePaste = (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) { if (item.kind === "file") { const f = item.getAsFile(); if (f) files.push(f); } }
    if (files.length > 0) {
      e.preventDefault();
      addFiles(files);
    }
  };

  const removeFile = useCallback(async (filePath) => {
    const currentText = editRef.current?.innerText?.trim() || parsed.text;
    const newFiles = parsed.files.filter(f => f !== filePath);
    await updateTaskV2(task.id, { description: buildFullDesc(currentText, newFiles) });
    onRefresh?.();
  }, [task.id, parsed, buildFullDesc, onRefresh]);

  // --- notify_at ---
  const dateRef = useRef(null);

  const handleCalendar = (e) => {
    e.stopPropagation();
    requestAnimationFrame(() => dateRef.current?.showPicker?.());
  };

  const handleDateChange = async (e) => {
    const val = e.target.value;
    if (val) {
      await updateTaskV2(task.id, { notify_at: new Date(val).toISOString() });
      onRefresh?.();
    }
  };

  // --- dispatch (launch agent) ---
  const handleDispatch = async (e) => {
    e.stopPropagation();
    setDispatching(true);
    try {
      if (editing && editRef.current) {
        const text = editRef.current.innerText.trim();
        if (text !== parsed.text.trim()) {
          await updateTaskV2(task.id, { description: buildFullDesc(text, parsed.files) });
        }
      }
      await dispatchTask(task.id);
      try { localStorage.removeItem(`draft:inbox-title:${task.id}`); localStorage.removeItem(`draft:inbox-desc:${task.id}`); } catch {}
      onRefresh?.();
    } catch {
      setDispatching(false);
    }
  };

  // --- card actions ---
  const handleClick = () => {
    if (filePickerOpenRef.current) return;
    if (selecting) onToggle?.(task.id);
    else onExpand?.(task.id);
  };

  const update = async (field, value) => {
    await updateTaskV2(task.id, { [field]: value });
    onRefresh?.();
  };

  // collapsed preview: text only, no [Attached file:] lines
  const preview = parsed.text || null;

  if (dispatching) return null;

  return (
    <div className="relative">
      <CardShell taskId={task.id} expanded={expanded} selecting={selecting} selected={selected}>
        <div
          className={`flex items-start gap-3 px-5 cursor-pointer transition-[padding] duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)] ${
            expanded && !selecting ? "pt-5 pb-3" : cardPadding(expanded, selecting)
          }`}
          onClick={handleClick}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" && !editing) handleClick(); }}
        >
          {dragHandleProps && (
            <button
              type="button"
              {...dragHandleProps.listeners}
              {...dragHandleProps.attributes}
              className="touch-none p-1 -ml-2 mr-0 rounded text-ghost hover:text-faint transition-colors cursor-grab active:cursor-grabbing self-center"
              onClick={(e) => e.stopPropagation()}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="currentColor">
                <rect x="3" y="4" width="10" height="1.5" rx="0.75" />
                <rect x="3" y="8" width="10" height="1.5" rx="0.75" />
                <rect x="3" y="12" width="10" height="1.5" rx="0.75" />
              </svg>
            </button>
          )}
          <div className={`flex-1 min-w-0 ${isExpanded ? "flex flex-col" : ""}`}>
            {/* Title + time — pinned to top */}
            <div className="flex items-start justify-between gap-3 shrink-0">
              {isExpanded ? (
                <div
                  ref={titleRef}
                  contentEditable={titleEditing}
                  suppressContentEditableWarning
                  onClick={startTitleEditing}
                  onBlur={saveTitle}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); titleRef.current?.blur(); } }}
                  className={`text-base font-semibold leading-snug whitespace-pre-wrap outline-none flex-1 min-w-0 ${
                    titleEditing ? "text-heading cursor-text" : "text-heading cursor-pointer"
                  }`}
                >
                  {task.title}
                </div>
              ) : (
                <p className="text-base font-medium leading-snug text-heading truncate transition-all duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)]">
                  {task.title}
                </p>
              )}
              <span className="text-[11px] text-faint shrink-0 mt-0.5">
                {relativeTime(task.created_at)}
              </span>
            </div>

            {/* Description — flexible middle, grows to fill space */}
            {isExpanded ? (
              <div className="min-h-[60px] mt-1.5 cursor-text" onClick={startEditing}>
                <div
                  ref={editRef}
                  contentEditable={editing}
                  suppressContentEditableWarning
                  onBlur={saveDesc}
                  onPaste={handlePaste}
                  className={`text-sm leading-relaxed outline-none whitespace-pre-wrap ${
                    editing ? "text-body" : parsed.text ? "text-dim" : "text-faint/40"
                  }`}
                >
                  {parsed.text || (editing ? "" : "Tap to add description...")}
                </div>
                {voice.refining && (
                  <div className="px-1 pb-1 text-sm text-cyan-400/80 italic animate-pulse">
                    Refining...
                  </div>
                )}
              </div>
            ) : (
              <>
                {preview && (
                  <p className="text-sm text-dim leading-relaxed mt-1 line-clamp-2">
                    {preview.slice(0, 200)}
                  </p>
                )}
                <div className="flex flex-wrap items-center gap-1 mt-1.5">
                  {task.attempt_number > 1 && (
                    <span className="text-[10px] font-semibold px-1.5 py-px rounded-full bg-orange-500/15 text-orange-500 dark:text-orange-400">
                      Retry #{task.attempt_number}
                    </span>
                  )}
                  <span className={`text-[10px] font-medium rounded-full px-1.5 py-px ${projColor}`}>
                    {task.project_name || "Project"}
                  </span>
                  {task.use_worktree !== false && (
                    <span className="text-[10px] font-medium px-1.5 py-px rounded-full bg-purple-500/15 text-purple-500 dark:text-purple-400 flex items-center gap-0.5">
                      <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
                      </svg>
                      {task.worktree_name || "Worktree"}
                    </span>
                  )}
                  {task.skip_permissions && (
                    <span className="text-[10px] font-medium px-1.5 py-px rounded-full bg-amber-500/15 text-amber-500 dark:text-amber-400">
                      Auto
                    </span>
                  )}
                  {task.model && (
                    <span className="text-[10px] font-medium px-1.5 py-px rounded-full bg-elevated text-dim">
                      {modelDisplayName(task.model)}
                    </span>
                  )}
                  {task.effort && (
                    <span className="text-[10px] font-medium px-1.5 py-px rounded-full bg-elevated text-dim">
                      {task.effort.charAt(0).toUpperCase() + task.effort.slice(1)}
                    </span>
                  )}
                  {task.notify_at && (
                    <span className="text-[10px] text-amber-500 dark:text-amber-400 flex items-center gap-0.5">
                      <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      {relativeTime(task.notify_at)}
                    </span>
                  )}
                </div>
              </>
            )}

            {/* Bottom area — pinned to bottom */}
            {isExpanded ? (
              <div className="shrink-0 mt-1.5 space-y-3">
                {/* Agent summary from previous attempt */}
                {task.attempt_number > 1 && (
                  <div className="rounded-lg bg-surface border border-edge/30 px-3 py-2">
                    <div className="flex items-center justify-between mb-0.5">
                      <p className="text-[10px] font-semibold text-dim">
                        Previous agent summary
                      </p>
                      {task.agent_summary && task.agent_summary !== ":::generating:::" && (
                        <button
                          className="text-[10px] text-dim/40 px-1 hover:text-accent"
                          onClick={(e) => { e.stopPropagation(); regenerateTaskSummary(task.id); }}
                        >refresh</button>
                      )}
                    </div>
                    {task.agent_summary === ":::generating:::" ? (
                      <p className="text-xs text-dim/50 italic">Generating summary...</p>
                    ) : task.agent_summary ? (
                      <p className="text-xs text-dim/80 whitespace-pre-wrap">{task.agent_summary}</p>
                    ) : (
                      <button
                        className="text-xs text-accent/70 hover:text-accent"
                        onClick={(e) => { e.stopPropagation(); regenerateTaskSummary(task.id); }}
                      >Generate summary</button>
                    )}
                  </div>
                )}
                {/* User feedback from previous attempt */}
                {task.attempt_number > 1 && task.retry_context && (
                  <div className="rounded-lg bg-orange-500/10 border border-orange-500/20 px-3 py-2">
                    <p className="text-[10px] font-semibold text-orange-500 dark:text-orange-400 mb-0.5">
                      Your notes
                    </p>
                    <p className="text-xs text-dim whitespace-pre-wrap">{task.retry_context.replace(/^User feedback:\s*/i, "")}</p>
                  </div>
                )}
                {/* Attachment chips */}
                {parsed.files.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {parsed.files.map((f, i) => (
                      <div key={f} className="flex items-center gap-1 px-2 py-1 rounded-lg bg-elevated text-xs max-w-[160px] cursor-pointer"
                        onClick={(e) => { e.stopPropagation(); setPreviewIndex(i); }}>
                        {isImagePath(f) ? (
                          <img src={`/api/uploads/${fileName(f)}`} alt="" className="w-6 h-6 rounded object-cover shrink-0" />
                        ) : (
                          <svg className="w-3.5 h-3.5 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                          </svg>
                        )}
                        <span className="truncate flex-1 min-w-0 text-dim">{fileName(f)}</span>
                        <button type="button" onClick={(e) => { e.stopPropagation(); removeFile(f); }}
                          className="shrink-0 text-faint hover:text-heading">
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                {/* Tags row: Project → WT → Auto → Priority → Model → Effort → notify(last) */}
                <div className="flex flex-wrap items-center gap-1.5">
                  {task.attempt_number > 1 && (
                    <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-orange-500/15 text-orange-500 dark:text-orange-400">
                      Retry #{task.attempt_number}
                    </span>
                  )}
                  <TagPicker options={projectPicker} value={task.project_name || ""} onSelect={(v) => update("project_name", v)}
                    className={`text-[11px] font-medium rounded-full px-2 py-0.5 cursor-pointer active:scale-90 transition-transform ${projColor}`}>
                    {task.project_name || "Project"}
                  </TagPicker>
                  <TagPicker options={WT_PICKER} value={task.use_worktree !== false} onSelect={(v) => update("use_worktree", v)}
                    keepOpenOnSelect={(v) => v === true}
                    className={`text-[11px] font-medium px-1.5 py-0.5 rounded-full cursor-pointer active:scale-90 transition-all ${
                      task.use_worktree !== false ? "bg-purple-500/15 text-purple-500 dark:text-purple-400" : "bg-elevated text-faint"
                    }`}
                    extra={task.use_worktree !== false ? (
                      <input
                        type="text"
                        placeholder="name (blank = random)"
                        value={task.worktree_name || ""}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => { e.stopPropagation(); update("worktree_name", e.target.value); }}
                        className="w-full mt-1 px-2 py-1.5 rounded-lg text-xs bg-elevated text-heading placeholder-hint outline-none border border-edge/30 focus:border-cyan-500/50 transition-colors"
                      />
                    ) : null}>
                    <span className="flex items-center gap-0.5">
                      <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
                      </svg>
                      {task.worktree_name || "Worktree"}
                    </span>
                  </TagPicker>
                  <TagPicker options={AUTO_PICKER} value={!!task.skip_permissions} onSelect={(v) => update("skip_permissions", v)}
                    className={`text-[11px] font-medium px-1.5 py-0.5 rounded-full cursor-pointer active:scale-90 transition-all ${
                      task.skip_permissions ? "bg-amber-500/15 text-amber-500 dark:text-amber-400" : "bg-elevated text-faint"
                    }`}>
                    Auto
                  </TagPicker>
                  {task.model && (
                    <TagPicker options={MODEL_PICKER} value={task.model} onSelect={(v) => update("model", v)}
                      className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-elevated text-dim cursor-pointer active:scale-90 transition-transform">
                      {modelDisplayName(task.model)}
                    </TagPicker>
                  )}
                  {task.effort && (
                    <TagPicker options={EFFORT_PICKER} value={task.effort} onSelect={(v) => update("effort", v)}
                      className="text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-elevated text-dim cursor-pointer active:scale-90 transition-transform">
                      {task.effort.charAt(0).toUpperCase() + task.effort.slice(1)}
                    </TagPicker>
                  )}
                  {task.notify_at && (
                    <span className="text-[11px] text-amber-500 dark:text-amber-400 flex items-center gap-0.5">
                      <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      {relativeTime(task.notify_at)}
                    </span>
                  )}
                </div>

                {/* Action toolbar */}
                <div className="flex items-center gap-2">
                  <input ref={fileInputRef} type="file" multiple className="hidden" onClick={(e) => e.stopPropagation()} onChange={handleFileSelect} />
                  <button type="button" onClick={(e) => { e.stopPropagation(); filePickerOpenRef.current = true; fileInputRef.current?.click(); }}
                    className="w-8 h-8 rounded-full bg-elevated flex items-center justify-center text-dim hover:text-heading active:scale-90 transition-all"
                    title="Attach file">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                    </svg>
                  </button>
                  <button type="button" onClick={async (e) => {
                      e.stopPropagation();
                      if (!confirm("Delete this task?")) return;
                      await cancelTask(task.id);
                      onRefresh?.();
                    }}
                    className="w-8 h-8 rounded-full bg-elevated flex items-center justify-center text-dim hover:text-red-500 active:scale-90 transition-all"
                    title="Delete task">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                    </svg>
                  </button>

                  <div className="flex-1" />

                  {voice.recording && voice.remainingSeconds != null && (
                    <span className="text-[9px] text-red-400 font-medium tabular-nums">
                      {Math.floor(voice.remainingSeconds / 60)}:{String(Math.floor(voice.remainingSeconds % 60)).padStart(2, "0")}
                    </span>
                  )}
                  <button type="button" onClick={(e) => { e.stopPropagation(); voice.toggleRecording(); }}
                    disabled={voice.voiceLoading}
                    className={`w-8 h-8 rounded-full flex items-center justify-center transition-all active:scale-90 ${
                      voice.recording ? "bg-red-500 text-white"
                        : voice.voiceLoading ? "bg-elevated cursor-wait"
                        : "bg-elevated text-dim hover:text-heading"
                    }`}
                    title={voice.recording ? "Stop recording" : "Voice input"}>
                    {voice.voiceLoading ? (
                      <svg className="animate-spin w-4 h-4 text-body" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                    ) : (
                      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                        <line x1="12" y1="19" x2="12" y2="23" />
                        <line x1="8" y1="23" x2="16" y2="23" />
                      </svg>
                    )}
                  </button>

                  <div className="relative overflow-hidden">
                    <input ref={dateRef} type="datetime-local" className="absolute opacity-0 w-0 h-0 pointer-events-none"
                      value={task.notify_at ? new Date(task.notify_at).toISOString().slice(0, 16) : ""}
                      onChange={handleDateChange} />
                    <button type="button" onClick={handleCalendar}
                      className={`w-8 h-8 rounded-full flex items-center justify-center transition-all active:scale-90 ${
                        task.notify_at ? "bg-amber-500 text-white" : "bg-elevated text-dim hover:text-heading"
                      }`}
                      title="Set notification time">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5" />
                      </svg>
                    </button>
                  </div>

                  <button type="button" onClick={handleDispatch}
                    disabled={!task.project_name}
                    className={`w-8 h-8 rounded-full flex items-center justify-center active:scale-90 transition-all ${
                      task.project_name ? "bg-cyan-500 text-white hover:bg-cyan-400" : "bg-elevated text-faint cursor-not-allowed"
                    }`}
                    title={task.project_name ? "Start task" : "Select a project first"}>
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
                    </svg>
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </CardShell>
      {previewIndex != null && parsed.files.length > 0 && (
        <ImageLightbox
          media={parsed.files.map(f => ({
            src: `/api/uploads/${fileName(f)}`,
            filename: fileName(f),
            type: isImagePath(f) ? "image" : "file",
          }))}
          initialIndex={previewIndex}
          onClose={() => setPreviewIndex(null)}
        />
      )}
    </div>
  );
});
