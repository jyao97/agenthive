import { memo } from "react";
import { relativeTime, durationDisplay } from "../../lib/formatters";
import CardShell, { cardPadding } from "./CardShell";
import TaskExpandedContent from "./TaskExpandedContent";

const STATUS_ICON = {
  COMPLETE:  { color: "border-green-500 bg-green-500", icon: "M5 13l4 4L19 7" },
  CANCELLED: { color: "border-gray-400 bg-gray-400 dark:border-gray-500 dark:bg-gray-500", icon: "M6 18L18 6M6 6l12 12" },
  REJECTED:  { color: "border-orange-500 bg-orange-500", icon: "M6 18L18 6M6 6l12 12" },
  FAILED:    { color: "border-red-500 bg-red-500", icon: "M6 18L18 6M6 6l12 12" },
  TIMEOUT:   { color: "border-orange-500 bg-orange-500", icon: "M12 6v6l4 2m6-2a10 10 0 11-20 0 10 10 0 0120 0z" },
};

export default memo(function DoneCard({ task, selecting, selected, onToggle, expanded, onExpand, onRefresh }) {
  const si = STATUS_ICON[task.status] || STATUS_ICON.COMPLETE;
  const isCancelled = task.status === "CANCELLED";

  const handleClick = () => {
    if (selecting) onToggle?.(task.id);
    else if (!(expanded && !selecting)) onExpand?.(task.id);
  };

  return (
    <div className="relative">
      <CardShell
        expanded={expanded}
        selecting={selecting}
        selected={selected}
        className="cursor-pointer"
        onClick={handleClick}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter") handleClick(); }}
      >
        <div className={`flex items-center gap-3 px-5 transition-[padding] duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)] ${cardPadding(expanded, selecting)}`}>
          {/* Status icon — only when not selecting */}
          {!selecting && (
            <div className="shrink-0">
              <div className={`w-5 h-5 rounded-full border-[1.5px] flex items-center justify-center ${si.color}`}>
                <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d={si.icon} />
                </svg>
              </div>
            </div>
          )}

        <div className="flex-1 min-w-0">
          <p className={`text-base font-semibold leading-snug truncate ${isCancelled ? "text-faint line-through" : "text-heading"}`}>
            {task.title}
          </p>
        </div>

        <div className="shrink-0 text-right">
          {task.started_at && task.completed_at && (
            <p className="text-[11px] text-dim">{durationDisplay(task.started_at, task.completed_at)}</p>
          )}
          <p className="text-[11px] text-faint">{relativeTime(task.completed_at || task.created_at)}</p>
        </div>
      </div>

        {!selecting && expanded && <TaskExpandedContent task={task} onRefresh={onRefresh} onCollapse={() => onExpand?.(task.id)} />}
      </CardShell>
    </div>
  );
});
