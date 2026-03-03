import { memo, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { elapsedDisplay } from "../../lib/formatters";

export default memo(function ActiveCard({ task, onCancel, onChat }) {
  const navigate = useNavigate();
  const [elapsed, setElapsed] = useState(task.elapsed_seconds || 0);

  useEffect(() => {
    setElapsed(task.elapsed_seconds || 0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [task.elapsed_seconds]);

  return (
    <button
      type="button"
      onClick={() => navigate(`/tasks/${task.id}`)}
      className="w-full text-left rounded-xl bg-surface shadow-card p-4 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span className="w-2 h-2 rounded-full bg-cyan-500 animate-pulse" />
        <span className="text-xs font-medium text-cyan-400">Running</span>
        <span className="ml-auto text-xs font-mono text-dim">{elapsedDisplay(elapsed)}</span>
      </div>

      <p className="text-sm font-semibold text-heading truncate">{task.title}</p>

      {task.last_agent_message && (
        <p className="text-xs text-dim truncate mt-1">{task.last_agent_message}</p>
      )}

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
    </button>
  );
});
