import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { updateTaskV2, dispatchTask, cancelTask } from "../../lib/api";
import { relativeTime, renderMarkdown, DATE_SHORT } from "../../lib/formatters";
import ProjectSelector from "../ProjectSelector";
import SendLaterPicker from "../SendLaterPicker";
import { useToast } from "../../contexts/ToastContext";
import useDraft from "../../hooks/useDraft";

export default function TaskExpandedContent({ task, onRefresh, onCollapse }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [actionLoading, setActionLoading] = useState(false);

  const canEdit = task.status === "INBOX" || task.status === "PLANNING";
  const [showRemindPicker, setShowRemindPicker] = useState(false);

  // Editable fields — persisted to localStorage via useDraft for crash recovery
  const [editTitle, setEditTitle, clearTitleDraft] = useDraft(`task-edit:${task.id}:title`, task.title);
  const [editDesc, setEditDesc, clearDescDraft] = useDraft(`task-edit:${task.id}:desc`, task.description || "");
  const [editProject, setEditProject, clearProjectDraft] = useDraft(`task-edit:${task.id}:project`, task.project_name || "");
  const [editNotifyAt, setEditNotifyAt, clearNotifyDraft] = useDraft(
    `task-edit:${task.id}:notifyAt`,
    task.notify_at ? new Date(task.notify_at).toISOString().slice(0, 16) : ""
  );

  const clearAllDrafts = () => {
    clearTitleDraft(); clearDescDraft(); clearProjectDraft(); clearNotifyDraft();
  };

  const doAction = async (fn, ...args) => {
    setActionLoading(true);
    try {
      await fn(...args);
      onRefresh?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setActionLoading(false);
    }
  };

  const doDelete = async () => {
    if (!confirm("Delete this task?")) return;
    setActionLoading(true);
    try {
      await cancelTask(task.id);
      clearAllDrafts();
      onRefresh?.();
      onCollapse?.();
    } catch (err) {
      toast.error(err.message);
      setActionLoading(false);
    }
  };

  // Save edits then run an action
  const saveAndAction = async (actionFn, ...actionArgs) => {
    setActionLoading(true);
    try {
      // Collect changes
      const updates = {};
      if (editTitle && editTitle !== task.title) updates.title = editTitle;
      if (editDesc !== (task.description || "")) updates.description = editDesc;
      if (editProject && editProject !== task.project_name) updates.project_name = editProject;
      const origNotify = task.notify_at ? new Date(task.notify_at).toISOString().slice(0, 16) : "";
      if (editNotifyAt !== origNotify) updates.notify_at = editNotifyAt ? new Date(editNotifyAt).toISOString() : null;
      if (Object.keys(updates).length > 0) {
        await updateTaskV2(task.id, updates);
      }
      // Run the follow-up action if provided
      if (actionFn) await actionFn(...actionArgs);
      clearAllDrafts();
      onRefresh?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div
      className={canEdit ? "px-5 pb-4 pt-1 space-y-2" : "border-t border-divider mx-4 pb-4 pt-3 space-y-3"}
      onClick={(e) => e.stopPropagation()}
    >
      {/* ── COMPACT INLINE EDITOR for INBOX/PLANNING ── */}
      {canEdit ? (
        <>
          <div className="space-y-2">
            {/* Title — bold inline text, no label */}
            <input
              type="text"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              className="w-full text-base font-semibold text-heading bg-transparent px-0 py-0.5 border-0 border-b border-transparent focus:border-cyan-500 focus:outline-none transition-colors"
              placeholder="Task title"
            />

            {/* Description — textarea, no label */}
            <textarea
              value={editDesc}
              onChange={(e) => setEditDesc(e.target.value)}
              rows={2}
              className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-sm text-body resize-none focus:border-cyan-500 focus:outline-none placeholder-hint"
              placeholder="Add description..."
            />

            {/* Project — no label, compact selector */}
            <ProjectSelector value={editProject} onChange={setEditProject} />

            {/* Remind At */}
            <div className="relative">
              <button type="button" onClick={() => setShowRemindPicker(v => !v)}
                className={`w-full flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm transition-colors ${
                  editNotifyAt ? "bg-amber-500/10 border-amber-500/30 text-amber-400" : "bg-input border-edge text-dim hover:text-heading"
                }`}>
                <svg className="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                {editNotifyAt ? new Date(editNotifyAt).toLocaleString([], DATE_SHORT) : "Set reminder"}
              </button>
              {showRemindPicker && (
                <SendLaterPicker
                  onSelect={(iso) => { setEditNotifyAt(new Date(iso).toISOString().slice(0, 16)); setShowRemindPicker(false); }}
                  onClose={() => setShowRemindPicker(false)}
                  onClear={editNotifyAt ? () => { setEditNotifyAt(""); setShowRemindPicker(false); } : undefined}
                />
              )}
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => saveAndAction()} disabled={actionLoading}
              className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 disabled:opacity-50 transition-colors">
              Save
            </button>
            <button type="button" onClick={() => saveAndAction(dispatchTask, task.id)}
              disabled={actionLoading || (!editProject && !task.project_name)}
              className="px-2.5 py-1 rounded-lg text-xs font-medium bg-green-500/15 text-green-400 hover:bg-green-500/25 disabled:opacity-50 transition-colors">
              Dispatch
            </button>
            {task.status === "PLANNING" && (
              <button type="button" onClick={() => saveAndAction((id) => updateTaskV2(id, { status: "INBOX" }), task.id)}
                disabled={actionLoading}
                className="px-2.5 py-1 rounded-lg text-xs font-medium bg-elevated text-label hover:text-heading transition-colors">
                Back to Inbox
              </button>
            )}
            <button type="button" onClick={doDelete} disabled={actionLoading}
              className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
              Delete
            </button>
          </div>
        </>
      ) : (
        <>
          {/* ── READ-ONLY CONTENT for non-editable statuses ── */}

          {task.description && task.description !== task.title && (
            <div className="text-sm text-body whitespace-pre-wrap">{task.description}</div>
          )}

          {task.branch_name && (
            <div className="flex items-center gap-2">
              <svg className="w-3.5 h-3.5 text-purple-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
              </svg>
              <code className="text-xs text-dim font-mono truncate">{task.branch_name}</code>
            </div>
          )}

          {task.status === "EXECUTING" && task.agent_id && (
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-cyan-500 animate-pulse" />
              <span className="text-sm text-label">Agent executing</span>
              <button type="button" onClick={() => navigate(`/agents/${task.agent_id}`)}
                className="ml-auto text-xs text-cyan-400 hover:text-cyan-300">
                View agent &rarr;
              </button>
            </div>
          )}

          {task.status === "REVIEW" && (
            <div className="space-y-2">
              {task.agent_summary && (
                <div className="text-sm text-body bg-inset rounded-lg p-3 max-h-[300px] overflow-y-auto">
                  {renderMarkdown(task.agent_summary, task.project_name)}
                </div>
              )}
              {task.agent_id && (
                <button type="button" onClick={() => navigate(`/agents/${task.agent_id}`)}
                  className="inline-flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300">
                  View agent conversation &rarr;
                </button>
              )}
            </div>
          )}

          {task.status === "MERGING" && (
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-purple-500 animate-pulse" />
              <span className="text-sm text-label">Merging branch...</span>
            </div>
          )}

          {task.status === "CONFLICT" && task.error_message && (
            <pre className="text-xs text-body bg-inset rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{task.error_message}</pre>
          )}

          {task.status === "COMPLETE" && (
            <div className="space-y-2">
              {task.completed_at && (
                <span className="text-xs text-faint">Completed {relativeTime(task.completed_at)}</span>
              )}
              {task.agent_summary && (
                <div className="text-sm text-body">{renderMarkdown(task.agent_summary, task.project_name)}</div>
              )}
              {task.agent_id && (
                <button type="button" onClick={() => navigate(`/agents/${task.agent_id}`)}
                  className="inline-flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300">
                  View agent &rarr;
                </button>
              )}
            </div>
          )}

          {task.status === "CANCELLED" && task.agent_id && (
            <button type="button" onClick={() => navigate(`/agents/${task.agent_id}`)}
              className="inline-flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300">
              View agent &rarr;
            </button>
          )}

          {task.status === "REJECTED" && (
            <div className="space-y-2">
              {task.rejection_reason && <p className="text-sm text-body">{task.rejection_reason}</p>}
              {task.agent_id && (
                <button type="button" onClick={() => navigate(`/agents/${task.agent_id}`)}
                  className="inline-flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300">
                  View agent &rarr;
                </button>
              )}
            </div>
          )}

          {(task.status === "FAILED" || task.status === "TIMEOUT") && (
            <div className="space-y-2">
              {task.error_message && <p className="text-sm text-body">{task.error_message}</p>}
              {task.agent_id && (
                <button type="button" onClick={() => navigate(`/agents/${task.agent_id}`)}
                  className="inline-flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300">
                  View agent &rarr;
                </button>
              )}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1">
              {task.status === "PENDING" && (
                <button type="button" onClick={doDelete} disabled={actionLoading}
                  className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
                  Cancel
                </button>
              )}

              {task.status === "EXECUTING" && (
                <button type="button" onClick={() => { if (confirm("Cancel this task?")) doAction(cancelTask, task.id); }}
                  disabled={actionLoading}
                  className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
                  Cancel
                </button>
              )}

              {(task.status === "REVIEW" || task.status === "CONFLICT") && (
                <button type="button" onClick={doDelete} disabled={actionLoading}
                  className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
                  Delete
                </button>
              )}

              {(task.status === "REJECTED" || task.status === "FAILED" || task.status === "TIMEOUT") && (
                <>
                  <button type="button" onClick={() => doAction(dispatchTask, task.id)}
                    disabled={actionLoading || !task.project_name}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 disabled:opacity-50 transition-colors">
                    Retry
                  </button>
                  <button type="button" onClick={doDelete} disabled={actionLoading}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
                    Delete
                  </button>
                </>
              )}

              {(task.status === "COMPLETE" || task.status === "CANCELLED") && (
                <button type="button" onClick={doDelete} disabled={actionLoading}
                  className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
                  Delete
                </button>
              )}
            </div>
        </>
      )}
    </div>
  );
}
