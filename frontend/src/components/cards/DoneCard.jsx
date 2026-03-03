import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { relativeTime, durationDisplay } from "../../lib/formatters";

const STATUS_ICON = {
  COMPLETE: { color: "text-green-400", icon: "M5 13l4 4L19 7" },
  CANCELLED: { color: "text-faint", icon: "M6 18L18 6M6 6l12 12" },
  REJECTED: { color: "text-orange-400", icon: "M6 18L18 6M6 6l12 12" },
  FAILED: { color: "text-red-400", icon: "M6 18L18 6M6 6l12 12" },
  TIMEOUT: { color: "text-orange-400", icon: "M12 6v6l4 2m6-2a10 10 0 11-20 0 10 10 0 0120 0z" },
};

export default memo(function DoneCard({ task }) {
  const navigate = useNavigate();
  const si = STATUS_ICON[task.status] || STATUS_ICON.COMPLETE;
  const isCancelled = task.status === "CANCELLED";

  return (
    <button
      type="button"
      onClick={() => navigate(`/tasks/${task.id}`)}
      className="w-full text-left rounded-xl bg-surface/50 shadow-card px-4 py-3 flex items-center gap-3 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      <svg className={`w-5 h-5 shrink-0 ${si.color}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d={si.icon} />
      </svg>

      <div className="flex-1 min-w-0">
        <p className={`text-sm font-medium truncate ${isCancelled ? "text-faint line-through" : "text-heading"}`}>
          {task.title}
        </p>
      </div>

      <div className="shrink-0 text-right">
        {task.started_at && task.completed_at && (
          <p className="text-[10px] text-dim">{durationDisplay(task.started_at, task.completed_at)}</p>
        )}
        <p className="text-[10px] text-faint">{relativeTime(task.completed_at || task.created_at)}</p>
      </div>
    </button>
  );
});
