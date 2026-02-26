import StatusBadge from "./StatusBadge";
import ModeBadge from "./ModeBadge";
import { projectBadgeColor } from "../lib/constants";
import { relativeTime } from "../lib/formatters";

export default function TaskCard({ task, isExpanded, onToggle, showProject = true }) {
  const projColor = projectBadgeColor(task.project);
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`w-full text-left rounded-xl bg-surface shadow-card p-4 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 ${
        isExpanded ? "ring-1 ring-cyan-500/50" : ""
      }`}
    >
      {/* Top row: badges */}
      <div className="flex flex-wrap items-center gap-2 mb-2">
        {showProject && (
          <span className={`text-xs font-medium rounded-full px-2 py-0.5 ${projColor}`}>
            {task.project}
          </span>
        )}
        <ModeBadge mode={task.mode} />
        <StatusBadge status={task.status} />
        <span className="ml-auto text-xs text-dim whitespace-nowrap">
          {relativeTime(task.completed_at || task.created_at)}
        </span>
      </div>

      {/* Prompt preview */}
      <p className="text-sm text-heading line-clamp-2 leading-snug">
        {task.prompt}
      </p>

      {/* Agent name */}
      <div className="mt-1.5 flex items-center gap-1.5 text-xs text-dim">
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        <span className="truncate">{task.agent_name}</span>
      </div>

      {/* Expand indicator */}
      <div className="mt-2 flex justify-center">
        <svg
          className={`w-4 h-4 text-faint transition-transform ${isExpanded ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </div>
    </button>
  );
}
