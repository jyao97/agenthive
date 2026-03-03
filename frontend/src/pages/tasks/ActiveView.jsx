import { useState } from "react";
import ActiveCard from "../../components/cards/ActiveCard";
import { cancelTask } from "../../lib/api";

export default function ActiveView({ tasks, loading, onRefresh }) {
  const [error, setError] = useState(null);

  const sorted = [...tasks].sort((a, b) =>
    new Date(a.started_at || a.created_at) - new Date(b.started_at || b.created_at)
  );

  const handleCancel = async (task) => {
    setError(null);
    try {
      await cancelTask(task.id);
      onRefresh?.();
    } catch (err) {
      setError(err.message || "Cancel failed");
    }
  };

  if (!loading && sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-faint">
        <svg className="w-10 h-10 mb-2" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.348a1.125 1.125 0 010 1.971l-11.54 6.347a1.125 1.125 0 01-1.667-.985V5.653z" />
        </svg>
        <p className="text-sm font-medium">No active tasks</p>
        <p className="text-xs mt-1">Tasks being executed by agents appear here</p>
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
        <ActiveCard key={task.id} task={task} onCancel={handleCancel} />
      ))}
    </div>
  );
}
