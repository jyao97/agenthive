import { memo, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { elapsedDisplay } from "../../lib/formatters";
import CardShell, { cardPadding } from "./CardShell";
import TaskExpandedContent from "./TaskExpandedContent";

export default memo(function ActiveCard({ task, selecting, selected, onToggle, onCancel, onChat, expanded, onExpand, onRefresh }) {
  const navigate = useNavigate();
  const [elapsed, setElapsed] = useState(task.elapsed_seconds || 0);

  useEffect(() => {
    setElapsed(task.elapsed_seconds || 0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [task.elapsed_seconds]);

  const handleClick = () => {
    if (selecting) onToggle?.(task.id);
    else if (!(expanded && !selecting)) onExpand?.(task.id);
  };

  return (
    <div className="relative">
      <CardShell expanded={expanded} selecting={selecting} selected={selected}>
        <div
          className={`flex items-start gap-3 px-5 cursor-pointer transition-[padding] duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)] ${cardPadding(expanded, selecting)}`}
          onClick={handleClick}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") handleClick(); }}
        >
          {/* Pulsing dot — only when not selecting */}
          {!selecting && (
            <div className="shrink-0 mt-[7px]">
              <span className="w-2 h-2 rounded-full bg-cyan-500 animate-pulse block" />
            </div>
          )}

          {/* Content area */}
          <div className="flex-1 min-w-0">
          {/* Row 1: Title + elapsed */}
          <div className="flex items-start justify-between gap-3">
            <p className="text-base font-semibold text-heading leading-snug truncate">
              {task.title}
            </p>
            <span className="text-[11px] font-mono text-dim shrink-0 mt-0.5">{elapsedDisplay(elapsed)}</span>
          </div>

          {/* Row 2: Status + last message */}
          <div className="mt-1.5">
            <span className="text-sm text-cyan-400 font-medium">Running</span>
            {task.last_agent_message && (
              <p className="text-sm text-dim leading-relaxed mt-0.5 line-clamp-2">{task.last_agent_message}</p>
            )}
          </div>
        </div>
      </div>

        {!selecting && expanded && <TaskExpandedContent task={task} onRefresh={onRefresh} onCollapse={() => onExpand?.(task.id)} />}
      </CardShell>
    </div>
  );
});
