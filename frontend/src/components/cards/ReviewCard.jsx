import { memo, useState } from "react";
import { useNavigate } from "react-router-dom";

export default memo(function ReviewCard({ task, merging, verifying, onApprove, onReject, onRetryMerge, onCancel, onVerify }) {
  const navigate = useNavigate();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  const isMerging = merging || task.status === "MERGING";

  const statusDot = isMerging ? "bg-purple-500 animate-pulse"
    : task.status === "REVIEW" ? "bg-amber-500"
    : task.status === "CONFLICT" ? "bg-red-500"
    : "bg-purple-500 animate-pulse";

  const statusLabel = isMerging ? "Merging..."
    : task.status === "REVIEW" ? "Needs Review"
    : task.status === "CONFLICT" ? "Conflict"
    : "Merging...";

  const statusColor = isMerging ? "text-purple-400"
    : task.status === "REVIEW" ? "text-amber-400"
    : task.status === "CONFLICT" ? "text-red-400"
    : "text-purple-400";

  return (
    <div
      data-review-task={task.id}
      onClick={() => !rejecting && navigate(`/tasks/${task.id}`)}
      onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && !rejecting) { e.preventDefault(); navigate(`/tasks/${task.id}`); } }}
      role="button"
      tabIndex={0}
      className="w-full text-left rounded-xl bg-surface shadow-card p-4 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover cursor-pointer"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`w-2 h-2 rounded-full ${statusDot}`} />
        <span className={`text-xs font-medium ${statusColor}`}>{statusLabel}</span>
        {task.attempt_number > 1 && (
          <span className="text-xs text-orange-400 font-medium">Attempt #{task.attempt_number}</span>
        )}
      </div>

      <p className="text-sm font-semibold text-heading truncate">{task.title}</p>

      {task.status === "REVIEW" && task.agent_summary && (
        <p className="text-xs text-dim mt-1 line-clamp-3">{task.agent_summary.slice(0, 200)}</p>
      )}

      {task.status === "CONFLICT" && task.error_message && (
        <p className="text-xs text-red-400/80 mt-1 line-clamp-2">{task.error_message.slice(0, 150)}</p>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-2 mt-2">
        {isMerging && (
          <span className="text-xs text-purple-400 font-medium">Merging branch...</span>
        )}
        {task.status === "REVIEW" && !rejecting && !isMerging && (() => {
          const arts = task.review_artifacts ? (() => { try { return JSON.parse(task.review_artifacts); } catch { return {}; } })() : {};
          const vs = arts.verify_status;
          return (
            <>
              {vs === "running" || verifying ? (
                <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 flex items-center gap-1">
                  <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="31.4" strokeLinecap="round" /></svg>
                  Verifying
                </span>
              ) : vs === "pass" ? (
                <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-green-500/15 text-green-400">Verified</span>
              ) : vs === "fail" ? (
                <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400">Verify Failed</span>
              ) : vs === "warn" ? (
                <span className="px-2.5 py-1 rounded-lg text-xs font-medium bg-amber-500/15 text-amber-400">Verify Warn</span>
              ) : vs === "error" ? (
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
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); if (confirm("Delete this task?")) onCancel?.(task); }}
                className="px-2.5 py-1 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors"
              >
                Delete
              </button>
            </>
          );
        })()}
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
  );
});
