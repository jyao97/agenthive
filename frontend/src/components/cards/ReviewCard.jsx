import { memo, useState } from "react";
import { useNavigate } from "react-router-dom";

const STATUS_STYLE = {
  MERGING:  { dot: "bg-purple-500 animate-pulse", label: "Merging...",    color: "text-purple-400" },
  REVIEW:   { dot: "bg-amber-500",                label: "Needs Review",  color: "text-amber-400" },
  CONFLICT: { dot: "bg-red-500",                  label: "Conflict",      color: "text-red-400" },
};

export default memo(function ReviewCard({ task, selected, onSelect, merging, verifying, onApprove, onReject, onRetryMerge, onCancel, onVerify }) {
  const navigate = useNavigate();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

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
      className={`relative w-full text-left rounded-xl bg-surface shadow-card overflow-hidden transition-all ${
        selected
          ? "ring-2 ring-purple-500/50 dark:ring-purple-400/40"
          : "hover:ring-1 hover:ring-ring-hover"
      }`}
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-purple-500" />

      <div className="flex items-start gap-3 pl-5 pr-4 py-3.5">
        {/* Checkbox */}
        <button
          type="button"
          onClick={() => onSelect?.(task.id)}
          className="shrink-0 mt-0.5 group"
          aria-label="Select task"
        >
          <div
            className={`w-7 h-7 rounded-full border-2 transition-all flex items-center justify-center ${
              selected
                ? "border-purple-500 bg-purple-500"
                : "border-purple-300 dark:border-purple-500/40 group-hover:border-purple-400 dark:group-hover:border-purple-400"
            }`}
          >
            {selected && (
              <svg className="w-3.5 h-3.5 text-white animate-checkbox-pop" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
        </button>

        {/* Content area */}
        <div
          className="flex-1 min-w-0 cursor-pointer"
          onClick={() => !rejecting && navigate(`/tasks/${task.id}`)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && !rejecting) { e.preventDefault(); navigate(`/tasks/${task.id}`); } }}
        >
          <div className="flex items-center gap-2 mb-1">
            <span className={`text-xs font-medium ${statusColor}`}>{statusLabel}</span>
            {task.attempt_number > 1 && (
              <span className="text-xs text-orange-400 font-medium">Attempt #{task.attempt_number}</span>
            )}
          </div>

          <p className="text-[15px] font-semibold text-heading leading-snug truncate">{task.title}</p>

          {task.status === "REVIEW" && task.agent_summary && (
            <p className="text-xs text-dim mt-1 line-clamp-3">{task.agent_summary.slice(0, 200)}</p>
          )}

          {task.status === "CONFLICT" && task.error_message && (
            <p className="text-xs text-red-400/80 mt-1 line-clamp-2">{task.error_message.slice(0, 150)}</p>
          )}

          {/* Action buttons (primary workflow) */}
          <div className="flex items-center gap-2 mt-2">
            {isMerging && (
              <span className="text-xs text-purple-400 font-medium">Merging branch...</span>
            )}
            {task.status === "REVIEW" && !rejecting && !isMerging && (
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
                  onClick={(e) => { e.stopPropagation(); setRejecting(true); }}
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

          {/* Inline reject textarea */}
          {rejecting && (
            <div className="mt-2 space-y-2" onClick={(e) => e.stopPropagation()}>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why are you rejecting this?"
                rows={2}
                autoFocus
                className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-sm text-heading placeholder-hint resize-none focus:border-red-500 focus:outline-none focus:ring-1 focus:ring-red-500"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => { if (reason.trim()) { onReject?.(task, reason.trim()); setRejecting(false); setReason(""); } }}
                  disabled={!reason.trim()}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                    reason.trim()
                      ? "bg-red-500 text-white hover:bg-red-400"
                      : "bg-elevated text-dim cursor-not-allowed"
                  }`}
                >
                  Confirm Reject
                </button>
                <button
                  type="button"
                  onClick={() => { setRejecting(false); setReason(""); }}
                  className="px-3 py-1 rounded-lg text-xs font-medium bg-elevated text-label hover:text-heading transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
});
