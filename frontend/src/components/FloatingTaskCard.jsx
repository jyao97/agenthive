import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { fetchTaskV2, updateTaskV2 } from "../lib/api";
import { renderMarkdown, relativeTime } from "../lib/formatters";
import useDraft from "../hooks/useDraft";

const STATUS_DOT = {
  inbox: "bg-slate-400",
  pending: "bg-blue-500",
  executing: "bg-amber-500",
  complete: "bg-green-500",
  failed: "bg-red-500",
  cancelled: "bg-gray-400",
  timeout: "bg-orange-500",
};

const STATUS_LABEL = {
  inbox: "Inbox",
  pending: "Pending",
  executing: "In Progress",
  complete: "Complete",
  failed: "Failed",
  cancelled: "Dropped",
  timeout: "Timeout",
  review: "Review",
  merging: "Merging",
  conflict: "Conflict",
  planning: "Planning",
  rejected: "Rejected",
};

/* Metadata row — label on left, value on right, bottom divider */
function MetaRow({ label, children, last }) {
  return (
    <div className={`flex items-center justify-between py-2.5 ${last ? "" : "border-b border-divider"}`}>
      <span className="text-xs text-dim shrink-0">{label}</span>
      <span className="text-xs text-heading text-right truncate ml-4">{children}</span>
    </div>
  );
}

