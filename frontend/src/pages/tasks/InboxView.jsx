import { useState } from "react";
import InboxCard from "../../components/cards/InboxCard";
import { dispatchTask, cancelTask } from "../../lib/api";

export default function InboxView({ tasks, loading, onRefresh }) {
  const [error, setError] = useState(null);

  const sorted = [...tasks].sort((a, b) => {
    if (b.priority !== a.priority) return b.priority - a.priority;
    return new Date(b.created_at) - new Date(a.created_at);
  });

  const handleDispatch = async (task) => {
    setError(null);
    try {
      await dispatchTask(task.id);
      onRefresh?.();
    } catch (err) {
      setError(err.message || "Dispatch failed");
    }
  };

  const handleDelete = async (task) => {
    setError(null);
    try {
      await cancelTask(task.id);
      onRefresh?.();
    } catch (err) {
      setError(err.message || "Delete failed");
    }
  };

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
      {error && (
        <div className="bg-red-950/40 border border-red-800 rounded-xl px-3 py-2 flex items-center justify-between">
          <p className="text-red-400 text-sm">{error}</p>
          <button type="button" onClick={() => setError(null)} className="text-red-400/60 hover:text-red-400 text-xs ml-2">dismiss</button>
        </div>
      )}
      {sorted.map((task) => (
        <InboxCard
          key={task.id}
          task={task}
          onDispatch={handleDispatch}
          onDelete={handleDelete}
          onEdit={(t) => window.location.assign(`/tasks/${t.id}`)}
        />
      ))}
    </div>
  );
}
