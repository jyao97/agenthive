import { memo } from "react";
import TaskExpandedContent from "./TaskExpandedContent";

const STATUS_STYLE = {
  MERGING:  { label: "Merging...",   color: "text-purple-400" },
  REVIEW:   { label: "Needs Review", color: "text-amber-400" },
  CONFLICT: { label: "Conflict",     color: "text-red-400" },
};

export default memo(function ReviewCard({ task, selecting, selected, onToggle, merging, verifying, onApprove, onReject, onRetryMerge, onCancel, onVerify, expanded, onExpand, onRefresh }) {
  const isMerging = merging || task.status === "MERGING";
  const { label: statusLabel, color: statusColor } =
    STATUS_STYLE[isMerging ? "MERGING" : task.status] || STATUS_STYLE.MERGING;

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
        data-review-task={task.id}
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
          {/* Content area */}
          <div className="flex-1 min-w-0">
          <p className="text-base font-semibold text-heading leading-snug truncate">{task.title}</p>
          <div className="mt-1.5">
            <span className={`text-sm font-medium ${statusColor}`}>{statusLabel}</span>
            {task.attempt_number > 1 && (
              <span className="text-sm text-orange-400 font-medium ml-2">Attempt #{task.attempt_number}</span>
            )}
          </div>
          {!expanded && task.status === "REVIEW" && task.agent_summary && (
            <p className="text-sm text-dim leading-relaxed mt-1 line-clamp-2">{task.agent_summary.slice(0, 200)}</p>
          )}
          {!expanded && task.status === "CONFLICT" && task.error_message && (
            <p className="text-sm text-red-400/80 leading-relaxed mt-1 line-clamp-2">{task.error_message.slice(0, 150)}</p>
          )}
        </div>
      </div>

        {!selecting && expanded && <TaskExpandedContent task={task} onRefresh={onRefresh} onCollapse={() => onExpand?.(task.id)} />}
      </div>
    </div>
  );
});
