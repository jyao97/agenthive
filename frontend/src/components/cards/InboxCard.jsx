import { memo } from "react";
import { projectBadgeColor, modelDisplayName } from "../../lib/constants";
import { relativeTime } from "../../lib/formatters";
import TaskExpandedContent from "./TaskExpandedContent";

export default memo(function InboxCard({ task, selected, onSelect, expanded, onExpand, onRefresh }) {
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";
  const preview = task.description && task.description !== task.title
    ? task.description
    : task.project_name || null;

  return (
    <div
      className={`relative w-full text-left rounded-[12px] bg-surface shadow-card overflow-hidden transition-all ${
        selected ? "ring-2 ring-blue-500/50 dark:ring-blue-400/40" : ""
      }`}
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-blue-500" />

      <div className="flex items-center gap-3.5 pl-5 pr-4 py-4">
        {/* Checkbox — 24px, vertically centered */}
        <button
          type="button"
          onClick={() => onSelect?.(task.id)}
          className="shrink-0 group"
          aria-label="Select task"
        >
          <div
            className={`w-6 h-6 rounded-full border-[2px] transition-all duration-200 flex items-center justify-center ${
              selected
                ? "border-cyan-500 bg-cyan-500"
                : "border-gray-300 dark:border-gray-500 group-hover:border-cyan-400 dark:group-hover:border-cyan-400"
            }`}
          >
            {selected && (
              <svg className="w-3 h-3 text-white animate-checkbox-pop" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
        </button>

        {/* Content area */}
        <div
          className="flex-1 min-w-0 cursor-pointer"
          onClick={() => onExpand?.(task.id)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") onExpand?.(task.id); }}
        >
          {/* Row 1: Title + time */}
          <div className="flex items-start justify-between gap-3">
            <p className="text-base font-semibold text-heading leading-snug truncate">
              {task.title}
            </p>
            <span className="text-xs text-faint shrink-0 mt-0.5">
              {relativeTime(task.created_at)}
            </span>
          </div>

          {/* Row 2: Description / prompt preview (max 2 lines) — hidden when expanded */}
          {!expanded && preview && (
            <p className="text-sm text-dim leading-relaxed mt-1 line-clamp-2">
              {preview.slice(0, 200)}
            </p>
          )}

          {/* Row 3: Tags */}
          <div className="flex flex-wrap items-center gap-1.5 mt-2">
            {task.project_name && (
              <span className={`text-[11px] font-medium rounded-full px-2 py-0.5 ${projColor}`}>
                {task.project_name}
              </span>
            )}
            {task.model && (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-elevated text-dim">
                {modelDisplayName(task.model)}
              </span>
            )}
            {task.effort && (
              <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-elevated text-dim uppercase">
                {task.effort[0]}
              </span>
            )}
            {task.priority === 1 && (
              <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-400 font-medium">!</span>
            )}
            {task.notify_at && (
              <span className="text-[11px] text-amber-400 flex items-center gap-0.5">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                {relativeTime(task.notify_at)}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && <TaskExpandedContent task={task} onRefresh={onRefresh} onCollapse={() => onExpand?.(task.id)} />}
    </div>
  );
});
