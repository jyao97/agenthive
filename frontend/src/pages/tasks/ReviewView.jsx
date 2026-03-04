import { useState } from "react";
import ReviewCard from "../../components/cards/ReviewCard";
import { approveTask, rejectTask, cancelTask, verifyTask } from "../../lib/api";

const STATUS_ORDER = { REVIEW: 0, CONFLICT: 1, MERGING: 2 };

export default function ReviewView({ tasks, loading, onRefresh }) {
  const [error, setError] = useState(null);

  const sorted = [...tasks].sort((a, b) => {
    const oa = STATUS_ORDER[a.status] ?? 9;
    const ob = STATUS_ORDER[b.status] ?? 9;
    if (oa !== ob) return oa - ob;
    return new Date(b.created_at) - new Date(a.created_at);
  });

  const [mergingIds, setMergingIds] = useState(new Set());

  const handleApprove = async (task) => {
    setError(null);
    setMergingIds((s) => new Set(s).add(task.id));
    try {
      await approveTask(task.id);
      onRefresh?.();
    } catch (err) {
      setError(err.message || "Approve failed");
    } finally {
      setMergingIds((s) => { const n = new Set(s); n.delete(task.id); return n; });
    }
  };

  const [rejectingIds, setRejectingIds] = useState(new Set());

  const handleReject = async (task, reason) => {
    setError(null);
    setRejectingIds((s) => new Set(s).add(task.id));
    try {
      await rejectTask(task.id, reason);
      onRefresh?.();
    } catch (err) {
      setError(err.message || "Reject failed");
    } finally {
      setRejectingIds((s) => { const n = new Set(s); n.delete(task.id); return n; });
    }
  };

  const handleCancel = async (task) => {
    setError(null);
    try {
      await cancelTask(task.id);
      onRefresh?.();
    } catch (err) {
      setError(err.message || "Cancel failed");
    }
  };

  const handleVerify = async (task) => {
    setError(null);
    try {
      await verifyTask(task.id);
      onRefresh?.();
    } catch (err) {
      setError(err.message || "Verify failed");
    }
  };

  if (!loading && sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-faint">
        <svg className="w-10 h-10 mb-2" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <p className="text-sm font-medium">Nothing to review</p>
        <p className="text-xs mt-1">Completed tasks awaiting approval appear here</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && (
        <div className="bg-red-950/40 border border-red-800 rounded-xl px-3 py-2 flex items-center justify-between">
          <p className="text-red-400 text-sm">{error}</p>
          <button type="button" onClick={() => setError(null)} className="text-red-400/60 hover:text-red-400 text-xs ml-2">dismiss</button>
        </div>
      )}
      {sorted.map((task) => (
        <ReviewCard
          key={task.id}
          task={task}
          merging={mergingIds.has(task.id)}
          rejecting={rejectingIds.has(task.id)}
          onApprove={handleApprove}
          onReject={handleReject}
          onRetryMerge={handleApprove}
          onCancel={handleCancel}
          onVerify={handleVerify}
        />
      ))}
    </div>
  );
}
