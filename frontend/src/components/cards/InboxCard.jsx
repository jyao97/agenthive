import { memo } from "react";
import { projectBadgeColor, modelDisplayName } from "../../lib/constants";
import { relativeTime } from "../../lib/formatters";
import TaskExpandedContent from "./TaskExpandedContent";

export default memo(function InboxCard({ task, selecting, selected, onToggle, expanded, onExpand, onRefresh }) {
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";
  const isHigh = task.priority >= 1;

  const handleClick = () => {
    if (selecting) onToggle?.(task.id);
    else onExpand?.(task.id);
  };

  return (
    <div className="relative">
      {selecting && (
        <div
          className="absolute left-0 top-[29px] -translate-x-1/2 -translate-y-1/2 z-10 cursor-pointer"
          onClick={(e) => { e.stopPropagation(); onToggle?.(task.id); }}
        >
          <div className={`w-5 h-5 rounded-full border-[1.5px] flex items-center justify-center transition-colors ${
            selected ? "bg-cyan-500 border-cyan-500" : "border-edge bg-surface"
          }`}>
            {selected && (
              <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
        </div>
      )}
      <div
        className={`w-full text-left rounded-2xl bg-surface shadow-card overflow-hidden transition-all ${
          selecting && selected ? "ring-1 ring-cyan-500" : ""
        }`}
      >
        <div
          className="flex items-start gap-3 px-5 py-[18px] cursor-pointer"
          onClick={handleClick}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") handleClick(); }}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-3">
              <p className="text-base font-semibold text-heading leading-snug truncate">
                {task.title}
              </p>
              <span className="text-[11px] text-faint shrink-0 mt-0.5">
                {relativeTime(task.created_at)}
              </span>
            </div>

            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              {task.project_name && (
                <span className={`text-[11px] font-medium rounded-full px-2 py-0.5 ${projColor}`}>
                  {task.project_name}
                </span>
              )}
              {task.model && (
                <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-elevated text-dim">
                  {modelDisplayName(task.model)}
                </span>
              )}
              {task.effort && (
                <span className="text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-elevated text-dim uppercase">
                  {task.effort[0]}
                </span>
              )}
              {isHigh && (
                <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-500 dark:text-amber-400">
                  H
                </span>
              )}
              {task.notify_at && (
                <span className="text-[11px] text-amber-500 dark:text-amber-400 flex items-center gap-0.5">
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  {relativeTime(task.notify_at)}
                </span>
              )}
            </div>
          </div>
        </div>

        {!selecting && expanded && <TaskExpandedContent task={task} onRefresh={onRefresh} onCollapse={() => onExpand?.(task.id)} />}
      </div>
    </div>
  );
});
