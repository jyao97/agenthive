import { useState, useEffect, useCallback, useRef } from "react";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATUS_TABS = [
  { key: "ALL", label: "All" },
  { key: "PENDING", label: "Pending" },
  { key: "PLANNING", label: "Planning" },
  { key: "PLAN_REVIEW", label: "Review" },
  { key: "EXECUTING", label: "Executing" },
  { key: "COMPLETED", label: "Completed" },
  { key: "FAILED", label: "Failed" },
];

const STATUS_COLORS = {
  PENDING: "bg-gray-500",
  PLANNING: "bg-blue-500",
  PLAN_REVIEW: "bg-amber-500",
  EXECUTING: "bg-violet-500",
  COMPLETED: "bg-green-500",
  FAILED: "bg-red-500",
  TIMEOUT: "bg-orange-500",
  CANCELLED: "bg-gray-600",
};

const STATUS_TEXT_COLORS = {
  PENDING: "text-gray-400",
  PLANNING: "text-blue-400",
  PLAN_REVIEW: "text-amber-400",
  EXECUTING: "text-violet-400",
  COMPLETED: "text-green-400",
  FAILED: "text-red-400",
  TIMEOUT: "text-orange-400",
  CANCELLED: "text-gray-500",
};

const PRIORITY_COLORS = {
  P0: "bg-red-500/20 text-red-400 border border-red-500/40",
  P1: "bg-yellow-500/20 text-yellow-400 border border-yellow-500/40",
  P2: "bg-gray-500/20 text-gray-400 border border-gray-500/40",
};

const POLL_INTERVAL = 5000;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Turn an ISO / unix timestamp into a relative string like "2m ago". */
function relativeTime(dateStr) {
  if (!dateStr) return "";
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = Math.max(0, now - then);
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/**
 * Extremely lightweight markdown-ish renderer.
 * Handles ## headers, fenced code blocks, inline code, bold, italic, and
 * image paths that look like local file references.
 */
function renderMarkdown(text, project) {
  if (!text) return null;

  const lines = text.split("\n");
  const elements = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      elements.push(
        <pre
          key={elements.length}
          className="my-2 p-3 rounded-lg bg-gray-950 text-sm text-gray-300 overflow-x-auto font-mono"
        >
          <code>{codeLines.join("\n")}</code>
        </pre>
      );
      continue;
    }

    // Headers
    if (line.startsWith("### ")) {
      elements.push(
        <h4 key={elements.length} className="text-sm font-semibold text-gray-200 mt-3 mb-1">
          {line.slice(4)}
        </h4>
      );
      i++;
      continue;
    }
    if (line.startsWith("## ")) {
      elements.push(
        <h3 key={elements.length} className="text-base font-semibold text-gray-100 mt-4 mb-1">
          {line.slice(3)}
        </h3>
      );
      i++;
      continue;
    }
    if (line.startsWith("# ")) {
      elements.push(
        <h2 key={elements.length} className="text-lg font-bold text-gray-100 mt-4 mb-2">
          {line.slice(2)}
        </h2>
      );
      i++;
      continue;
    }

    // Image reference (simple heuristic: line is a path ending with image extension)
    const imgMatch = line.trim().match(/^!\[.*?\]\((.+?)\)$/);
    const plainImgMatch =
      !imgMatch && line.trim().match(/^(\S+\.(png|jpg|jpeg|gif|svg|webp))$/i);
    if (imgMatch || plainImgMatch) {
      const src = imgMatch ? imgMatch[1] : plainImgMatch[1];
      const resolvedSrc = src.startsWith("http")
        ? src
        : `/api/files/${project}/${src.replace(/^\/+/, "")}`;
      elements.push(
        <img
          key={elements.length}
          src={resolvedSrc}
          alt=""
          className="my-2 max-w-full rounded-lg border border-gray-800"
        />
      );
      i++;
      continue;
    }

    // Empty line
    if (line.trim() === "") {
      elements.push(<div key={elements.length} className="h-2" />);
      i++;
      continue;
    }

    // Regular paragraph — apply inline formatting
    elements.push(
      <p key={elements.length} className="text-sm text-gray-300 leading-relaxed">
        {renderInline(line)}
      </p>
    );
    i++;
  }

  return <div className="space-y-0.5">{elements}</div>;
}

