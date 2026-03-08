import { useNavigate } from "react-router-dom";
import useAsyncHandler from "../../hooks/useAsyncHandler";
import ErrorAlert from "../../components/ErrorAlert";
import { dispatchTask, cancelTask, updateTaskV2 } from "../../lib/api";
import { projectBadgeColor, modelDisplayName } from "../../lib/constants";
import { relativeTime } from "../../lib/formatters";

function PlanningCard({ task, onDispatch, onBack, onDelete, loading }) {
  const navigate = useNavigate();
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";

  return (
    <button
      type="button"
      onClick={() => navigate(`/tasks/${task.id}`)}
      className="w-full text-left rounded-xl bg-surface/60 shadow-card p-4 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 hover:ring-1 hover:ring-ring-hover"
    >
      <div className="flex flex-wrap items-center gap-2 mb-1.5">
        <span className="w-2 h-2 rounded-full bg-violet-500" />
        {task.project_name && (
          <span className={`text-xs font-medium rounded-full px-2 py-0.5 ${projColor}`}>
            {task.project_name}
          </span>
        )}
        <span className="ml-auto text-xs text-faint">{relativeTime(task.created_at)}</span>
      </div>

      <p className="text-sm font-semibold text-heading truncate">{task.title}</p>

      {task.notify_at && (
        <p className="text-[10px] text-amber-400 mt-1">Remind {relativeTime(task.notify_at)}</p>
      )}

      <div className="flex items-center gap-2 mt-2">
        {task.model && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-elevated text-dim">
            {modelDisplayName(task.model)}
          </span>
        )}
        {task.effort && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-elevated text-dim uppercase">
            {task.effort[0]}
          </span>
        )}
        <div className="flex items-center gap-1.5 ml-auto">
          <button
            type="button"
            disabled={loading}
            onClick={(e) => { e.stopPropagation(); if (confirm("Delete this task?")) onDelete?.(task); }}
            className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 disabled:opacity-50 transition-colors"
          >
            Delete
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={(e) => { e.stopPropagation(); onBack?.(task); }}
            className="px-2.5 py-1 rounded-lg text-xs font-medium bg-elevated text-label hover:text-heading disabled:opacity-50 transition-colors"
          >
            Inbox
          </button>
          {task.project_name && (
            <button
              type="button"
              disabled={loading}
              onClick={(e) => { e.stopPropagation(); onDispatch?.(task); }}
              className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 disabled:opacity-50 transition-colors"
            >
              Dispatch
            </button>
          )}
        </div>
      </div>
    </button>
  );
}

export default function PlanningView({ tasks, loading, onRefresh }) {
  const { loadingIds, error, setError, handle } = useAsyncHandler();

  const sorted = [...tasks].sort((a, b) => {
    if (b.priority !== a.priority) return b.priority - a.priority;
    return new Date(a.created_at) - new Date(b.created_at);
  });

  if (!loading && sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-faint">
        <svg className="w-10 h-10 mb-2" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25z" />
        </svg>
        <p className="text-sm font-medium">No tasks in planning</p>
        <p className="text-xs mt-1">Move tasks from Inbox to start planning</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <ErrorAlert error={error} onDismiss={() => setError(null)} />
      {sorted.map((task) => (
        <PlanningCard
          key={task.id}
          task={task}
          onDispatch={(t) => handle(t.id, () => dispatchTask(t.id).then(() => onRefresh?.()), "Dispatch failed")}
          onBack={(t) => handle(t.id, () => updateTaskV2(t.id, { status: "INBOX" }).then(() => onRefresh?.()), "Move to inbox failed")}
          onDelete={(t) => handle(t.id, () => cancelTask(t.id).then(() => onRefresh?.()), "Delete failed")}
          loading={loadingIds.has(task.id)}
        />
      ))}
    </div>
  );
}
