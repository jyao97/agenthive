import { memo, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { elapsedDisplay } from "../../lib/formatters";
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
      </div>
    </div>
  );
});
