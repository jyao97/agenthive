import { memo } from "react";
import TaskExpandedContent from "./TaskExpandedContent";

const STATUS_STYLE = {
  MERGING:  { label: "Merging...",   color: "text-purple-400" },
  REVIEW:   { label: "Needs Review", color: "text-amber-400" },
  CONFLICT: { label: "Conflict",     color: "text-red-400" },
};

export default memo(function ReviewCard({ task, selected, onSelect, merging, verifying, onApprove, onReject, onRetryMerge, onCancel, onVerify, expanded, onExpand, onRefresh }) {
  const isMerging = merging || task.status === "MERGING";
  const { label: statusLabel, color: statusColor } =
    STATUS_STYLE[isMerging ? "MERGING" : task.status] || STATUS_STYLE.MERGING;

  let verifyStatus = null;
  if (task.review_artifacts) {
    try { verifyStatus = JSON.parse(task.review_artifacts).verify_status; } catch { /* malformed JSON */ }
  }

  return (
    <div
      data-review-task={task.id}
      className={`relative w-full text-left rounded-[12px] bg-surface shadow-card overflow-hidden transition-all ${
        selected ? "ring-2 ring-purple-500/50 dark:ring-purple-400/40" : ""
      }`}
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-purple-500" />

      <div className="flex items-center gap-3.5 pl-5 pr-4 py-4">
        {/* Checkbox */}
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
            {selected && (
              <svg className="w-3 h-3 text-white animate-checkbox-pop" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
        </button>

        {/* Content area */}
        <div
          className="flex-1 min-w-0 cursor-pointer"
          onClick={() => onExpand?.(task.id)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") onExpand?.(task.id); }}
        >
          {/* Row 1: Title */}
          <p className="text-base font-semibold text-heading leading-snug truncate">{task.title}</p>

          {/* Row 2: Status + summary */}
          <div className="mt-1">
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

          {/* Row 3: Action buttons — hidden when expanded (shown in expanded content instead) */}
          {!expanded && (
            <div className="flex flex-wrap items-center gap-2 mt-2.5">
              {isMerging && (
                <span className="text-xs text-purple-400 font-medium">Merging branch...</span>
              )}
              {task.status === "REVIEW" && !isMerging && (
                <>
                  {verifyStatus === "running" || verifying ? (
                    <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 flex items-center gap-1">
                      <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="31.4" strokeLinecap="round" /></svg>
                      Verifying
                    </span>
                  ) : verifyStatus === "pass" ? (
                    <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-green-500/15 text-green-400">Verified</span>
                  ) : verifyStatus === "fail" ? (
                    <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400">Verify Failed</span>
                  ) : verifyStatus === "warn" ? (
                    <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-amber-500/15 text-amber-400">Verify Warn</span>
                  ) : verifyStatus === "error" ? (
                    <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400">Verify Error</span>
                  ) : (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); onVerify?.(task); }}
                      className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 transition-colors"
                    >
                      Verify
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onApprove?.(task); }}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-green-500/15 text-green-400 hover:bg-green-500/25 transition-colors"
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onExpand?.(task.id); }}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-amber-500/15 text-amber-400 hover:bg-amber-500/25 transition-colors"
                  >
                    Reject
                  </button>
                </>
              )}
              {task.status === "CONFLICT" && (
                <>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onRetryMerge?.(task); }}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-purple-500/15 text-purple-400 hover:bg-purple-500/25 transition-colors"
                  >
                    Retry
                  </button>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onCancel?.(task); }}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors"
                  >
                    Cancel
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && <TaskExpandedContent task={task} onRefresh={onRefresh} onCollapse={() => onExpand?.(task.id)} />}
    </div>
  );
});
