import ErrorAlert from "../../components/ErrorAlert";
import { projectBadgeColor, modelDisplayName } from "../../lib/constants";
import { relativeTime } from "../../lib/formatters";
import TaskExpandedContent from "../../components/cards/TaskExpandedContent";

function PlanningCard({ task, selecting, selected, onToggle, expanded, onExpand, onRefresh }) {
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";
  const preview = task.description && task.description !== task.title
    ? task.description
    : task.project_name || null;
  const isHigh = task.priority >= 1;

  const handleClick = () => {
    if (selecting) onToggle?.(task.id);
    else onExpand?.(task.id);
  };

  return (
    <div className="relative">
      {selecting && (
        <div
          className="absolute left-0 top-[29px] -translate-x-1/2 -translate-y-1/2 z-10 cursor-pointer"
          onClick={(e) => { e.stopPropagation(); onToggle?.(task.id); }}
        >
          <div className={`w-5 h-5 rounded-full border-[1.5px] flex items-center justify-center transition-colors ${
            selected ? "bg-cyan-500 border-cyan-500" : "border-edge bg-surface"
          }`}>
            {selected && (
              <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
        </div>
      )}
      <div
        className={`w-full text-left rounded-2xl bg-surface overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)] ${
          expanded && !selecting
            ? "shadow-lg scale-[1.02] ring-1 ring-cyan-500/20 z-10"
            : "shadow-card scale-100"
        } ${selecting && selected ? "ring-1 ring-cyan-500" : ""}`}
      >
        <div
          className={`flex items-start gap-3 px-5 cursor-pointer transition-[padding] duration-300 ease-in-out ${
            expanded && !selecting ? "pt-6 pb-10" : "py-[18px]"
          }`}
          onClick={handleClick}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") handleClick(); }}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-3">
              <p className={`text-base font-semibold leading-snug transition-all duration-300 ease-in-out ${
                expanded && !selecting ? "text-heading whitespace-pre-wrap" : "text-heading truncate"
              }`}>
                {task.title}
              </p>
              <span className="text-[11px] text-faint shrink-0 mt-0.5">
                {relativeTime(task.created_at)}
              </span>
            </div>

            {preview && (
              <p className={`text-sm text-dim leading-relaxed mt-1.5 transition-all duration-300 ease-in-out ${
                expanded && !selecting ? "line-clamp-none" : "line-clamp-2"
              }`}>
                {expanded && !selecting ? preview : preview.slice(0, 200)}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-1.5 mt-2.5">
              {task.project_name && (
                <span className={`text-[11px] font-medium rounded-full px-2 py-0.5 ${projColor}`}>
                  {task.project_name}
                </span>
              )}
              {task.model && (
                <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-elevated text-dim">
                  {modelDisplayName(task.model)}
                </span>
              )}
              {task.effort && (
                <span className="text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-elevated text-dim uppercase">
                  {task.effort[0]}
                </span>
              )}
              {isHigh && (
                <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-500 dark:text-amber-400">
                  H
                </span>
              )}
              {task.notify_at && (
                <span className="text-[11px] text-amber-500 dark:text-amber-400 flex items-center gap-0.5">
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  {relativeTime(task.notify_at)}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function PlanningView({ tasks, loading, selecting, selected, onToggle, expandedTaskId, onExpandTask, onRefresh }) {
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
          selecting={selecting}
          selected={selected.has(task.id)}
          onToggle={onToggle}
          expanded={expandedTaskId === task.id}
          onExpand={onExpandTask}
          onRefresh={onRefresh}
        />
      ))}
    </div>
  );
}
