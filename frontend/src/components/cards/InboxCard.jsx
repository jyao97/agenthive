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
      className="w-full text-left rounded-xl bg-surface/60 shadow-card px-3.5 py-2.5 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      {/* Row 1: title + time */}
      <div className="flex items-start gap-2">
        <span className="w-1.5 h-1.5 rounded-full bg-blue-500 mt-[7px] shrink-0" />
        <p className="text-sm font-semibold text-heading truncate flex-1 min-w-0">{task.title}</p>
        <span className="text-[10px] text-faint shrink-0 mt-0.5">{relativeTime(task.created_at)}</span>
      </div>

      {/* Row 2: description preview */}
      {task.description && task.description !== task.title && (
        <p className="text-xs text-dim truncate mt-0.5 ml-[14px]">{task.description.slice(0, 120)}</p>
      )}

      {/* Row 3: badges + actions */}
      <div className="flex items-center gap-1.5 mt-1.5 ml-[14px]">
        {task.project_name && (
          <span className={`text-[10px] font-medium rounded-full px-1.5 py-px ${projColor}`}>
            {task.project_name}
          </span>
        )}
        {task.model && (
          <span className="text-[10px] px-1.5 py-px rounded bg-elevated text-dim">
            {modelDisplayName(task.model)}
          </span>
        )}
        {task.effort && (
          <span className="text-[10px] px-1 py-px rounded bg-elevated text-dim uppercase">
            {task.effort[0]}
          </span>
        )}
        {task.priority === 1 && (
          <span className="text-[10px] px-1.5 py-px rounded bg-amber-500/15 text-amber-400 font-medium">!</span>
        )}
        {task.notify_at && (
          <span className="text-[10px] text-amber-400 flex items-center gap-0.5">
            <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            {relativeTime(task.notify_at)}
          </span>
        )}
        <div className="flex items-center gap-1 ml-auto shrink-0">
          <button
            type="button"
            disabled={loading}
            onClick={(e) => { e.stopPropagation(); if (confirm("Delete this task?")) onDelete?.(task); }}
            className="w-7 h-7 rounded-lg flex items-center justify-center text-red-400/60 hover:text-red-400 hover:bg-red-500/10 disabled:opacity-50 transition-colors"
            title="Delete"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onEdit?.(task); }}
            className="w-7 h-7 rounded-lg flex items-center justify-center text-dim hover:text-heading hover:bg-elevated transition-colors"
            title="Edit"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
          </button>
          {task.project_name && (
            <button
              type="button"
              disabled={loading}
              onClick={(e) => { e.stopPropagation(); onDispatch?.(task); }}
              className="px-2 py-1 rounded-lg text-[10px] font-semibold bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 disabled:opacity-50 transition-colors"
            >
              Dispatch
            </button>
          )}
        </div>
      </div>
    </button>
  );
});
