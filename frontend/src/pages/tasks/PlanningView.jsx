import { useNavigate } from "react-router-dom";
import ErrorAlert from "../../components/ErrorAlert";
import { projectBadgeColor, modelDisplayName } from "../../lib/constants";
import { relativeTime } from "../../lib/formatters";

function PlanningCard({ task, selected, onSelect }) {
  const navigate = useNavigate();
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";

  return (
    <div
      className={`relative w-full text-left rounded-xl bg-surface shadow-card overflow-hidden transition-all ${
        selected
          ? "ring-2 ring-violet-500/50 dark:ring-violet-400/40"
          : "hover:ring-1 hover:ring-ring-hover"
      }`}
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-orange-500" />

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
                ? "border-violet-500 bg-violet-500"
                : "border-violet-300 dark:border-violet-500/40 group-hover:border-violet-400 dark:group-hover:border-violet-400"
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
          onClick={() => navigate(`/tasks/${task.id}`)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") navigate(`/tasks/${task.id}`); }}
        >
          <div className="flex items-start justify-between gap-2">
            <p className="text-[15px] font-semibold text-heading leading-snug truncate">
              {task.title}
            </p>
            <span className="text-xs text-faint shrink-0 mt-0.5">
              {relativeTime(task.created_at)}
            </span>
          </div>

          {task.notify_at && (
            <p className="text-[10px] text-amber-400 mt-1">Remind {relativeTime(task.notify_at)}</p>
          )}

          {/* Tags row */}
          <div className="flex items-center gap-1.5 mt-2">
            {task.project_name && (
              <span className={`text-[10px] font-medium rounded-full px-1.5 py-px ${projColor}`}>
                {task.project_name}
              </span>
            )}
            {task.model && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-elevated text-dim">
                {modelDisplayName(task.model)}
              </span>
            )}
            {task.effort && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-elevated text-dim uppercase">
                {task.effort[0]}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function PlanningView({ tasks, loading, selectedTaskId, onSelectTask }) {
  const sorted = [...tasks].sort((a, b) => {
    if (b.priority !== a.priority) return b.priority - a.priority;
    return new Date(a.created_at) - new Date(b.created_at);
  });

  if (!loading && sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-faint">
        <svg className="w-10 h-10 mb-2" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25z" />
        </svg>
        <p className="text-sm font-medium">No tasks in planning</p>
        <p className="text-xs mt-1">Move tasks from Inbox to start planning</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {sorted.map((task) => (
        <PlanningCard
          key={task.id}
          task={task}
          selected={selectedTaskId === task.id}
          onSelect={onSelectTask}
        />
      ))}
    </div>
  );
}
