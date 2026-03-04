import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { fetchTaskV2, updateTaskV2, planTask, dispatchTask, approveTask, rejectTask, cancelTask, tryTaskChanges, revertTaskChanges, verifyTask } from "../lib/api";
import { TASK_STATUS_COLORS, TASK_STATUS_TEXT_COLORS, projectBadgeColor, POLL_INTERVAL } from "../lib/constants";
import { relativeTime, renderMarkdown } from "../lib/formatters";
import ProjectSelector from "../components/ProjectSelector";
import usePageVisible from "../hooks/usePageVisible";
import useWebSocket from "../hooks/useWebSocket";
import { useToast } from "../contexts/ToastContext";

export default function TaskDetailPage({ theme, onToggleTheme }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [task, setTask] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editProject, setEditProject] = useState("");
  const pollRef = useRef(null);
  const visible = usePageVisible();
  const { lastEvent, sendWsMessage } = useWebSocket();

  const load = useCallback(async () => {
    try {
      const data = await fetchTaskV2(id);
      setTask(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [id]);

  const TERMINAL_STATUSES = new Set(["COMPLETE", "CANCELLED", "FAILED", "TIMEOUT", "REJECTED"]);

  useEffect(() => {
    if (!visible) return;
    load();
    // Don't poll terminal-status tasks — they won't change
    if (task && TERMINAL_STATUSES.has(task.status)) return;
    pollRef.current = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(pollRef.current);
  }, [load, visible, task?.status]);

  // Suppress notifications for the agent executing this task
  const agentId = task?.agent_id;
  useEffect(() => {
    if (!agentId) return;
    sendWsMessage({ type: "viewing", agent_id: agentId });
    return () => sendWsMessage({ type: "viewing", agent_id: null });
  }, [agentId, sendWsMessage]);

  // Refresh on WebSocket task_update for this task
  useEffect(() => {
    if (!lastEvent || lastEvent.type !== "task_update") return;
    if (lastEvent.data?.task_id === id) load();
  }, [lastEvent, id, load]);

  const toast = useToast();
  const doAction = async (fn, ...args) => {
    setActionLoading(true);
    try {
      await fn(...args);
      await load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setActionLoading(false);
    }
  };

  const doDelete = async () => {
    setActionLoading(true);
    try {
      await cancelTask(id);
      navigate("/tasks", { replace: true });
    } catch (err) {
      toast.error(err.message);
      setActionLoading(false);
    }
  };

  const handleSaveEdit = async () => {
    const updates = {};
    if (editTitle && editTitle !== task.title) updates.title = editTitle;
    if (editDesc !== (task.description || "")) updates.description = editDesc;
    if (editProject && editProject !== task.project_name) updates.project_name = editProject;
    if (Object.keys(updates).length > 0) {
      await doAction(updateTaskV2, id, updates);
    }
    setEditMode(false);
  };

  const startEdit = () => {
    setEditTitle(task.title);
    setEditDesc(task.description || "");
    setEditProject(task.project_name || "");
    setEditMode(true);
  };

  if (loading && !task) {
    return (
      <div className="h-full flex items-center justify-center">
        <span className="text-dim text-sm animate-pulse">Loading task...</span>
      </div>
    );
  }

  if (error && !task) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-red-400 text-sm">{error}</p>
        <button type="button" onClick={() => navigate("/tasks")} className="text-xs text-label underline">
          Back to tasks
        </button>
      </div>
    );
  }

  if (!task) return null;

  const dotColor = TASK_STATUS_COLORS[task.status] || "bg-gray-500";
  const textColor = TASK_STATUS_TEXT_COLORS[task.status] || "text-dim";
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";
  const canEdit = task.status === "INBOX" || task.status === "PLANNING";

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="shrink-0 bg-page border-b border-divider px-4 pt-4 pb-3 z-10 safe-area-pt">
        <div className="flex items-center gap-2 mb-2">
          <button type="button" onClick={() => navigate("/tasks")} className="flex items-center gap-1 text-sm text-label hover:text-heading">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
            Tasks
          </button>
          <div className="ml-auto flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${dotColor}`} />
            <span className={`text-xs font-medium ${textColor}`}>{task.status}</span>
          </div>
        </div>
        <h1 className="text-lg font-bold text-heading">{task.title}</h1>
        <div className="flex items-center gap-2 mt-1">
          {task.project_name && (
            <span className={`text-xs font-medium rounded-full px-2 py-0.5 ${projColor}`}>
              {task.project_name}
            </span>
          )}
          {task.attempt_number > 1 && (
            <span className="text-xs text-orange-400 font-medium">Attempt #{task.attempt_number}</span>
          )}
          <span className="text-xs text-faint">{relativeTime(task.created_at)}</span>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        <div className="max-w-2xl mx-auto w-full pb-20 p-4 space-y-4">
          {error && !task && (
            <div className="bg-red-950/40 border border-red-800 rounded-xl p-3">
              <p className="text-red-400 text-sm">{error}</p>
            </div>
          )}

          {/* INBOX: editable fields */}
          {canEdit && editMode ? (
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
              <div>
                <label className="block text-sm font-medium text-label mb-1">Title</label>
                <input
                  type="text"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading text-sm focus:border-cyan-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-label mb-1">Description</label>
                <textarea
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  rows={4}
                  className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading text-sm resize-none focus:border-cyan-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-label mb-1">Project</label>
                <ProjectSelector value={editProject} onChange={setEditProject} />
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={handleSaveEdit}
                  disabled={actionLoading}
                  className="px-4 py-2 rounded-lg bg-cyan-500 text-white text-sm font-medium hover:bg-cyan-400 transition-colors"
                >
                  Save
                </button>
                <button
                  type="button"
                  onClick={() => setEditMode(false)}
                  className="px-4 py-2 rounded-lg bg-elevated text-label text-sm hover:text-heading transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              {/* Description */}
              {task.description && (
                <div className="rounded-xl bg-surface shadow-card p-4">
                  <h3 className="text-sm font-medium text-label mb-2">Description</h3>
                  <div className="text-sm text-body whitespace-pre-wrap">{task.description}</div>
                </div>
              )}

              {/* Task info summary */}
              {!canEdit && !task.description && (
                <div className="rounded-xl bg-surface shadow-card p-4">
                  <div className="flex items-center gap-3 text-sm text-dim">
                    {task.model && <span>Model: {task.model.replace(/^claude-/, "").replace(/-\d{8}$/, "")}</span>}
                    {task.effort && <span>Effort: {task.effort}</span>}
                    {task.priority === 1 && <span className="text-amber-400">High Priority</span>}
                  </div>
                </div>
              )}

              {canEdit && (
                <button
                  type="button"
                  onClick={startEdit}
                  className="text-xs text-cyan-400 hover:text-cyan-300"
                >
                  Edit task details
                </button>
              )}
            </>
          )}

          {/* EXECUTING: link to agent */}
          {task.status === "EXECUTING" && task.agent_id && (
            <div className="rounded-xl bg-surface shadow-card p-4">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-cyan-500 animate-pulse" />
                <span className="text-sm text-label">Agent executing...</span>
                <button
                  type="button"
                  onClick={() => navigate(`/agents/${task.agent_id}`)}
                  className="ml-auto text-xs text-cyan-400 hover:text-cyan-300 underline"
                >
                  View agent chat
                </button>
              </div>
            </div>
          )}

          {/* REVIEW: agent summary + actions */}
          {task.status === "REVIEW" && (
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
              <h3 className="text-sm font-medium text-amber-400">Ready for Review</h3>
              {task.agent_summary ? (
                <div className="text-sm text-body bg-inset rounded-lg p-3 max-h-[400px] overflow-y-auto">
                  {renderMarkdown(task.agent_summary, task.project_name)}
                </div>
              ) : (
                <p className="text-sm text-dim">Agent completed — review the conversation below.</p>
              )}
              {task.agent_id && (
                <button
                  type="button"
                  onClick={() => navigate(`/agents/${task.agent_id}`)}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-cyan-500/10 text-sm text-cyan-400 hover:bg-cyan-500/20 transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                  </svg>
                  View agent conversation
                </button>
              )}
              {(() => {
                const arts = task.review_artifacts ? (() => { try { return JSON.parse(task.review_artifacts); } catch { return {}; } })() : {};
                if (!arts.verify_status || arts.verify_status === "running") return null;
                const colors = { pass: "text-green-400 bg-green-500/10", fail: "text-red-400 bg-red-500/10", warn: "text-amber-400 bg-amber-500/10", done: "text-cyan-400 bg-cyan-500/10", error: "text-red-400 bg-red-500/10" };
                const labels = { pass: "Verification Passed", fail: "Verification Failed", warn: "Verification Warning", done: "Verification Complete", error: "Verification Error" };
                return (
                  <div className="space-y-2">
                    <h4 className={`text-xs font-medium ${(colors[arts.verify_status] || "text-dim").split(" ")[0]}`}>{labels[arts.verify_status] || "Verification"}</h4>
                    {arts.verify_result && (
                      <div className={`text-xs rounded-lg p-3 max-h-[300px] overflow-y-auto ${colors[arts.verify_status] || "bg-inset text-body"}`}>
                        <pre className="whitespace-pre-wrap font-mono">{arts.verify_result}</pre>
                      </div>
                    )}
                    {arts.verify_agent_id && (
                      <button
                        type="button"
                        onClick={() => navigate(`/agents/${arts.verify_agent_id}`)}
                        className="inline-flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300"
                      >
                        View verification agent
                      </button>
                    )}
                  </div>
                );
              })()}
            </div>
          )}

          {/* MERGING */}
          {task.status === "MERGING" && (
            <div className="rounded-xl bg-surface shadow-card p-4 flex items-center gap-3">
              <span className="w-2 h-2 rounded-full bg-purple-500 animate-pulse" />
              <span className="text-sm text-label">Merging branch...</span>
              {task.merge_agent_id && (
                <a
                  href={`/agents/${task.merge_agent_id}`}
                  className="text-xs text-accent hover:underline ml-auto"
                  onClick={(e) => { e.preventDefault(); navigate(`/agents/${task.merge_agent_id}`); }}
                >
                  View merge agent
                </a>
              )}
            </div>
          )}

          {/* CONFLICT */}
          {task.status === "CONFLICT" && (
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-2">
              <h3 className="text-sm font-medium text-red-400">Merge Conflict</h3>
              {task.error_message && (
                <pre className="text-xs text-body bg-inset rounded-lg p-3 overflow-x-auto">{task.error_message}</pre>
              )}
            </div>
          )}

          {/* COMPLETE */}
          {task.status === "COMPLETE" && (
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-2">
              <h3 className="text-sm font-medium text-green-400">Completed</h3>
              {task.agent_summary && (
                <div className="text-sm text-body">{renderMarkdown(task.agent_summary, task.project_name)}</div>
              )}
              {task.completed_at && (
                <span className="text-xs text-faint">Completed {relativeTime(task.completed_at)}</span>
              )}
            </div>
          )}

          {/* REJECTED */}
          {task.status === "REJECTED" && (
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-2">
              <h3 className="text-sm font-medium text-orange-400">Rejected</h3>
              {task.rejection_reason && (
                <p className="text-sm text-body">{task.rejection_reason}</p>
              )}
            </div>
          )}

          {/* FAILED / TIMEOUT */}
          {(task.status === "FAILED" || task.status === "TIMEOUT") && (
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-2">
              <h3 className="text-sm font-medium text-red-400">{task.status === "TIMEOUT" ? "Timed Out" : "Failed"}</h3>
              {task.error_message && (
                <p className="text-sm text-body">{task.error_message}</p>
              )}
            </div>
          )}

          {/* Reject reason input */}
          {rejectOpen && (
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
              <label className="block text-sm font-medium text-label">Rejection Reason</label>
              <textarea
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                rows={3}
                placeholder="What needs to be changed?"
                className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading text-sm resize-none focus:border-red-500 focus:outline-none"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => { doAction(rejectTask, id, rejectReason); setRejectOpen(false); setRejectReason(""); }}
                  disabled={!rejectReason.trim() || actionLoading}
                  className="px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
                >
                  Confirm Reject
                </button>
                <button
                  type="button"
                  onClick={() => setRejectOpen(false)}
                  className="px-4 py-2 rounded-lg bg-elevated text-label text-sm hover:text-heading transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Retry history */}
          {task.attempt_number > 1 && task.retry_context && (
            <details className="rounded-xl bg-surface shadow-card p-4">
              <summary className="text-sm font-medium text-label cursor-pointer">
                Previous attempt context
              </summary>
              <pre className="mt-2 text-xs text-body bg-inset rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">
                {task.retry_context}
              </pre>
            </details>
          )}
        </div>
      </div>

      {/* Action bar */}
      <div className="shrink-0 border-t border-divider bg-page px-4 py-3 safe-area-pb flex flex-wrap gap-2 justify-center">
        {/* INBOX → Plan + Delete */}
        {task.status === "INBOX" && (
          <>
            <button
              type="button"
              onClick={() => doAction(planTask, id)}
              disabled={actionLoading}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium hover:bg-violet-500 disabled:opacity-50 transition-colors"
            >
              Plan
            </button>
            <button
              type="button"
              onClick={() => { if (confirm("Delete this task?")) doDelete(); }}
              disabled={actionLoading}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
            >
              Delete
            </button>
          </>
        )}

        {/* PLANNING → Dispatch + Back to Inbox + Delete */}
        {task.status === "PLANNING" && (
          <>
            <button
              type="button"
              onClick={() => doAction(dispatchTask, id)}
              disabled={actionLoading || !task.project_name}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-medium hover:bg-cyan-500 disabled:opacity-50 transition-colors"
            >
              Dispatch
            </button>
            <button
              type="button"
              onClick={() => doAction((taskId) => updateTaskV2(taskId, { status: "INBOX" }), id)}
              disabled={actionLoading}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-elevated text-label text-sm font-medium hover:text-heading disabled:opacity-50 transition-colors"
            >
              Back to Inbox
            </button>
            <button
              type="button"
              onClick={() => { if (confirm("Delete this task?")) doDelete(); }}
              disabled={actionLoading}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
            >
              Delete
            </button>
          </>
        )}

        {/* PENDING → Cancel */}
        {task.status === "PENDING" && (
          <button
            type="button"
            onClick={() => { if (confirm("Cancel this task?")) doDelete(); }}
            disabled={actionLoading}
            className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
        )}

        {/* EXECUTING → Cancel */}
        {task.status === "EXECUTING" && (
          <button
            type="button"
            onClick={() => { if (confirm("Cancel this task?")) doDelete(); }}
            disabled={actionLoading}
            className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
        )}

        {/* REVIEW → Verify + Try/Revert + Approve + Reject + Delete */}
        {task.status === "REVIEW" && !rejectOpen && (() => {
          const artifacts = task.review_artifacts ? (() => { try { return JSON.parse(task.review_artifacts); } catch { return {}; } })() : {};
          const vs = artifacts.verify_status;
          return (
            <>
              {vs === "running" ? (
                <span className="whitespace-nowrap px-4 py-2 rounded-lg bg-cyan-600/20 text-cyan-400 text-sm font-medium flex items-center gap-1.5">
                  <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="31.4" strokeLinecap="round" /></svg>
                  Verifying...
                </span>
              ) : vs === "pass" ? (
                <span className="whitespace-nowrap px-4 py-2 rounded-lg bg-green-600/20 text-green-400 text-sm font-medium">Verified</span>
              ) : vs === "fail" ? (
                <span className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600/20 text-red-400 text-sm font-medium">Verify Failed</span>
              ) : vs === "warn" ? (
                <span className="whitespace-nowrap px-4 py-2 rounded-lg bg-amber-600/20 text-amber-400 text-sm font-medium">Verify Warning</span>
              ) : vs === "error" ? (
                <button
                  type="button"
                  onClick={() => doAction(verifyTask, id)}
                  disabled={actionLoading}
                  className="whitespace-nowrap px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-medium hover:bg-cyan-500 transition-colors"
                >
                  Retry Verify
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => doAction(verifyTask, id)}
                  disabled={actionLoading}
                  className="whitespace-nowrap px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-medium hover:bg-cyan-500 transition-colors"
                >
                  Verify
                </button>
              )}
              {task.branch_name && !task.try_base_commit && (
                <button
                  type="button"
                  onClick={() => doAction(tryTaskChanges, id)}
                  disabled={actionLoading}
                  className="whitespace-nowrap px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-500 transition-colors"
                >
                  Try
                </button>
              )}
              {task.try_base_commit && (
                <button
                  type="button"
                  onClick={() => doAction(revertTaskChanges, id)}
                  disabled={actionLoading}
                  className="whitespace-nowrap px-4 py-2 rounded-lg bg-orange-600 text-white text-sm font-medium hover:bg-orange-500 transition-colors"
                >
                  {task.branch_name ? "Revert" : "Rollback"}
                </button>
              )}
              <button
                type="button"
                onClick={() => doAction(approveTask, id)}
                disabled={actionLoading}
                className="whitespace-nowrap px-4 py-2 rounded-lg bg-green-600 text-white text-sm font-medium hover:bg-green-500 transition-colors"
              >
                Approve & Merge
              </button>
              <button
                type="button"
                onClick={() => setRejectOpen(true)}
                disabled={actionLoading}
                className="whitespace-nowrap px-4 py-2 rounded-lg bg-amber-600 text-white text-sm font-medium hover:bg-amber-500 transition-colors"
              >
                Reject
              </button>
              <button
                type="button"
                onClick={() => { if (confirm("Delete this task?")) doDelete(); }}
                disabled={actionLoading}
                className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
              >
                Delete
              </button>
            </>
          );
        })()}

        {/* CONFLICT → Retry + Delete */}
        {task.status === "CONFLICT" && (
          <>
            <button
              type="button"
              onClick={() => doAction(approveTask, id)}
              disabled={actionLoading}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-medium hover:bg-cyan-500 disabled:opacity-50 transition-colors"
            >
              Retry Merge
            </button>
            <button
              type="button"
              onClick={() => { if (confirm("Delete this task?")) doDelete(); }}
              disabled={actionLoading}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
            >
              Delete
            </button>
          </>
        )}

        {/* REJECTED / FAILED / TIMEOUT → Retry + Delete */}
        {(task.status === "REJECTED" || task.status === "FAILED" || task.status === "TIMEOUT") && (
          <>
            <button
              type="button"
              onClick={() => doAction(dispatchTask, id)}
              disabled={actionLoading || !task.project_name}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-medium hover:bg-cyan-500 disabled:opacity-50 transition-colors"
            >
              Retry
            </button>
            <button
              type="button"
              onClick={() => { if (confirm("Delete this task?")) doDelete(); }}
              disabled={actionLoading}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
            >
              Delete
            </button>
          </>
        )}
      </div>
    </div>
  );
}
