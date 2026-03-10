import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { updateTaskV2, planTask, dispatchTask, approveTask, rejectTask, cancelTask, tryTaskChanges, revertTaskChanges, verifyTask } from "../../lib/api";
import { relativeTime, renderMarkdown } from "../../lib/formatters";
import ProjectSelector from "../ProjectSelector";
import { useToast } from "../../contexts/ToastContext";

export default function TaskExpandedContent({ task, onRefresh, onCollapse }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [actionLoading, setActionLoading] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");

  const canEdit = task.status === "INBOX" || task.status === "PLANNING";

  // Editable fields — initialized from task, always shown for INBOX/PLANNING
  const [editTitle, setEditTitle] = useState(task.title);
  const [editDesc, setEditDesc] = useState(task.description || "");
  const [editProject, setEditProject] = useState(task.project_name || "");
  const [editNotifyAt, setEditNotifyAt] = useState(
    task.notify_at ? new Date(task.notify_at).toISOString().slice(0, 16) : ""
  );

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
      onRefresh?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setActionLoading(false);
    }
  };

  // Parse verify artifacts
  let verifyStatus = null, verifyResult = null;
  if (task.review_artifacts) {
    try {
      const arts = JSON.parse(task.review_artifacts);
      verifyStatus = arts.verify_status;
      verifyResult = arts.verify_result;
    } catch {}
  }

  return (
    <div className="border-t border-divider mx-4 pb-4 pt-3 space-y-3" onClick={(e) => e.stopPropagation()}>
      {/* EDITABLE FORM — always visible for INBOX/PLANNING */}
      {canEdit ? (
        <>
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-label mb-1">Title</label>
              <input type="text" value={editTitle} onChange={(e) => setEditTitle(e.target.value)}
                className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading text-sm focus:border-cyan-500 focus:outline-none" />
            </div>
            <div>
              <label className="block text-xs font-medium text-label mb-1">Description</label>
              <textarea value={editDesc} onChange={(e) => setEditDesc(e.target.value)} rows={3}
                className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading text-sm resize-none focus:border-cyan-500 focus:outline-none" />
            </div>
            <div>
              <label className="block text-xs font-medium text-label mb-1">Project</label>
              <ProjectSelector value={editProject} onChange={setEditProject} />
            </div>
            <div>
              <label className="block text-xs font-medium text-label mb-1">Remind At</label>
              <input type="datetime-local" value={editNotifyAt} onChange={(e) => setEditNotifyAt(e.target.value)}
                className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading text-sm focus:border-cyan-500 focus:outline-none" />
            </div>
          </div>

          {/* Action buttons — Save + Plan/Dispatch/Delete */}
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button type="button" onClick={() => saveAndAction()} disabled={actionLoading}
              className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 disabled:opacity-50 transition-colors">
              Save
            </button>
            {task.status === "INBOX" && (
              <button type="button" onClick={() => saveAndAction(planTask, task.id)}
                disabled={actionLoading || (!editProject && !task.project_name)}
                className="px-2.5 py-1 rounded-lg text-xs font-medium bg-violet-500/15 text-violet-400 hover:bg-violet-500/25 disabled:opacity-50 transition-colors">
                Plan
              </button>
            )}
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
          {/* Read-only content for non-editable statuses */}

          {/* Full description — skip if same as title */}
          {task.description && task.description !== task.title && (
            <div className="text-sm text-body whitespace-pre-wrap">{task.description}</div>
          )}

          {/* Branch */}
          {task.branch_name && (
            <div className="flex items-center gap-2">
              <svg className="w-3.5 h-3.5 text-purple-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
              </svg>
              <code className="text-xs text-dim font-mono truncate">{task.branch_name}</code>
            </div>
          )}

          {/* EXECUTING: agent link */}
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

          {/* REVIEW: full summary + verify result */}
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
              {verifyStatus && verifyStatus !== "running" && verifyResult && (
                <div className="text-xs bg-inset rounded-lg p-3 max-h-[200px] overflow-y-auto">
                  <pre className="whitespace-pre-wrap font-mono text-body">{verifyResult}</pre>
                </div>
              )}
            </div>
          )}

          {/* MERGING */}
          {task.status === "MERGING" && (
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-purple-500 animate-pulse" />
              <span className="text-sm text-label">Merging branch...</span>
            </div>
          )}

          {/* CONFLICT */}
          {task.status === "CONFLICT" && task.error_message && (
            <pre className="text-xs text-body bg-inset rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{task.error_message}</pre>
          )}

          {/* COMPLETE */}
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

          {/* CANCELLED */}
          {task.status === "CANCELLED" && task.agent_id && (
            <button type="button" onClick={() => navigate(`/agents/${task.agent_id}`)}
              className="inline-flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300">
              View agent &rarr;
            </button>
          )}

          {/* REJECTED */}
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

          {/* FAILED/TIMEOUT */}
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

          {/* REJECT REASON INPUT */}
          {rejectOpen && (
            <div className="space-y-2">
              <textarea value={rejectReason} onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Why are you rejecting this?" rows={2} autoFocus
                className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-sm text-heading placeholder-hint resize-none focus:border-red-500 focus:outline-none" />
              <div className="flex gap-2">
                <button type="button" disabled={!rejectReason.trim() || actionLoading}
                  onClick={() => { doAction(rejectTask, task.id, rejectReason); setRejectOpen(false); setRejectReason(""); }}
                  className="px-3 py-1 rounded-lg text-xs font-medium bg-red-500 text-white hover:bg-red-400 disabled:opacity-50 transition-colors">
                  Confirm Reject
                </button>
                <button type="button" onClick={() => { setRejectOpen(false); setRejectReason(""); }}
                  className="px-3 py-1 rounded-lg text-xs font-medium bg-elevated text-label hover:text-heading transition-colors">
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* ACTION BUTTONS for non-editable statuses */}
          {!rejectOpen && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              {/* PENDING */}
              {task.status === "PENDING" && (
                <button type="button" onClick={doDelete} disabled={actionLoading}
                  className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
                  Cancel
                </button>
              )}

              {/* EXECUTING */}
              {task.status === "EXECUTING" && (
                <button type="button" onClick={() => { if (confirm("Cancel this task?")) doAction(cancelTask, task.id); }}
                  disabled={actionLoading}
                  className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
                  Cancel
                </button>
              )}

              {/* REVIEW */}
              {task.status === "REVIEW" && (
                <>
                  {verifyStatus === "running" ? (
                    <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 flex items-center gap-1">
                      <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="31.4" strokeLinecap="round" />
                      </svg>
                      Verifying
                    </span>
                  ) : (
                    <button type="button" onClick={() => doAction(verifyTask, task.id)} disabled={actionLoading}
                      className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 transition-colors">
                      {verifyStatus === "error" ? "Retry Verify" : "Verify"}
                    </button>
                  )}
                  {task.branch_name && !task.try_base_commit && (
                    <button type="button" onClick={() => doAction(tryTaskChanges, task.id)} disabled={actionLoading}
                      className="px-2.5 py-1 rounded-lg text-xs font-medium bg-indigo-500/15 text-indigo-400 hover:bg-indigo-500/25 transition-colors">
                      Try
                    </button>
                  )}
                  {task.try_base_commit && (
                    <button type="button" onClick={() => doAction(revertTaskChanges, task.id)} disabled={actionLoading}
                      className="px-2.5 py-1 rounded-lg text-xs font-medium bg-orange-500/15 text-orange-400 hover:bg-orange-500/25 transition-colors">
                      {task.branch_name ? "Revert" : "Rollback"}
                    </button>
                  )}
                  <button type="button" onClick={() => doAction(approveTask, task.id)} disabled={actionLoading}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-green-500/15 text-green-400 hover:bg-green-500/25 transition-colors">
                    Approve
                  </button>
                  <button type="button" onClick={() => setRejectOpen(true)} disabled={actionLoading}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-amber-500/15 text-amber-400 hover:bg-amber-500/25 transition-colors">
                    Reject
                  </button>
                  <button type="button" onClick={doDelete} disabled={actionLoading}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
                    Delete
                  </button>
                </>
              )}

              {/* CONFLICT */}
              {task.status === "CONFLICT" && (
                <>
                  <button type="button" onClick={() => doAction(approveTask, task.id)} disabled={actionLoading}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-purple-500/15 text-purple-400 hover:bg-purple-500/25 transition-colors">
                    Retry Merge
                  </button>
                  <button type="button" onClick={doDelete} disabled={actionLoading}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors">
                    Delete
                  </button>
                </>
              )}

              {/* REJECTED/FAILED/TIMEOUT */}
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
            </div>
          )}
        </>
      )}
    </div>
  );
}
