import { useState, useEffect, useRef } from "react";
import { fetchTaskV2, updateTaskV2 } from "../lib/api";
import useDraft from "../hooks/useDraft";

const STATUS_COLORS = {
  inbox: "bg-slate-500",
  pending: "bg-blue-500",
  executing: "bg-amber-500",
  complete: "bg-green-500",
  failed: "bg-red-500",
  cancelled: "bg-gray-500",
  timeout: "bg-orange-500",
};

export default function FloatingTaskCard({ taskId, onClose, onAction }) {
  const [task, setTask] = useState(null);
  const [loading, setLoading] = useState(true);
  const [editingTitle, setEditingTitle] = useState(false);
  const [editingDesc, setEditingDesc] = useState(false);
  const [selectedPill, setSelectedPill] = useState(null);
  const [titleDraft, setTitleDraft, clearTitleDraft] = useDraft(taskId ? `task-edit:${taskId}:title` : null);
  const [descDraft, setDescDraft, clearDescDraft] = useDraft(taskId ? `task-edit:${taskId}:desc` : null);
  const cardRef = useRef(null);

  useEffect(() => {
    if (!taskId) return;
    setLoading(true);
    fetchTaskV2(taskId)
      .then((t) => {
        setTask(t);
        // Default selected pill to last attempt
        if (t.attempt_agents?.length) setSelectedPill(t.attempt_agents.length - 1);
        // Only initialize from server if no draft exists (preserve crash recovery drafts)
        if (localStorage.getItem(`draft:task-edit:${taskId}:title`) === null) setTitleDraft(t.title || "");
        if (localStorage.getItem(`draft:task-edit:${taskId}:desc`) === null) setDescDraft(t.description || "");
      })
      .catch((e) => console.warn("Task fetch failed:", e))
      .finally(() => setLoading(false));
  }, [taskId]);

  // Close on click outside
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

  if (!taskId) return null;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <div ref={cardRef} className="bg-surface rounded-2xl shadow-card max-w-md w-full max-h-[80vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 pb-2">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {task && (
              <span className={`shrink-0 px-2 py-0.5 rounded-full text-[10px] font-semibold text-white uppercase ${STATUS_COLORS[task.status] || "bg-gray-500"}`}>
                {task.status}
              </span>
            )}
            {task?.attempt_agents?.length > 1 && (
              <span className="shrink-0 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/20 text-amber-400">
                {task.attempt_agents.length} trials
              </span>
            )}
          </div>
          <button type="button" onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-dim hover:text-body hover:bg-input transition-colors">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {loading ? (
          <div className="p-4 text-center text-dim text-sm">Loading...</div>
        ) : task ? (
          <div className="px-4 pb-4 space-y-3">
            {/* Title */}
            {editingTitle ? (
              <input
                autoFocus
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={saveTitle}
                onKeyDown={(e) => { if (e.key === "Enter") saveTitle(); if (e.key === "Escape") { setTitleDraft(task.title || ""); setEditingTitle(false); } }}
                className="w-full text-base font-semibold text-heading bg-input rounded-lg px-2 py-1 focus:outline-none focus:ring-1 focus:ring-amber-500/50"
              />
            ) : (
              <h3
                onClick={() => { if (task.status === "inbox") setEditingTitle(true); }}
                className={`text-base font-semibold text-heading ${task.status === "inbox" ? "cursor-pointer hover:text-amber-400" : ""}`}
                title={task.status === "inbox" ? "Click to edit" : undefined}
              >
                {task.title || "Untitled"}
              </h3>
            )}

            {/* Project */}
            {task.project_name && (
              <p className="text-xs text-dim">{task.project_name}</p>
            )}

            {/* Description */}
            {editingDesc ? (
              <textarea
                autoFocus
                value={descDraft}
                onChange={(e) => setDescDraft(e.target.value)}
                onBlur={saveDesc}
                rows={4}
                className="w-full text-sm text-body bg-input rounded-lg px-2 py-1 resize-none focus:outline-none focus:ring-1 focus:ring-amber-500/50"
              />
            ) : (
              <p
                onClick={() => { if (task.status === "inbox") setEditingDesc(true); }}
                className={`text-sm text-body whitespace-pre-wrap ${task.status === "inbox" ? "cursor-pointer hover:text-amber-400" : ""} ${!task.description ? "text-dim italic" : ""}`}
                title={task.status === "inbox" ? "Click to edit" : undefined}
              >
                {task.description || "No description"}
              </p>
            )}

            {/* Attempt agents — pill toggles summary/context inline */}
            {task.attempt_agents?.length > 0 && (() => {
              const total = task.attempt_agents.length;
              const sel = selectedPill ?? total - 1;
              const isFirst = sel === 0;
              const isLast = sel === total - 1;

              // agent_summary describes attempt (total-2); retry_context describes attempt (total-1)
              const showPrevContext = !isFirst && isLast && task.retry_context;
              const showSummary = !isLast && sel === total - 2 && task.agent_summary;

              return (
                <div className="rounded-lg bg-input p-3 space-y-2">
                  <p className="text-xs font-semibold text-dim">
                    {total === 1 ? "Agent" : `Agents (${total} trials)`}
                  </p>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {task.attempt_agents.map((a, i) => (
                      <button
                        key={a.agent_id}
                        type="button"
                        onClick={() => setSelectedPill(i)}
                        className={`px-2 py-0.5 rounded-full text-[10px] font-medium transition-colors ${
                          i === sel
                            ? "bg-orange-500 text-white"
                            : "bg-orange-500/15 text-orange-500 dark:text-orange-400 hover:bg-orange-500/25"
                        }`}
                      >
                        #{i + 1}
                      </button>
                    ))}
                  </div>

                  {/* Previous Attempt Context — first, for the current/last attempt */}
                  {showPrevContext && (
                    <div className="border-t border-edge/30 pt-2 space-y-1">
                      <p className="text-[10px] font-semibold text-amber-400">Previous Attempt Context</p>
                      <p className="text-xs text-body whitespace-pre-wrap">{task.retry_context}</p>
                    </div>
                  )}

                  {/* Agent Summary — second, for the previous attempt */}
                  {showSummary && (
                    <div className="border-t border-edge/30 pt-2 space-y-1">
                      <p className="text-[10px] font-semibold text-dim">Agent Summary</p>
                      {task.agent_summary === ":::generating:::" ? (
                        <p className="text-xs text-dim/50 italic">Generating summary...</p>
                      ) : (
                        <p className="text-xs text-body whitespace-pre-wrap">{task.agent_summary}</p>
                      )}
                    </div>
                  )}
                </div>
              );
            })()}

            {/* Actions */}
            {task.status === "executing" && onAction && (
              <div className="flex gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => onAction("complete")}
                  className="flex-1 px-3 py-2 rounded-lg text-xs font-medium bg-green-600 text-white hover:bg-green-500 transition-colors"
                >
                  Complete
                </button>
                <button
                  type="button"
                  onClick={() => onAction("incomplete")}
                  className="flex-1 px-3 py-2 rounded-lg text-xs font-medium bg-amber-600 text-white hover:bg-amber-500 transition-colors"
                >
                  Mark Incomplete
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="p-4 text-center text-dim text-sm">Task not found</div>
        )}
      </div>
    </div>
  );
}
