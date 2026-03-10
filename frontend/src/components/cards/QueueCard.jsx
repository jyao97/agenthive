import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { modelDisplayName } from "../../lib/constants";

export default memo(function QueueCard({ task, position, selected, onSelect }) {
  const navigate = useNavigate();

  return (
    <div
      className={`relative w-full text-left rounded-[12px] bg-surface shadow-card overflow-hidden transition-all ${
        selected ? "ring-2 ring-cyan-500/50 dark:ring-cyan-400/40" : ""
      }`}
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-green-500" />

      <div className="flex items-center gap-3.5 pl-5 pr-4 py-4">
        {/* Checkbox with position */}
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
            {selected ? (
              <svg className="w-3 h-3 text-white animate-checkbox-pop" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            ) : (
              <span className="text-[10px] font-bold text-dim">{position}</span>
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
          <p className="text-base font-semibold text-heading leading-snug truncate">
            {task.title}
          </p>

          {task.description && task.description !== task.title && (
            <p className="text-sm text-dim leading-relaxed mt-1 line-clamp-2">
              {task.description.slice(0, 200)}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-1.5 mt-2">
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
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 font-medium">
                High
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});
