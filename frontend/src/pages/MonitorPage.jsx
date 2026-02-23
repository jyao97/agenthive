import { useEffect, useState, useCallback } from "react";

const STATUS_COLORS = {
  ok: "bg-green-500",
  error: "bg-red-500",
  degraded: "bg-yellow-500",
  unavailable: "bg-red-500",
  unknown: "bg-gray-500",
};

const WORKER_STATUS_STYLES = {
  running: { dot: "bg-green-500 animate-pulse", label: "text-green-400", bg: "border-green-500/20" },
  exited: { dot: "bg-gray-500", label: "text-gray-400", bg: "border-gray-500/20" },
  created: { dot: "bg-yellow-500", label: "text-yellow-400", bg: "border-yellow-500/20" },
  restarting: { dot: "bg-yellow-500 animate-pulse", label: "text-yellow-400", bg: "border-yellow-500/20" },
  paused: { dot: "bg-yellow-500", label: "text-yellow-400", bg: "border-yellow-500/20" },
  dead: { dot: "bg-red-500", label: "text-red-400", bg: "border-red-500/20" },
  removed: { dot: "bg-gray-600", label: "text-gray-500", bg: "border-gray-600/20" },
};

const TASK_STATUS_ORDER = [
  "PENDING",
  "PLANNING",
  "PLAN_REVIEW",
  "EXECUTING",
  "COMPLETED",
  "FAILED",
  "TIMEOUT",
  "CANCELLED",
];

const TASK_STATUS_COLORS = {
  PENDING: "text-gray-400",
  PLANNING: "text-blue-400",
  PLAN_REVIEW: "text-amber-400",
  EXECUTING: "text-violet-400",
  COMPLETED: "text-green-400",
  FAILED: "text-red-400",
  TIMEOUT: "text-orange-400",
  CANCELLED: "text-gray-500",
};

function StatusDot({ status }) {
  const color = STATUS_COLORS[status] || STATUS_COLORS.unknown;
  return (
    <span
      className={`inline-block w-2.5 h-2.5 rounded-full ${color}`}
      aria-label={status}
    />
  );
}

function HealthCard({ label, status }) {
  return (
    <div className="rounded-xl bg-gray-900 p-4 flex items-center gap-3 min-w-0">
      <StatusDot status={status} />
      <div className="min-w-0">
        <p className="text-xs text-gray-500 uppercase tracking-wider">{label}</p>
        <p className="text-sm font-medium text-gray-100 truncate">{status}</p>
      </div>
    </div>
  );
}

function formatRuntime(createdStr) {
  if (!createdStr) return "--";
  try {
    const created = new Date(createdStr);
    const now = new Date();
    const diffMs = now - created;
    if (diffMs < 0) return "0s";
    const totalSec = Math.floor(diffMs / 1000);
    if (totalSec < 60) return `${totalSec}s`;
    const min = Math.floor(totalSec / 60);
    const sec = totalSec % 60;
    if (min < 60) return `${min}m ${sec}s`;
    const hr = Math.floor(min / 60);
    const remMin = min % 60;
    return `${hr}h ${remMin}m`;
  } catch {
    return "--";
  }
}

function truncateName(name, maxLen = 28) {
  if (!name) return "";
  if (name.length <= maxLen) return name;
  return name.slice(0, maxLen - 3) + "...";
}

function extractProjectFromName(name) {
  // Container names follow pattern: cc-worker-{task_id}
  // We try to parse project from the name or return null
  if (!name) return null;
  return null; // project info comes from the worker dict if available
}

