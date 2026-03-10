import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { relativeTime, durationDisplay } from "../../lib/formatters";

const STATUS_ICON = {
  COMPLETE:  { color: "border-green-500 bg-green-500", icon: "M5 13l4 4L19 7" },
  CANCELLED: { color: "border-gray-400 bg-gray-400 dark:border-gray-500 dark:bg-gray-500", icon: "M6 18L18 6M6 6l12 12" },
  REJECTED:  { color: "border-orange-500 bg-orange-500", icon: "M6 18L18 6M6 6l12 12" },
  FAILED:    { color: "border-red-500 bg-red-500", icon: "M6 18L18 6M6 6l12 12" },
  TIMEOUT:   { color: "border-orange-500 bg-orange-500", icon: "M12 6v6l4 2m6-2a10 10 0 11-20 0 10 10 0 0120 0z" },
};

const ACCENT_COLOR = {
  COMPLETE:  "bg-green-500/60",
  CANCELLED: "bg-gray-400 dark:bg-gray-600",
  REJECTED:  "bg-orange-500/60",
  FAILED:    "bg-red-500/60",
  TIMEOUT:   "bg-orange-500/60",
};

export default memo(function DoneCard({ task }) {
  const navigate = useNavigate();
  const si = STATUS_ICON[task.status] || STATUS_ICON.COMPLETE;
  const accent = ACCENT_COLOR[task.status] || ACCENT_COLOR.COMPLETE;
  const isCancelled = task.status === "CANCELLED";

  return (
    <div
      className="relative w-full text-left rounded-[12px] bg-surface shadow-card overflow-hidden cursor-pointer"
      onClick={() => navigate(`/tasks/${task.id}`)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter") navigate(`/tasks/${task.id}`); }}
    >
      {/* Left accent bar */}
      <div className={`absolute left-0 top-0 bottom-0 w-[3px] ${accent}`} />

      <div className="flex items-center gap-3.5 pl-5 pr-4 py-4">
        {/* Filled checkbox — 24px */}
        <div className="shrink-0">
          <div className={`w-6 h-6 rounded-full border-[2px] flex items-center justify-center ${si.color}`}>
            <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d={si.icon} />
            </svg>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <p className={`text-base font-semibold leading-snug truncate ${isCancelled ? "text-faint line-through" : "text-heading"}`}>
            {task.title}
          </p>
        </div>

        {/* Duration + time */}
        <div className="shrink-0 text-right">
          {task.started_at && task.completed_at && (
            <p className="text-xs text-dim">{durationDisplay(task.started_at, task.completed_at)}</p>
          )}
          <p className="text-xs text-faint">{relativeTime(task.completed_at || task.created_at)}</p>
        </div>
      </div>
    </div>
  );
});
