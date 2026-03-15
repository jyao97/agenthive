import { memo } from "react";
import { modelDisplayName, MODEL_OPTIONS } from "../../lib/constants";
import { updateTaskV2 } from "../../lib/api";
import CardShell, { cardPadding } from "./CardShell";
import TagPicker from "./TagPicker";
import TaskExpandedContent from "./TaskExpandedContent";

const MODEL_PICKER = MODEL_OPTIONS.map(m => ({ value: m.value, label: m.label }));
const EFFORT_PICKER = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Med" },
  { value: "high", label: "High" },
  { value: "max", label: "Max" },
];
const PRIORITY_PICKER = [
  { value: 0, label: "Normal" },
  { value: 1, label: "High" },
];

export default memo(function QueueCard({ task, position, selecting, selected, onToggle, expanded, onExpand, onRefresh }) {
  const isExpanded = expanded && !selecting;

  const handleClick = () => {
    if (selecting) onToggle?.(task.id);
    else onExpand?.(task.id);
  };

  const update = async (field, value) => {
    await updateTaskV2(task.id, { [field]: value });
    onRefresh?.();
  };

  return (
    <div className="relative">
      <CardShell taskId={task.id} expanded={expanded} selecting={selecting} selected={selected}>
        <div
          className={`flex items-start gap-3 px-5 cursor-pointer transition-[padding] duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)] ${cardPadding(expanded, selecting)}`}
          onClick={handleClick}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") handleClick(); }}
        >
          <div className={`flex-1 min-w-0 ${isExpanded ? "flex flex-col min-h-[160px]" : ""}`}>
            <div className="flex items-center gap-2">
              {!selecting && (
                <span className="shrink-0 w-5 h-5 rounded-full bg-elevated flex items-center justify-center text-[9px] font-bold text-dim">
                  {position}
                </span>
              )}
              <p className="text-base font-semibold text-heading leading-snug truncate">
                {task.title}
              </p>
            </div>

            {!isExpanded && task.description && task.description !== task.title && (
              <p className="text-sm text-dim leading-relaxed mt-1.5 line-clamp-2">
                {task.description.slice(0, 200)}
              </p>
            )}

            {isExpanded ? (
              <div className="flex flex-wrap items-center gap-1.5 mt-auto">
                {task.model && (
                  <TagPicker options={MODEL_PICKER} value={task.model} onSelect={(v) => update("model", v)}
                    className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-elevated text-dim cursor-pointer active:scale-90 transition-transform">
                    {modelDisplayName(task.model)}
                  </TagPicker>
                )}
                {task.effort && (
                  <TagPicker options={EFFORT_PICKER} value={task.effort} onSelect={(v) => update("effort", v)}
                    className="text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-elevated text-dim uppercase cursor-pointer active:scale-90 transition-transform">
                    {task.effort[0]}
                  </TagPicker>
                )}
                <TagPicker options={PRIORITY_PICKER} value={task.priority >= 1 ? 1 : 0} onSelect={(v) => update("priority", v)}
                  className={`text-[11px] font-semibold px-1.5 py-0.5 rounded-full cursor-pointer active:scale-90 transition-transform ${
                    task.priority >= 1 ? "bg-amber-500/15 text-amber-500 dark:text-amber-400" : "bg-elevated text-faint"
                  }`}>
                  {task.priority >= 1 ? "H" : "N"}
                </TagPicker>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-1.5 mt-2.5">
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
                {task.priority >= 1 && (
                  <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-500 dark:text-amber-400">
                    H
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        {!selecting && expanded && <TaskExpandedContent task={task} onRefresh={onRefresh} onCollapse={() => onExpand?.(task.id)} />}
      </CardShell>
    </div>
  );
});
