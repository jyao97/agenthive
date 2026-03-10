import { memo, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { elapsedDisplay } from "../../lib/formatters";

export default memo(function ActiveCard({ task, selected, onSelect, onCancel, onChat }) {
  const navigate = useNavigate();
  const [elapsed, setElapsed] = useState(task.elapsed_seconds || 0);

  useEffect(() => {
    setElapsed(task.elapsed_seconds || 0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [task.elapsed_seconds]);

  return (
    <div
      className={`relative w-full text-left rounded-xl bg-surface shadow-card overflow-hidden transition-all ${
        selected
          ? "ring-2 ring-cyan-500/50 dark:ring-cyan-400/40"
          : "hover:ring-1 hover:ring-ring-hover"
      }`}
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-cyan-500" />

      <div className="flex items-start gap-3 pl-5 pr-4 py-3.5">
        {/* Pulsing checkbox */}
        <button
          type="button"
          onClick={() => onSelect?.(task.id)}
          className="shrink-0 mt-0.5 group"
          aria-label="Select task"
        >
          <div
            className={`w-7 h-7 rounded-full border-2 transition-all flex items-center justify-center ${
              selected
                ? "border-cyan-500 bg-cyan-500"
                : "border-cyan-400 dark:border-cyan-500/40"
            }`}
          >
            {selected ? (
              <svg className="w-3.5 h-3.5 text-white animate-checkbox-pop" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            ) : (
              <span className="w-2.5 h-2.5 rounded-full bg-cyan-500 animate-pulse" />
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
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-cyan-400">Running</span>
            <span className="ml-auto text-xs font-mono text-dim">{elapsedDisplay(elapsed)}</span>
          </div>

          <p className="text-[15px] font-semibold text-heading leading-snug truncate">
            {task.title}
          </p>

          {task.last_agent_message && (
            <p className="text-xs text-dim truncate mt-1">{task.last_agent_message}</p>
          )}

          {/* Inline actions for active tasks (these are primary workflow actions) */}
          <div className="flex items-center gap-2 mt-2">
            {task.agent_id && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onChat ? onChat(task) : navigate(`/agents/${task.agent_id}`); }}
                className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 transition-colors"
              >
                Chat
              </button>
            )}
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); if (confirm("Cancel this task?")) onCancel?.(task); }}
              className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
});
