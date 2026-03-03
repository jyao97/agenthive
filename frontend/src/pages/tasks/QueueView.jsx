import { useState } from "react";
import QueueCard from "../../components/cards/QueueCard";
import { updateTaskV2 } from "../../lib/api";

export default function QueueView({ tasks, loading, onRefresh }) {
  const [error, setError] = useState(null);

  const sorted = [...tasks].sort((a, b) => {
    if (b.priority !== a.priority) return b.priority - a.priority;
    return new Date(a.created_at) - new Date(b.created_at);
  });

  const handleDispatchNow = async (task) => {
    setError(null);
    try {
      await updateTaskV2(task.id, { priority: 1 });
      onRefresh?.();
    } catch (err) {
      setError(err.message || "Priority update failed");
    }
  };

  if (!loading && sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-faint">
        <svg className="w-10 h-10 mb-2" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z" />
        </svg>
        <p className="text-sm font-medium">Queue empty</p>
        <p className="text-xs mt-1">Tasks waiting to be dispatched appear here</p>
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
      {sorted.map((task, i) => (
        <QueueCard
          key={task.id}
          task={task}
          position={i + 1}
          onDispatchNow={handleDispatchNow}
        />
      ))}
    </div>
  );
}
