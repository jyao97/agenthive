import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { projectBadgeColor, modelDisplayName } from "../../lib/constants";
import { relativeTime } from "../../lib/formatters";

export default memo(function InboxCard({ task, selected, onSelect }) {
  const navigate = useNavigate();
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";

  return (
    <div
      className={`relative w-full text-left rounded-xl bg-surface shadow-card overflow-hidden transition-all ${
        selected
          ? "ring-2 ring-blue-500/50 dark:ring-blue-400/40"
          : "hover:ring-1 hover:ring-ring-hover"
      }`}
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-blue-500" />

      <div className="flex items-start gap-3 pl-5 pr-4 py-3.5">
        {/* Checkbox */}
        <button
          type="button"
          onClick={() => onSelect?.(task.id)}
          className="shrink-0 mt-0.5 group"
          aria-label="Select task"
        >
          <div
            className={`w-7 h-7 rounded-full border-2 transition-all flex items-center justify-center ${
              selected
                ? "border-blue-500 bg-blue-500"
                : "border-blue-300 dark:border-blue-500/40 group-hover:border-blue-400 dark:group-hover:border-blue-400"
            }`}
          >
            {selected && (
              <svg className="w-3.5 h-3.5 text-white animate-checkbox-pop" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
        </button>

        {/* Content area */}
        <div
          className="flex-1 min-w-0 cursor-pointer"
          onClick={() => navigate(`/tasks/${task.id}`)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") navigate(`/tasks/${task.id}`); }}
        >
          <div className="flex items-start justify-between gap-2">
            <p className="text-[15px] font-semibold text-heading leading-snug truncate">
              {task.title}
            </p>
            <span className="text-xs text-faint shrink-0 mt-0.5">
              {relativeTime(task.created_at)}
            </span>
          </div>

          {task.description && task.description !== task.title && (
            <p className="text-xs text-dim truncate mt-1">{task.description.slice(0, 120)}</p>
          )}

          {/* Tags row */}
          <div className="flex items-center gap-1.5 mt-2">
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
          </div>
        </div>
      </div>
    </div>
  );
});
