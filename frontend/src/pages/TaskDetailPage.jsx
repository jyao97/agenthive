import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { fetchTaskV2, updateTaskV2, dispatchTask, cancelTask } from "../lib/api";
import { TASK_STATUS_COLORS, TASK_STATUS_TEXT_COLORS, projectBadgeColor, modelDisplayName, POLL_INTERVAL } from "../lib/constants";
import { relativeTime, renderMarkdown } from "../lib/formatters";
import { serverNow } from "../lib/serverTime";
import ProjectSelector from "../components/ProjectSelector";
import usePageVisible from "../hooks/usePageVisible";
import useWebSocket, { useWsEvent, registerViewingTasks, unregisterViewingTasks } from "../hooks/useWebSocket";
import { useToast } from "../contexts/ToastContext";

export default function TaskDetailPage({ theme, onToggleTheme }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [task, setTask] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editProject, setEditProject] = useState("");
  const [editNotifyAt, setEditNotifyAt] = useState("");
  const pollRef = useRef(null);
  const visible = usePageVisible();
  const { sendWsMessage } = useWebSocket();

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

  // Register viewing for notification suppression
  useEffect(() => { registerViewingTasks(); return () => unregisterViewingTasks(); }, []);

  useEffect(() => {
    if (!visible) return;
    load();
    if (task && TERMINAL_STATUSES.has(task.status)) return;
    pollRef.current = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(pollRef.current);
  }, [load, visible, task?.status]);

  const agentId = task?.agent_id;
  useEffect(() => {
    if (!agentId) return;
    sendWsMessage({ type: "viewing", agent_id: agentId });
    return () => sendWsMessage({ type: "viewing", agent_id: null, _unview: agentId });
  }, [agentId, sendWsMessage]);

  const loadRef = useRef(load);
  loadRef.current = load;
  useWsEvent((event) => {
    if (event.type !== "task_update") return;
    if (event.data?.task_id === id) loadRef.current();
  }, [id]);

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
    const origNotify = task.notify_at ? new Date(task.notify_at).toISOString().slice(0, 16) : "";
    if (editNotifyAt !== origNotify) updates.notify_at = editNotifyAt ? new Date(editNotifyAt).toISOString() : null;
    if (Object.keys(updates).length > 0) {
      await doAction(updateTaskV2, id, updates);
    }
    setEditMode(false);
  };

  const startEdit = () => {
    setEditTitle(task.title);
    setEditDesc(task.description || "");
    setEditProject(task.project_name || "");
    setEditNotifyAt(task.notify_at ? new Date(task.notify_at).toISOString().slice(0, 16) : "");
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

  // Elapsed time for running tasks
  const elapsed = task.started_at && !task.completed_at
    ? Math.floor((serverNow() - new Date(task.started_at).getTime()) / 1000)
    : task.elapsed_seconds;
  const fmtElapsed = (s) => {
    if (!s) return null;
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="shrink-0 bg-page border-b border-divider px-4 pt-4 pb-3 z-10 safe-area-pt">
        <div className="flex items-center gap-2 mb-1.5">
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
        <h1 className="text-lg font-bold text-heading leading-tight">{task.title}</h1>

        {/* Metadata row */}
        <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
          {task.project_name && (
            <span className={`text-[10px] font-medium rounded-full px-2 py-0.5 ${projColor}`}>
              {task.project_name}
            </span>
          )}
          {task.model && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-elevated text-dim">
              {modelDisplayName(task.model)}
            </span>
          )}
          {task.effort && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-elevated text-dim uppercase">
              {task.effort}
            </span>
          )}
          {task.priority === 1 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-medium">High Priority</span>
          )}
          {task.use_worktree && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400">Worktree</span>
          )}
          {task.sync_mode && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">Tmux</span>
          )}
          {task.attempt_number > 1 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500/15 text-orange-400">Attempt #{task.attempt_number}</span>
          )}
          <span className="text-[10px] text-faint">{relativeTime(task.created_at)}</span>
          {elapsed != null && (
            <span className="text-[10px] text-dim">{fmtElapsed(elapsed)}</span>
          )}
          {task.notify_at && (
            <span className="text-[10px] text-amber-400 flex items-center gap-0.5">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              {relativeTime(task.notify_at)}
            </span>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        <div className="max-w-2xl mx-auto w-full pb-20 p-4 space-y-3">
          {error && !task && (
            <div className="bg-red-950/40 border border-red-800 rounded-xl p-3">
              <p className="text-red-400 text-sm">{error}</p>
            </div>
          )}

          {/* INBOX/PLANNING: editable fields */}
          {canEdit && editMode ? (
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
              <div>
                <label className="block text-xs font-medium text-label mb-1">Title</label>
                <input
                  type="text"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading text-sm focus:border-cyan-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-label mb-1">Description</label>
                <textarea
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  rows={4}
                  className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading text-sm resize-none focus:border-cyan-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-label mb-1">Project</label>
                <ProjectSelector value={editProject} onChange={setEditProject} />
              </div>
              <div>
                <label className="block text-xs font-medium text-label mb-1">Remind At</label>
                <input
                  type="datetime-local"
                  value={editNotifyAt}
                  onChange={(e) => setEditNotifyAt(e.target.value)}
                  className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-heading text-sm focus:border-cyan-500 focus:outline-none"
                />
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
                <div className="rounded-xl bg-surface shadow-card p-3">
                  <div className="text-sm text-body whitespace-pre-wrap">{task.description}</div>
                </div>
              )}

              {/* Branch info */}
              {task.branch_name && (
                <div className="flex items-center gap-2 px-1">
                  <svg className="w-3.5 h-3.5 text-purple-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
                  </svg>
                  <code className="text-xs text-dim font-mono truncate">{task.branch_name}</code>
                </div>
              )}

              {/* Edit button for editable tasks */}
              {canEdit && (
                <button
                  type="button"
                  onClick={startEdit}
                  className="w-full rounded-xl bg-surface shadow-card p-3 flex items-center gap-2 text-sm text-label hover:text-heading transition-colors"
                >
                  <svg className="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                  </svg>
                  Edit task details
                </button>
              )}

              {/* No-project warning for INBOX */}
              {task.status === "INBOX" && !task.project_name && (
                <div className="rounded-xl bg-amber-500/10 border border-amber-500/20 px-3 py-2.5 flex items-start gap-2">
                  <svg className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.999L13.732 4.001c-.77-1.333-2.694-1.333-3.464 0L3.34 16.001C2.57 17.334 3.532 19 5.072 19z" />
                  </svg>
                  <p className="text-xs text-amber-400/80">Assign a project to enable planning and dispatch</p>
                </div>
              )}
            </>
          )}

          {/* EXECUTING: link to agent */}
          {task.status === "EXECUTING" && task.agent_id && (
            <div className="rounded-xl bg-surface shadow-card p-3">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-cyan-500 animate-pulse" />
                <span className="text-sm text-label">Agent executing...</span>
                <button
                  type="button"
                  onClick={() => navigate(`/agents/${task.agent_id}`)}
                  className="ml-auto text-xs text-cyan-400 hover:text-cyan-300 underline"
                >
                  View agent
                </button>
              </div>
            </div>
          )}

          {/* REVIEW: agent summary + actions */}
          {task.status === "REVIEW" && (
            <div className="rounded-xl bg-surface shadow-card p-3 space-y-3">
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
            <div className="rounded-xl bg-surface shadow-card p-3 flex items-center gap-3">
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
            <div className="rounded-xl bg-surface shadow-card p-3 space-y-2">
              <h3 className="text-sm font-medium text-red-400">Merge Conflict</h3>
              {task.error_message && (
                <pre className="text-xs text-body bg-inset rounded-lg p-3 overflow-x-auto">{task.error_message}</pre>
              )}
            </div>
          )}

          {/* COMPLETE */}
          {task.status === "COMPLETE" && (
            <div className="rounded-xl bg-surface shadow-card p-3 space-y-2">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-medium text-green-400">Completed</h3>
                {task.completed_at && (
                  <span className="text-[10px] text-faint ml-auto">{relativeTime(task.completed_at)}</span>
                )}
              </div>
              {task.agent_summary && (
                <div className="text-sm text-body">{renderMarkdown(task.agent_summary, task.project_name)}</div>
              )}
            </div>
          )}

          {/* REJECTED */}
          {task.status === "REJECTED" && (
            <div className="rounded-xl bg-surface shadow-card p-3 space-y-2">
              <h3 className="text-sm font-medium text-orange-400">Rejected</h3>
              {task.rejection_reason && (
                <p className="text-sm text-body">{task.rejection_reason}</p>
              )}
            </div>
          )}

          {/* FAILED / TIMEOUT */}
          {(task.status === "FAILED" || task.status === "TIMEOUT") && (
            <div className="rounded-xl bg-surface shadow-card p-3 space-y-2">
              <h3 className="text-sm font-medium text-red-400">{task.status === "TIMEOUT" ? "Timed Out" : "Failed"}</h3>
              {task.error_message && (
                <p className="text-sm text-body">{task.error_message}</p>
              )}
            </div>
          )}

          {/* Retry history */}
          {task.attempt_number > 1 && task.retry_context && (
            <details className="rounded-xl bg-surface shadow-card p-3">
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
        {/* INBOX → Dispatch + Delete */}
        {task.status === "INBOX" && (
          <>
            <button
              type="button"
              onClick={() => doAction(dispatchTask, id)}
              disabled={actionLoading || !task.project_name}
              title={!task.project_name ? "Set a project before dispatching" : "Dispatch directly"}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-medium hover:bg-cyan-500 disabled:opacity-50 transition-colors"
            >
              Dispatch
            </button>
            <button
              type="button"
              onClick={() => { if (confirm("Delete this task?")) doDelete(); }}
              disabled={actionLoading}
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600/80 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
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
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600/80 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
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

        {/* REVIEW / CONFLICT → Delete only */}
        {(task.status === "REVIEW" || task.status === "CONFLICT") && (
          <button
            type="button"
            onClick={() => { if (confirm("Delete this task?")) doDelete(); }}
            disabled={actionLoading}
            className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600/80 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
          >
            Delete
          </button>
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
              className="whitespace-nowrap px-4 py-2 rounded-lg bg-red-600/80 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 transition-colors"
            >
              Delete
            </button>
          </>
        )}
      </div>
    </div>
  );
}