function WorkerCard({ worker }) {
  const [expanded, setExpanded] = useState(false);
  const style = WORKER_STATUS_STYLES[worker.status] || WORKER_STATUS_STYLES.removed;

  return (
    <div
      className={`rounded-xl bg-gray-900 p-4 border-l-2 ${style.bg} cursor-pointer transition-all active:scale-[0.98]`}
      onClick={() => setExpanded((prev) => !prev)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setExpanded((prev) => !prev);
        }
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-mono font-medium text-gray-100 truncate">
            {truncateName(worker.name)}
          </p>
          <div className="flex items-center gap-2 mt-1.5 flex-wrap">
            <span className="flex items-center gap-1.5">
              <span className={`inline-block w-2 h-2 rounded-full ${style.dot}`} />
              <span className={`text-xs font-medium ${style.label}`}>
                {worker.status}
              </span>
            </span>
            {worker.project && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-violet-500/15 text-violet-400 font-medium">
                {worker.project}
              </span>
            )}
          </div>
        </div>
        <div className="text-right shrink-0">
          <p className="text-xs text-gray-500">Runtime</p>
          <p className="text-sm font-mono text-gray-300">
            {formatRuntime(worker.created)}
          </p>
        </div>
      </div>

      {/* Expand chevron */}
      <div className="flex justify-center mt-2">
        <svg
          className={`w-4 h-4 text-gray-600 transition-transform ${expanded ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-gray-800 space-y-2">
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <span className="text-gray-500">Container ID</span>
            <span className="font-mono text-gray-300 truncate">
              {worker.id ? worker.id.slice(0, 12) : "--"}
            </span>
            <span className="text-gray-500">Full Name</span>
            <span className="font-mono text-gray-300 truncate">{worker.name || "--"}</span>
            <span className="text-gray-500">Created</span>
            <span className="text-gray-300">
              {worker.created
                ? new Date(worker.created).toLocaleString()
                : "--"}
            </span>
            {worker.task_id && (
              <>
                <span className="text-gray-500">Task ID</span>
                <span className="font-mono text-gray-300 truncate">{worker.task_id}</span>
              </>
            )}
          </div>
          {worker.stream_log && (
            <div className="mt-2">
              <p className="text-xs text-gray-500 mb-1">Stream log (last 50 lines)</p>
              <pre className="text-xs font-mono text-gray-400 bg-gray-950 rounded-lg p-3 max-h-60 overflow-auto whitespace-pre-wrap break-all leading-relaxed">
                {worker.stream_log}
              </pre>
            </div>
          )}
          {!worker.stream_log && (
            <p className="text-xs text-gray-600 italic">
              No log data available in worker summary.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function EmptyWorkers() {
  return (
    <div className="rounded-xl bg-gray-900 p-8 flex flex-col items-center justify-center text-center">
      <svg
        className="w-12 h-12 text-gray-700 mb-3"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M8.25 3v1.5M4.5 8.25H3m18 0h-1.5M4.5 12H3m18 0h-1.5m-15 3.75H3m18 0h-1.5M8.25 19.5V21M12 3v1.5m0 15V21m3.75-18v1.5m0 15V21m-9-1.5h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 004.5 8.25v9a2.25 2.25 0 002.25 2.25z"
        />
      </svg>
      <p className="text-gray-500 text-sm font-medium">No active workers</p>
      <p className="text-gray-600 text-xs mt-1">
        Workers will appear here when tasks are dispatched.
      </p>
    </div>
  );
}

export default function MonitorPage() {
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(false);
  const [workers, setWorkers] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [taskCounts, setTaskCounts] = useState({});

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      setHealth(data);
      setHealthError(false);
    } catch {
      setHealthError(true);
    }
  }, []);

  const fetchWorkers = useCallback(async () => {
    try {
      const res = await fetch("/api/workers");
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      setWorkers(data);
    } catch {
      // silently ignore — will retry on next poll
    }
  }, []);

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch("/api/tasks?limit=200");
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      setTasks(data);
      const counts = {};
      for (const t of data) {
        counts[t.status] = (counts[t.status] || 0) + 1;
      }
      setTaskCounts(counts);
    } catch {
      // silently ignore
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    fetchHealth();
    fetchWorkers();
    fetchTasks();
  }, [fetchHealth, fetchWorkers, fetchTasks]);

  // Poll workers and tasks every 3 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      fetchWorkers();
      fetchTasks();
    }, 3000);
    return () => clearInterval(interval);
  }, [fetchWorkers, fetchTasks]);

  // Poll health every 10 seconds
  useEffect(() => {
    const interval = setInterval(fetchHealth, 10000);
    return () => clearInterval(interval);
  }, [fetchHealth]);

  const activeCount = workers.filter((w) => w.status === "running").length;

  return (
    <div className="p-4 space-y-5 max-w-2xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-100">Monitor</h1>
        <span className="text-xs text-gray-600">
          Auto-refreshing every 3s
        </span>
      </div>

      {/* System Health Panel */}
      <section>
        <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
          System Health
        </h2>
        {healthError && !health ? (
          <div className="rounded-xl bg-gray-900 p-4">
            <p className="text-sm text-red-400">
              Failed to reach health endpoint.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            <HealthCard
              label="Overall"
              status={health?.status || "unknown"}
            />
            <HealthCard
              label="Database"
              status={health?.db || "unknown"}
            />
            <HealthCard
              label="Docker"
              status={health?.docker || "unknown"}
            />
          </div>
        )}
      </section>

      {/* Summary Stats Row */}
      <section className="grid grid-cols-2 gap-3">
        {/* Active Workers */}
        <div className="rounded-xl bg-gray-900 p-4">
          <p className="text-xs text-gray-500 uppercase tracking-wider">
            Active Workers
          </p>
          <div className="mt-1 flex items-baseline gap-1">
            <span className="text-2xl font-bold text-violet-400">
              {activeCount}
            </span>
            <span className="text-sm text-gray-500">
              / {workers.length} total
            </span>
          </div>
        </div>

        {/* Task Status Counts */}
        <div className="rounded-xl bg-gray-900 p-4">
          <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">
            Tasks by Status
          </p>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {TASK_STATUS_ORDER.map((st) =>
              taskCounts[st] ? (
                <span key={st} className="text-xs whitespace-nowrap">
                  <span className={TASK_STATUS_COLORS[st] || "text-gray-400"}>
                    {taskCounts[st]}
                  </span>{" "}
                  <span className="text-gray-600">
                    {st.toLowerCase().replace("_", " ")}
                  </span>
                </span>
              ) : null,
            )}
            {Object.keys(taskCounts).length === 0 && (
              <span className="text-xs text-gray-600">No tasks</span>
            )}
          </div>
        </div>
      </section>

      {/* Workers */}
      <section>
        <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
          Workers ({workers.length})
        </h2>
        {workers.length === 0 ? (
          <EmptyWorkers />
        ) : (
          <div className="space-y-3">
            {workers.map((w) => (
              <WorkerCard key={w.id} worker={w} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
