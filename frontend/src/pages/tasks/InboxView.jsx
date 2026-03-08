import { useNavigate } from "react-router-dom";
import useAsyncHandler from "../../hooks/useAsyncHandler";
import ErrorAlert from "../../components/ErrorAlert";
import InboxCard from "../../components/cards/InboxCard";
import { dispatchTask, cancelTask } from "../../lib/api";

export default function InboxView({ tasks, loading, onRefresh }) {
  const navigate = useNavigate();
  const { loadingIds, error, setError, handle } = useAsyncHandler();

  const sorted = [...tasks].sort((a, b) => {
    if (b.priority !== a.priority) return b.priority - a.priority;
    return new Date(b.created_at) - new Date(a.created_at);
  });

  if (!loading && sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-faint">
        <svg className="w-10 h-10 mb-2" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
        </svg>
        <p className="text-sm font-medium">Inbox zero</p>
        <p className="text-xs mt-1">Tap + to create a new task</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <ErrorAlert error={error} onDismiss={() => setError(null)} />
      {sorted.map((task) => (
        <InboxCard
          key={task.id}
          task={task}
          onDispatch={(t) => handle(t.id, () => dispatchTask(t.id).then(() => onRefresh?.()), "Dispatch failed")}
          onDelete={(t) => handle(t.id, () => cancelTask(t.id).then(() => onRefresh?.()), "Delete failed")}
          loading={loadingIds.has(task.id)}
          onEdit={(t) => navigate(`/tasks/${t.id}`)}
        />
      ))}
    </div>
  );
}
