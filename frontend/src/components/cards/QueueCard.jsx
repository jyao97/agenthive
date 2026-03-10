import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { modelDisplayName } from "../../lib/constants";

export default memo(function QueueCard({ task, position, selected, onSelect }) {
  const navigate = useNavigate();

  return (
    <div
      className={`relative w-full text-left rounded-xl bg-surface shadow-card overflow-hidden transition-all ${
        selected
          ? "ring-2 ring-green-500/50 dark:ring-green-400/40"
          : "hover:ring-1 hover:ring-ring-hover"
      }`}
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-green-500" />

      <div className="flex items-start gap-3 pl-5 pr-4 py-3.5">
        {/* Checkbox with position number */}
        <button
          type="button"
          onClick={() => onSelect?.(task.id)}
          className="shrink-0 mt-0.5 group"
          aria-label="Select task"
        >
          <div
            className={`w-7 h-7 rounded-full border-2 transition-all flex items-center justify-center ${
              selected
                ? "border-green-500 bg-green-500"
                : "border-green-300 dark:border-green-500/40 group-hover:border-green-400 dark:group-hover:border-green-400"
            }`}
          >
            {selected ? (
              <svg className="w-3.5 h-3.5 text-white animate-checkbox-pop" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            ) : (
              <span className="text-[10px] font-bold text-green-500/60 dark:text-green-400/50">
                {position}
              </span>
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
          <p className="text-[15px] font-semibold text-heading leading-snug truncate">
            {task.title}
          </p>

          {/* Tags row */}
          <div className="flex items-center gap-1.5 mt-2">
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
      </div>
    </div>
  );
});
