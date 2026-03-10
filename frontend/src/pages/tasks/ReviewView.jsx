import useAsyncHandler from "../../hooks/useAsyncHandler";
import ErrorAlert from "../../components/ErrorAlert";
import ReviewCard from "../../components/cards/ReviewCard";
import { approveTask, rejectTask, cancelTask, verifyTask } from "../../lib/api";

const STATUS_ORDER = { REVIEW: 0, CONFLICT: 1, MERGING: 2 };

export default function ReviewView({ tasks, loading, onRefresh, selectedTaskId, onSelectTask }) {
  const { loadingIds, error, setError, handle } = useAsyncHandler();

  const sorted = [...tasks].sort((a, b) => {
    const oa = STATUS_ORDER[a.status] ?? 9;
    const ob = STATUS_ORDER[b.status] ?? 9;
    if (oa !== ob) return oa - ob;
    return new Date(b.created_at) - new Date(a.created_at);
  });

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
    <div className="space-y-2">
      <ErrorAlert error={error} onDismiss={() => setError(null)} />
      {sorted.map((task) => (
        <ReviewCard
          key={task.id}
          task={task}
          selected={selectedTaskId === task.id}
          onSelect={onSelectTask}
          merging={loadingIds.has(task.id)}
          rejecting={false}
          verifying={loadingIds.has(`verify-${task.id}`)}
          onApprove={(t) => handle(t.id, () => approveTask(t.id).then(() => onRefresh?.()), "Approve failed")}
          onReject={(t, reason) => handle(t.id, () => rejectTask(t.id, reason).then(() => onRefresh?.()), "Reject failed")}
          onRetryMerge={(t) => handle(t.id, () => approveTask(t.id).then(() => onRefresh?.()), "Retry merge failed")}
          onCancel={(t) => handle(t.id, () => cancelTask(t.id).then(() => onRefresh?.()), "Cancel failed")}
          onVerify={(t) => handle(`verify-${t.id}`, () => verifyTask(t.id).then(() => onRefresh?.()), "Verify failed")}
        />
      ))}
    </div>
  );
}
