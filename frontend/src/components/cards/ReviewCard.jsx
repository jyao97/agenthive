import { memo } from "react";
import CardShell, { cardPadding } from "./CardShell";
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
    else if (!(expanded && !selecting)) onExpand?.(task.id);
  };

  return (
    <div className="relative">
      <CardShell expanded={expanded} selecting={selecting} selected={selected} data-review-task={task.id}>
        <div
          className={`flex items-start gap-3 px-5 cursor-pointer transition-[padding] duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)] ${cardPadding(expanded, selecting)}`}
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
      </CardShell>
    </div>
  );
});
