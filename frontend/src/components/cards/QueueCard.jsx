import { memo } from "react";
import { modelDisplayName } from "../../lib/constants";
import TaskExpandedContent from "./TaskExpandedContent";

export default memo(function QueueCard({ task, position, selecting, selected, onToggle, expanded, onExpand, onRefresh }) {
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
        className={`w-full text-left rounded-2xl bg-surface overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)] ${
          expanded && !selecting
            ? "shadow-lg scale-[1.02] ring-1 ring-cyan-500/20 z-10"
            : "shadow-card scale-100"
        } ${selecting && selected ? "ring-1 ring-cyan-500" : ""}`}
      >
        <div
          className={`flex items-start gap-3 px-5 cursor-pointer transition-[padding] duration-300 ease-in-out ${
            expanded && !selecting ? "pt-6 pb-10" : "py-[18px]"
          }`}
          onClick={handleClick}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") handleClick(); }}
        >
          {/* Content area */}
          <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            {!selecting && (
              <span className="shrink-0 w-5 h-5 rounded-full bg-elevated flex items-center justify-center text-[9px] font-bold text-dim">
                {position}
              </span>
            )}
            <p className="text-base font-semibold text-heading leading-snug truncate">
              {task.title}
            </p>
          </div>

          {!expanded && task.description && task.description !== task.title && (
            <p className="text-sm text-dim leading-relaxed mt-1.5 line-clamp-2">
              {task.description.slice(0, 200)}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-1.5 mt-2.5">
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
            {task.priority >= 1 && (
              <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-500 dark:text-amber-400">
                H
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