export default function FloatingTaskCard({ taskId, onClose, onAction }) {
  const navigate = useNavigate();
  const [task, setTask] = useState(null);
  const [loading, setLoading] = useState(true);
  const [editingTitle, setEditingTitle] = useState(false);
  const [editingDesc, setEditingDesc] = useState(false);
  const [selectedPill, setSelectedPill] = useState(null);
  const [editingNote, setEditingNote] = useState(false);
  const [titleDraft, setTitleDraft, clearTitleDraft] = useDraft(taskId ? `task-edit:${taskId}:title` : null);
  const [descDraft, setDescDraft, clearDescDraft] = useDraft(taskId ? `task-edit:${taskId}:desc` : null);
  const [noteDraft, setNoteDraft, clearNoteDraft] = useDraft(taskId ? `task-edit:${taskId}:note` : null);
  const cardRef = useRef(null);

  useEffect(() => {
    if (!taskId) return;
    setLoading(true);
    fetchTaskV2(taskId)
      .then((t) => {
        setTask(t);
        if (t.attempt_agents?.length) setSelectedPill(t.attempt_agents.length - 1);
        if (localStorage.getItem(`draft:task-edit:${taskId}:title`) === null) setTitleDraft(t.title || "");
        if (localStorage.getItem(`draft:task-edit:${taskId}:desc`) === null) setDescDraft(t.description || "");
        if (localStorage.getItem(`draft:task-edit:${taskId}:note`) === null) setNoteDraft(t.note || "");
      })
      .catch((e) => console.warn("Task fetch failed:", e))
      .finally(() => setLoading(false));
  }, [taskId]);

  useEffect(() => {
    const handler = (e) => {
      if (cardRef.current && !cardRef.current.contains(e.target)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const saveTitle = async () => {
    if (!task || titleDraft.trim() === task.title) { setEditingTitle(false); return; }
    try {
      const updated = await updateTaskV2(task.id, { title: titleDraft.trim() });
      setTask(updated);
      clearTitleDraft();
    } catch (e) { console.warn("Task update failed:", e); }
    setEditingTitle(false);
  };

  const saveDesc = async () => {
    if (!task || descDraft.trim() === (task.description || "")) { setEditingDesc(false); return; }
    try {
      const updated = await updateTaskV2(task.id, { description: descDraft.trim() });
      setTask(updated);
      clearDescDraft();
    } catch (e) { console.warn("Task update failed:", e); }
    setEditingDesc(false);
  };

  const saveNote = async () => {
    if (!task || noteDraft.trim() === (task.note || "")) { setEditingNote(false); return; }
    try {
      const updated = await updateTaskV2(task.id, { note: noteDraft.trim() || null });
      setTask(updated);
      clearNoteDraft();
    } catch (e) { console.warn("Note update failed:", e); }
    setEditingNote(false);
  };

  if (!taskId) return null;

  const canEdit = task?.status === "inbox";
  const statusKey = task?.status?.toLowerCase();

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <div ref={cardRef} className="bg-surface rounded-2xl shadow-[0_8px_30px_rgba(0,0,0,0.12)] dark:shadow-[0_8px_30px_rgba(0,0,0,0.4)] border border-divider max-w-md w-full max-h-[80vh] overflow-y-auto">

        {/* ── Header: close + title ── */}
        <div className="px-5 pt-5 pb-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              {editingTitle ? (
                <input
                  autoFocus
                  value={titleDraft}
                  onChange={(e) => setTitleDraft(e.target.value)}
                  onBlur={saveTitle}
                  onKeyDown={(e) => { if (e.key === "Enter") saveTitle(); if (e.key === "Escape") { setTitleDraft(task?.title || ""); setEditingTitle(false); } }}
                  className="w-full text-lg font-semibold text-heading bg-transparent px-0 py-0 border-0 border-b border-cyan-500 focus:outline-none"
                />
              ) : (
                <h3
                  onClick={() => { if (canEdit) setEditingTitle(true); }}
                  className={`text-lg font-semibold text-heading leading-snug ${canEdit ? "cursor-pointer hover:text-cyan-400" : ""}`}
                >
                  {task?.title || "Untitled"}
                </h3>
              )}
            </div>
            <button type="button" onClick={onClose}
              className="shrink-0 w-7 h-7 flex items-center justify-center rounded-full text-faint hover:text-body hover:bg-input transition-colors -mt-0.5">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {loading ? (
          <div className="px-5 pb-5 text-center text-dim text-sm">Loading...</div>
        ) : task ? (
          <>
            {/* ── Metadata rows ── */}
            <div className="px-5">
              {/* Status */}
              <MetaRow label="Status">
                <span className="inline-flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${STATUS_DOT[statusKey] || "bg-gray-400"} ${statusKey === "executing" ? "animate-pulse" : ""}`} />
                  <span>{STATUS_LABEL[statusKey] || task.status}</span>
                </span>
              </MetaRow>

              {/* Project */}
              {task.project_name && (
                <MetaRow label="Project">
                  {task.project_name}
                </MetaRow>
              )}

              {/* Created */}
              <MetaRow label="Created" last={!task.model && !task.effort}>
                {relativeTime(task.created_at)}
              </MetaRow>

              {/* Model — if set */}
              {task.model && (
                <MetaRow label="Model" last={!task.effort}>
                  {task.model.replace("claude-", "").replace(/-\d+$/, "")}
                </MetaRow>
              )}

              {/* Effort — if set */}
              {task.effort && (
                <MetaRow label="Effort" last>
                  <span className="capitalize">{task.effort}</span>
                </MetaRow>
              )}
            </div>

            {/* ── Description section ── */}
            <div className="px-5 pt-4">
              <p className="text-[11px] font-medium text-dim uppercase tracking-wider mb-2">Description</p>
              {editingDesc ? (
                <textarea
                  autoFocus
                  value={descDraft}
                  onChange={(e) => setDescDraft(e.target.value)}
                  onBlur={saveDesc}
                  rows={4}
                  className="w-full text-sm text-body bg-transparent px-0 py-0 resize-none focus:outline-none border-0 placeholder-hint"
                  placeholder="Add description..."
                />
              ) : (
                <div
                  onClick={() => { if (canEdit) setEditingDesc(true); }}
                  className={canEdit ? "cursor-pointer" : ""}
                >
                  {task.description ? (
                    <p className="text-sm text-body leading-relaxed whitespace-pre-wrap">{task.description}</p>
                  ) : (
                    <p className="text-sm text-hint">Add description</p>
                  )}
                </div>
              )}
            </div>

            {/* ── Attempts section ── */}
            {task.attempt_agents?.length > 0 && (() => {
              const total = task.attempt_agents.length;
              const sel = selectedPill ?? total - 1;
              const showSummary = sel < total - 1 && sel === total - 2 && task.agent_summary;
              const showUserFeedback = sel < total - 1 && sel === total - 2 && task.retry_context;

              return (
                <div className="px-5 pt-4">
                  <p className="text-[11px] font-medium text-dim uppercase tracking-wider mb-2">Attempts</p>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {task.attempt_agents.map((a, i) => (
                      <button
                        key={a.agent_id}
                        type="button"
                        onClick={() => setSelectedPill(i)}
                        className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors ${
                          i === sel
                            ? "bg-cyan-500 text-white"
                            : "bg-input text-dim hover:text-body"
                        }`}
                      >
                        #{i + 1}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => { onClose(); navigate(`/agents/${task.attempt_agents[sel].agent_id}`); }}
                      className="ml-auto text-[11px] font-medium text-cyan-500 hover:text-cyan-400 transition-colors"
                    >
                      Enter Chat &rarr;
                    </button>
                  </div>

                  {showSummary && (
                    <div className="mt-3">
                      <p className="text-[11px] font-medium text-dim mb-1">Agent Summary</p>
                      {task.agent_summary === ":::generating:::" ? (
                        <p className="text-xs text-faint italic">Generating summary...</p>
                      ) : (
                        <p className="text-xs text-body leading-relaxed whitespace-pre-wrap">{task.agent_summary}</p>
                      )}
                    </div>
                  )}

                  {showUserFeedback && (
                    <div className="mt-3">
                      <p className="text-[11px] font-medium text-dim mb-1">User Feedback</p>
                      <p className="text-xs text-body leading-relaxed whitespace-pre-wrap">{task.retry_context}</p>
                    </div>
                  )}
                </div>
              );
            })()}

            {/* ── Notes section ── */}
            <div className="px-5 pt-4">
              <p className="text-[11px] font-medium text-dim uppercase tracking-wider mb-2">Notes</p>
              {editingNote ? (
                <textarea
                  autoFocus
                  value={noteDraft}
                  onChange={(e) => setNoteDraft(e.target.value)}
                  onBlur={saveNote}
                  onKeyDown={(e) => { if (e.key === "Escape") { setNoteDraft(task.note || ""); setEditingNote(false); } }}
                  rows={3}
                  className="w-full text-sm text-body bg-transparent px-0 py-0 resize-none focus:outline-none border-0 placeholder-hint"
                  placeholder="Add a note..."
                />
              ) : (
                <div
                  onClick={() => { setNoteDraft(task.note || ""); setEditingNote(true); }}
                  className="cursor-pointer"
                >
                  {task.note ? (
                    <div className="text-sm text-body leading-relaxed prose-sm">{renderMarkdown(task.note, task.project_name)}</div>
                  ) : (
                    <p className="text-sm text-hint">Add a note</p>
                  )}
                </div>
              )}
            </div>

            {/* ── Actions ── */}
            {task.status === "executing" && onAction && (
              <div className="px-5 pt-4 flex gap-2">
                <button
                  type="button"
                  onClick={() => onAction("complete")}
                  className="flex-1 px-3 py-2 rounded-lg text-xs font-semibold bg-green-600 text-white hover:bg-green-500 transition-colors"
                >
                  Complete
                </button>
                <button
                  type="button"
                  onClick={() => onAction("incomplete")}
                  className="flex-1 px-3 py-2 rounded-lg text-xs font-semibold bg-transparent border border-edge text-dim hover:text-body hover:border-ring-hover transition-colors"
                >
                  Mark Incomplete
                </button>
              </div>
            )}

            {/* Bottom spacing */}
            <div className="h-5" />
          </>
        ) : (
          <div className="px-5 pb-5 text-center text-dim text-sm">Task not found</div>
        )}
      </div>
    </div>
  );
}
