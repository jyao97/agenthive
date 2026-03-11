import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { projectBadgeColor, modelDisplayName } from "../../lib/constants";
import { relativeTime } from "../../lib/formatters";
import { updateTaskV2, cancelTask } from "../../lib/api";
import CardShell, { cardPadding } from "../../components/cards/CardShell";

const ATTACH_RE = /\[Attached file: ([^\]]+)\]/g;

function parseDesc(desc) {
  if (!desc) return { text: "", files: [] };
  const files = [];
  let m;
  while ((m = ATTACH_RE.exec(desc)) !== null) files.push(m[1]);
  ATTACH_RE.lastIndex = 0;
  const text = desc.replace(ATTACH_RE, "").replace(/\n{2,}/g, "\n").trim();
  return { text, files };
}

function fileName(path) { return path.split("/").pop() || path; }
function isImagePath(path) { return /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(path); }

function PlanningCard({ task, selecting, selected, onToggle, expanded, onExpand, onRefresh }) {
  const navigate = useNavigate();
  const subState = task.planning_status || (!task.agent_id ? "queued" : "planning");
  const projColor = task.project_name ? projectBadgeColor(task.project_name) : "";
  const isHigh = task.priority >= 1;
  const isExpanded = expanded && !selecting;

  const savedDesc = task.description || "";
  const parsed = useMemo(() => parseDesc(savedDesc), [savedDesc]);
  const preview = parsed.text && parsed.text !== task.title ? parsed.text : task.project_name || null;

  const handleClick = useCallback(() => {
    if (selecting) { onToggle?.(task.id); return; }
    onExpand?.(task.id);
  }, [selecting, task.id, onToggle, onExpand]);

  return (
    <div className="relative">
      <CardShell taskId={task.id} expanded={expanded} selecting={selecting} selected={selected}>
        <div
          className={`flex items-start gap-3 px-5 cursor-pointer transition-[padding] duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)] ${
            isExpanded ? "pt-5 pb-3" : cardPadding(false, selecting)
          }`}
          onClick={handleClick}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter") handleClick(); }}
        >
          <div className={`flex-1 min-w-0 ${isExpanded ? "flex flex-col" : ""}`}>
            {/* Title + status + time */}
            <div className="flex items-start justify-between gap-3 shrink-0">
              <p className={`text-base font-semibold leading-snug text-heading ${isExpanded ? "whitespace-pre-wrap" : "truncate"}`}>
                {task.title}
              </p>
              <div className="flex items-center gap-1.5 shrink-0 mt-0.5">
                {subState === "queued" ? (
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-elevated text-faint">
                    Queued
                  </span>
                ) : subState === "needs_answer" ? (
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-500 animate-pulse">
                    Needs Answer
                  </span>
                ) : subState === "needs_approval" ? (
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-green-500/15 text-green-500">
                    Review Plan
                  </span>
                ) : (
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-cyan-500/15 text-cyan-500">
                    Planning...
                  </span>
                )}
                <span className="text-[11px] text-faint">
                  {relativeTime(task.created_at)}
                </span>
              </div>
            </div>

            {/* Description */}
            {isExpanded ? (
              <div className="flex-1 mt-1.5">
                {parsed.text && (
                  <p className="text-sm text-dim leading-relaxed whitespace-pre-wrap">
                    {parsed.text}
                  </p>
                )}
              </div>
            ) : (
              preview && (
                <p className="text-sm text-dim leading-relaxed mt-1.5 line-clamp-2">
                  {preview.slice(0, 200)}
                </p>
              )
            )}

            {/* Expanded: attachment chips */}
            {isExpanded && parsed.files.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {parsed.files.map((f) => (
                  <div key={f} className="flex items-center gap-1 px-2 py-1 rounded-lg bg-elevated text-xs max-w-[160px]">
                    {isImagePath(f) ? (
                      <img src={`/api/uploads/${fileName(f)}`} alt="" className="w-6 h-6 rounded object-cover shrink-0" />
                    ) : (
                      <svg className="w-3.5 h-3.5 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                      </svg>
                    )}
                    <span className="truncate flex-1 min-w-0 text-dim">{fileName(f)}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Tags */}
            <div className={`flex flex-wrap items-center gap-1.5 ${isExpanded ? "mt-3" : "mt-2.5"}`}>
              {task.project_name && (
                <span className={`text-[11px] font-medium rounded-full px-2 py-0.5 ${projColor}`}>{task.project_name}</span>
              )}
              {task.use_worktree !== false && (
                <span className="text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-purple-500/15 text-purple-500 dark:text-purple-400">
                  {task.worktree_name ? `WT:${task.worktree_name}` : "WT"}
                </span>
              )}
              {task.skip_permissions && (
                <span className="text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-500 dark:text-amber-400">
                  Auto
                </span>
              )}
              {task.model && (
                <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-elevated text-dim">
                  {modelDisplayName(task.model)}
                </span>
              )}
              {task.effort && (
                <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-elevated text-dim capitalize">
                  {task.effort}
                </span>
              )}
              {isHigh && (
                <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-500 dark:text-amber-400">
                  High
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

            {/* Expanded: action toolbar */}
            {isExpanded && (
              <div className="flex items-center gap-2 mt-3">
                {/* Back to Inbox */}
                <button
                  type="button"
                  onClick={async (e) => {
                    e.stopPropagation();
                    await updateTaskV2(task.id, { status: "INBOX" });
                    onRefresh?.();
                  }}
                  className="w-8 h-8 rounded-full bg-elevated flex items-center justify-center text-dim hover:text-heading active:scale-90 transition-all"
                  title="Back to Inbox"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 15L3 9m0 0l6-6M3 9h12a6 6 0 010 12h-3" />
                  </svg>
                </button>
                {/* Delete */}
                <button
                  type="button"
                  onClick={async (e) => {
                    e.stopPropagation();
                    if (!confirm("Delete this task?")) return;
                    await cancelTask(task.id);
                    onRefresh?.();
                  }}
                  className="w-8 h-8 rounded-full bg-elevated flex items-center justify-center text-dim hover:text-red-500 active:scale-90 transition-all"
                  title="Delete task"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                  </svg>
                </button>

                <div className="flex-1" />

                {/* View Conversation */}
                {task.agent_id && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); navigate(`/agents/${task.agent_id}`); }}
                    className="w-8 h-8 rounded-full bg-cyan-500 text-white flex items-center justify-center hover:bg-cyan-400 active:scale-90 transition-all"
                    title="View Conversation"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.76c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.076-4.076a1.526 1.526 0 011.037-.443 48.282 48.282 0 005.68-.494c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
                    </svg>
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </CardShell>
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
