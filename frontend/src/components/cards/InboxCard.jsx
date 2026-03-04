import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { projectBadgeColor, modelDisplayName } from "../../lib/constants";
import { relativeTime } from "../../lib/formatters";

export default memo(function InboxCard({ task, onDispatch, onEdit, onDelete, loading }) {
  const navigate = useNavigate();
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";

  return (
    <button
      type="button"
      onClick={() => navigate(`/tasks/${task.id}`)}
      className="w-full text-left rounded-xl bg-surface/60 shadow-card p-4 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      <div className="flex flex-wrap items-center gap-2 mb-1.5">
        <span className="w-2 h-2 rounded-full bg-blue-500" />
        {task.project_name && (
          <span className={`text-xs font-medium rounded-full px-2 py-0.5 ${projColor}`}>
            {task.project_name}
          </span>
        )}
        <span className="ml-auto text-xs text-faint">{relativeTime(task.created_at)}</span>
      </div>

      <p className="text-sm font-semibold text-heading truncate">{task.title}</p>

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
            onClick={(e) => { e.stopPropagation(); onEdit?.(task); }}
            className="px-2.5 py-1 rounded-lg text-xs font-medium bg-elevated text-label hover:text-heading transition-colors"
          >
            Edit
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
});
