import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { TASK_STATUS_COLORS, TASK_STATUS_TEXT_COLORS, projectBadgeColor } from "../lib/constants";
import { relativeTime } from "../lib/formatters";

export default memo(function TaskCardV2({ task }) {
  const navigate = useNavigate();
  const dotColor = TASK_STATUS_COLORS[task.status] || "bg-gray-500";
  const textColor = TASK_STATUS_TEXT_COLORS[task.status] || "text-dim";
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";

  return (
    <button
      type="button"
      onClick={() => navigate(`/tasks/${task.id}`)}
      className="w-full text-left rounded-xl bg-surface shadow-card p-4 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      {/* Top row: status + project + time */}
      <div className="flex flex-wrap items-center gap-2 mb-1.5">
        <span className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${dotColor}`} />
          <span className={`text-xs font-medium ${textColor}`}>{task.status}</span>
        </span>
        {task.project_name && (
          <span className={`text-xs font-medium rounded-full px-2 py-0.5 ${projColor}`}>
            {task.project_name}
          </span>
        )}
        {task.attempt_number > 1 && (
          <span className="text-xs text-orange-400 font-medium">
            Attempt #{task.attempt_number}
          </span>
        )}
        <span className="ml-auto text-xs text-faint">{relativeTime(task.created_at)}</span>
      </div>

      {/* Title */}
      <p className="text-sm font-semibold text-heading truncate">{task.title}</p>

      {/* Bottom row: model + effort + agent */}
      <div className="flex items-center gap-2 mt-1.5">
        {task.model && (
          <span className="text-xs text-dim">
            {task.model.replace(/^claude-/, "").replace(/-\d{8}$/, "")}
          </span>
        )}
        {task.agent_summary && (
          <span className="text-xs text-dim truncate max-w-[200px]">
            {task.agent_summary.slice(0, 80)}
          </span>
        )}
      </div>
    </button>
  );
});
