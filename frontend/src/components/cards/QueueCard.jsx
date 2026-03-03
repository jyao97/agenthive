import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { modelDisplayName } from "../../lib/constants";

export default memo(function QueueCard({ task, position, onDispatchNow }) {
  const navigate = useNavigate();

  return (
    <button
      type="button"
      onClick={() => navigate(`/tasks/${task.id}`)}
      className="w-full text-left rounded-xl bg-surface shadow-card p-4 flex items-center gap-3 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      <div className="shrink-0 w-9 h-9 rounded-full bg-elevated flex items-center justify-center text-sm font-bold text-dim">
        #{position}
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-heading truncate">{task.title}</p>
        <div className="flex items-center gap-2 mt-1">
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
          {task.priority === 1 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-medium">
              High
            </span>
          )}
        </div>
      </div>

      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onDispatchNow?.(task); }}
        className="shrink-0 w-9 h-9 rounded-full bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 flex items-center justify-center transition-colors"
        title="Dispatch now"
      >
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
          <path d="M8 5v14l11-7z" />
        </svg>
      </button>
    </button>
  );
});