/** Inline formatting: bold, italic, inline code. */
function renderInline(text) {
  // Split by inline code first, then handle bold/italic in non-code segments.
  const parts = text.split(/(`[^`]+`)/g);
  return parts.map((part, idx) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={idx}
          className="px-1 py-0.5 rounded bg-gray-800 text-violet-300 text-xs font-mono"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    // Bold
    let processed = part.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    // Italic
    processed = processed.replace(/\*(.+?)\*/g, "<i>$1</i>");
    // We use dangerouslySetInnerHTML only for these safe tags
    return <span key={idx} dangerouslySetInnerHTML={{ __html: processed }} />;
  });
}

/** Scan text for image-like paths and render them as images below the text. */
function extractImages(text, project) {
  if (!text) return null;
  const imgRegex = /(?:^|\s)(\S+\.(?:png|jpg|jpeg|gif|svg|webp))(?:\s|$)/gim;
  const matches = [];
  let m;
  while ((m = imgRegex.exec(text)) !== null) {
    matches.push(m[1]);
  }
  if (matches.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 mt-2">
      {matches.map((src, idx) => {
        const resolvedSrc = src.startsWith("http")
          ? src
          : `/api/files/${project}/${src.replace(/^\/+/, "")}`;
        return (
          <img
            key={idx}
            src={resolvedSrc}
            alt=""
            className="max-w-full max-h-48 rounded-lg border border-gray-800"
          />
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusBadge({ status }) {
  const bg = STATUS_COLORS[status] || "bg-gray-500";
  const label = status === "PLAN_REVIEW" ? "REVIEW" : status;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-medium text-white px-2 py-0.5 rounded-full ${bg}`}
    >
      {/* Pulsing dot for active states */}
      {(status === "EXECUTING" || status === "PLANNING") && (
        <span className="relative flex h-2 w-2">
          <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${bg} opacity-75`} />
          <span className={`relative inline-flex rounded-full h-2 w-2 ${bg}`} />
        </span>
      )}
      {label}
    </span>
  );
}

function PriorityBadge({ priority }) {
  const cls = PRIORITY_COLORS[priority] || PRIORITY_COLORS.P2;
  return (
    <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${cls}`}>
      {priority}
    </span>
  );
}

