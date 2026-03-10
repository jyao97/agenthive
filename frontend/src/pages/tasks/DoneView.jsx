import DoneCard from "../../components/cards/DoneCard";

export default function DoneView({ tasks, loading, expandedTaskId, onExpandTask, onRefresh }) {
  const sorted = [...tasks].sort((a, b) =>
    new Date(b.completed_at || b.created_at) - new Date(a.completed_at || a.created_at)
  );
  const completedCount = tasks.filter((t) => t.status === "COMPLETE").length;

  if (!loading && sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-faint">
        <svg className="w-10 h-10 mb-2" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <p className="text-sm font-medium">No completed tasks yet</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {completedCount > 0 && (
        <div className="flex items-center gap-2 px-1 py-2 text-sm text-green-500">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
          </svg>
          <span className="font-medium">{completedCount} task{completedCount !== 1 ? "s" : ""} completed</span>
        </div>
      )}
      {sorted.map((task) => (
        <DoneCard
          key={task.id}
          task={task}
          expanded={expandedTaskId === task.id}
          onExpand={onExpandTask}
          onRefresh={onRefresh}
        />
      ))}
    </div>
  );
}