function ProjectBadge({ name }) {
  return (
    <span className="text-xs bg-gray-800 text-gray-300 rounded-full px-2 py-0.5">
      {name}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Task card (collapsed)
// ---------------------------------------------------------------------------

function TaskCard({ task, isExpanded, onToggle }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`w-full text-left rounded-xl bg-gray-900 p-4 transition-colors active:bg-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
        isExpanded ? "ring-1 ring-violet-500/50" : ""
      }`}
    >
      {/* Top row: badges */}
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <ProjectBadge name={task.project} />
        <PriorityBadge priority={task.priority} />
        <StatusBadge status={task.status} />
        {task.retries > 0 && (
          <span className="text-xs text-orange-400 font-medium">
            retry {task.retries}
          </span>
        )}
        <span className="ml-auto text-xs text-gray-500 whitespace-nowrap">
          {relativeTime(task.created_at)}
        </span>
      </div>

      {/* Prompt preview */}
      <p className="text-sm text-gray-200 line-clamp-2 leading-snug">
        {task.prompt}
      </p>

      {/* Expand indicator */}
      <div className="mt-2 flex justify-center">
        <svg
          className={`w-4 h-4 text-gray-600 transition-transform ${isExpanded ? "rotate-180" : ""}`}
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

// ---------------------------------------------------------------------------
// Expanded detail panel
// ---------------------------------------------------------------------------

function TaskDetail({ taskId, project, status, onAction }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [rejecting, setRejecting] = useState(false);
  const [revisionNotes, setRevisionNotes] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  const fetchDetail = useCallback(async () => {
    try {
      const res = await fetch(`/api/tasks/${taskId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setDetail(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    fetchDetail();
    // Keep refreshing while in an active state
    const active = ["PENDING", "PLANNING", "PLAN_REVIEW", "EXECUTING"];
    if (!active.includes(status)) return;
    const interval = setInterval(fetchDetail, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchDetail, status]);

  const handleAction = async (method, url, body) => {
    setActionLoading(true);
    try {
      const opts = { method, headers: {} };
      if (body) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
      const res = await fetch(url, opts);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      onAction();
      fetchDetail();
    } catch (err) {
      alert(`Action failed: ${err.message}`);
    } finally {
      setActionLoading(false);
      setRejecting(false);
      setRevisionNotes("");
    }
  };

  if (loading) {
    return (
      <div className="px-4 py-6 flex justify-center">
        <span className="text-gray-500 text-sm animate-pulse">Loading details...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-4 py-3">
        <p className="text-red-400 text-sm">Error loading details: {error}</p>
      </div>
    );
  }

  if (!detail) return null;

  const canCancel = ["PENDING", "PLANNING", "EXECUTING"].includes(detail.status);
  const canRetry = ["FAILED", "TIMEOUT"].includes(detail.status);
  const isPlanReview = detail.status === "PLAN_REVIEW";

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-800 p-4 space-y-4">
      {/* Timing info */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
        <span>Created: {relativeTime(detail.created_at)}</span>
        {detail.started_at && <span>Started: {relativeTime(detail.started_at)}</span>}
        {detail.completed_at && <span>Finished: {relativeTime(detail.completed_at)}</span>}
        {detail.branch && (
          <span className="text-violet-400 font-mono">branch: {detail.branch}</span>
        )}
      </div>

      {/* Full prompt */}
      <div>
        <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
          Prompt
        </h4>
        <p className="text-sm text-gray-200 whitespace-pre-wrap">{detail.prompt}</p>
      </div>

      {/* Plan */}
      {detail.plan && (
        <div>
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Plan
          </h4>
          <div className="bg-gray-950 rounded-lg p-3 max-h-80 overflow-y-auto">
            {renderMarkdown(detail.plan, project)}
          </div>
        </div>
      )}

      {/* Error message */}
      {detail.error_message && (
        <div>
          <h4 className="text-xs font-semibold text-red-500 uppercase tracking-wider mb-1">
            Error
          </h4>
          <pre className="text-sm text-red-300 bg-red-950/40 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap font-mono">
            {detail.error_message}
          </pre>
        </div>
      )}

      {/* Result summary */}
      {detail.result_summary && (
        <div>
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Result
          </h4>
          <div className="bg-gray-950 rounded-lg p-3 max-h-96 overflow-y-auto">
            {renderMarkdown(detail.result_summary, project)}
            {extractImages(detail.result_summary, project)}
          </div>
        </div>
      )}

      {/* Stream log */}
      {detail.stream_log && (
        <details className="group">
          <summary className="text-xs font-semibold text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-400">
            Stream Log
            <svg
              className="inline w-3 h-3 ml-1 transition-transform group-open:rotate-90"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </summary>
          <pre className="mt-2 text-xs text-gray-400 bg-gray-950 rounded-lg p-3 max-h-64 overflow-auto whitespace-pre-wrap font-mono">
            {detail.stream_log}
          </pre>
          {extractImages(detail.stream_log, project)}
        </details>
      )}

      {/* ---- Action buttons ---- */}
      <div className="flex flex-wrap gap-3 pt-2">
        {/* Plan review */}
        {isPlanReview && !rejecting && (
          <>
            <button
              type="button"
              disabled={actionLoading}
              onClick={() => handleAction("PUT", `/api/tasks/${taskId}/approve`)}
              className="flex-1 min-h-[44px] rounded-lg bg-green-600 hover:bg-green-500 active:bg-green-700 text-white font-semibold text-sm transition-colors disabled:opacity-50"
            >
              {actionLoading ? "Approving..." : "Approve Plan"}
            </button>
            <button
              type="button"
              disabled={actionLoading}
              onClick={() => setRejecting(true)}
              className="flex-1 min-h-[44px] rounded-lg bg-red-600 hover:bg-red-500 active:bg-red-700 text-white font-semibold text-sm transition-colors disabled:opacity-50"
            >
              Reject Plan
            </button>
          </>
        )}

        {/* Reject textarea */}
        {isPlanReview && rejecting && (
          <div className="w-full space-y-2">
            <textarea
              value={revisionNotes}
              onChange={(e) => setRevisionNotes(e.target.value)}
              placeholder="Revision notes (optional)..."
              rows={3}
              className="w-full rounded-lg bg-gray-950 border border-gray-700 text-sm text-gray-200 p-3 placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-red-500 resize-y"
            />
            <div className="flex gap-3">
              <button
                type="button"
                disabled={actionLoading}
                onClick={() =>
                  handleAction("PUT", `/api/tasks/${taskId}/reject`, {
                    revision_notes: revisionNotes,
                  })
                }
                className="flex-1 min-h-[44px] rounded-lg bg-red-600 hover:bg-red-500 active:bg-red-700 text-white font-semibold text-sm transition-colors disabled:opacity-50"
              >
                {actionLoading ? "Rejecting..." : "Confirm Reject"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setRejecting(false);
                  setRevisionNotes("");
                }}
                className="min-h-[44px] min-w-[44px] px-4 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Cancel */}
        {canCancel && (
          <button
            type="button"
            disabled={actionLoading}
            onClick={() => handleAction("DELETE", `/api/tasks/${taskId}`)}
            className="min-h-[44px] min-w-[44px] px-4 rounded-lg bg-gray-800 hover:bg-gray-700 active:bg-gray-600 text-gray-300 font-medium text-sm transition-colors disabled:opacity-50"
          >
            {actionLoading ? "Cancelling..." : "Cancel Task"}
          </button>
        )}

        {/* Retry */}
        {canRetry && (
          <button
            type="button"
            disabled={actionLoading}
            onClick={() => handleAction("POST", `/api/tasks/${taskId}/retry`)}
            className="min-h-[44px] min-w-[44px] px-4 rounded-lg bg-violet-600 hover:bg-violet-500 active:bg-violet-700 text-white font-semibold text-sm transition-colors disabled:opacity-50"
          >
            {actionLoading ? "Retrying..." : "Retry"}
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export default function TasksPage() {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState("ALL");
  const [expandedId, setExpandedId] = useState(null);
  const pollRef = useRef(null);

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch("/api/tasks");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTasks(Array.isArray(data) ? data : []);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch + polling
  useEffect(() => {
    fetchTasks();
    pollRef.current = setInterval(fetchTasks, POLL_INTERVAL);
    return () => clearInterval(pollRef.current);
  }, [fetchTasks]);

  // Compute counts per status
  const counts = {};
  counts.ALL = tasks.length;
  for (const tab of STATUS_TABS) {
    if (tab.key !== "ALL") {
      counts[tab.key] = tasks.filter((t) => t.status === tab.key).length;
    }
  }
  // The "Failed" tab also shows TIMEOUT and CANCELLED
  counts.FAILED = tasks.filter((t) =>
    ["FAILED", "TIMEOUT", "CANCELLED"].includes(t.status)
  ).length;

  // Filtered list
  const filtered =
    activeTab === "ALL"
      ? tasks
      : activeTab === "FAILED"
        ? tasks.filter((t) => ["FAILED", "TIMEOUT", "CANCELLED"].includes(t.status))
        : tasks.filter((t) => t.status === activeTab);

  // Sort: active states first (PLAN_REVIEW, EXECUTING, PLANNING, PENDING), then by created_at desc
  const statusOrder = {
    PLAN_REVIEW: 0,
    EXECUTING: 1,
    PLANNING: 2,
    PENDING: 3,
    COMPLETED: 4,
    FAILED: 5,
    TIMEOUT: 6,
    CANCELLED: 7,
  };
  const sorted = [...filtered].sort((a, b) => {
    const oa = statusOrder[a.status] ?? 99;
    const ob = statusOrder[b.status] ?? 99;
    if (oa !== ob) return oa - ob;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-gray-950 border-b border-gray-800">
        <div className="px-4 pt-4 pb-2">
          <h1 className="text-xl font-bold text-gray-100">Tasks</h1>
        </div>

        {/* Status filter tabs — horizontally scrollable */}
        <div className="overflow-x-auto scrollbar-none">
          <div className="flex gap-1 px-4 pb-3 min-w-max">
            {STATUS_TABS.map((tab) => {
              const isActive = activeTab === tab.key;
              const count = counts[tab.key] || 0;
              return (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setActiveTab(tab.key)}
                  className={`min-h-[36px] px-3 py-1.5 rounded-full text-sm font-medium transition-colors whitespace-nowrap ${
                    isActive
                      ? "bg-violet-600 text-white"
                      : "bg-gray-900 text-gray-400 hover:bg-gray-800 hover:text-gray-300"
                  }`}
                >
                  {tab.label}
                  <span
                    className={`ml-1.5 text-xs ${
                      isActive ? "text-violet-200" : "text-gray-600"
                    }`}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Task list */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {loading && tasks.length === 0 && (
          <div className="flex justify-center py-12">
            <span className="text-gray-500 text-sm animate-pulse">Loading tasks...</span>
          </div>
        )}

        {error && (
          <div className="bg-red-950/40 border border-red-800 rounded-xl p-4">
            <p className="text-red-400 text-sm">Failed to fetch tasks: {error}</p>
            <button
              type="button"
              onClick={fetchTasks}
              className="mt-2 text-xs text-red-300 underline hover:text-red-200"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !error && sorted.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-gray-600">
            <svg
              className="w-12 h-12 mb-3"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
              />
            </svg>
            <p className="text-sm">No tasks found</p>
          </div>
        )}

        {sorted.map((task) => {
          const isExpanded = expandedId === task.id;
          return (
            <div key={task.id} className="space-y-2">
              <TaskCard
                task={task}
                isExpanded={isExpanded}
                onToggle={() => setExpandedId(isExpanded ? null : task.id)}
              />
              {isExpanded && (
                <TaskDetail
                  taskId={task.id}
                  project={task.project}
                  status={task.status}
                  onAction={fetchTasks}
                />
              )}
            </div>
          );
        })}

        {/* Bottom spacer so last card is not hidden by nav bar */}
        <div className="h-4" />
      </div>
    </div>
  );
}
