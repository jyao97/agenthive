import { useState, useEffect, useCallback, useRef } from "react";
import { fetchTasks } from "../lib/api";
import { STATUS_TABS, POLL_INTERVAL } from "../lib/constants";
import TaskCard from "../components/TaskCard";
import TaskDetail from "../components/TaskDetail";
import PageHeader from "../components/PageHeader";
import FilterTabs from "../components/FilterTabs";

export default function TasksPage({ theme, onToggleTheme }) {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState("ALL");
  const [expandedId, setExpandedId] = useState(null);
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchTasks();
      setTasks(Array.isArray(data) ? data : []);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    pollRef.current = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(pollRef.current);
  }, [load]);

  // Compute counts per status
  const counts = {};
  counts.ALL = tasks.length;
  for (const tab of STATUS_TABS) {
    if (tab.key !== "ALL") {
      counts[tab.key] = tasks.filter((t) => t.status === tab.key).length;
    }
  }
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

  // Sort: newest first by created_at
  const sorted = [...filtered].sort((a, b) =>
    new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Tasks" theme={theme} onToggleTheme={onToggleTheme}>
        <FilterTabs tabs={STATUS_TABS} active={activeTab} onChange={setActiveTab} counts={counts} />
      </PageHeader>

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      {/* Task list */}
      <div className="pb-20 px-4 py-3 space-y-3">
        {loading && tasks.length === 0 && (
          <div className="flex justify-center py-12">
            <span className="text-dim text-sm animate-pulse">Loading tasks...</span>
          </div>
        )}

        {error && (
          <div className="bg-red-950/40 border border-red-800 rounded-xl p-4">
            <p className="text-red-400 text-sm">Failed to fetch tasks: {error}</p>
            <button type="button" onClick={load} className="mt-2 text-xs text-red-300 underline hover:text-red-200">
              Retry
            </button>
          </div>
        )}

        {!loading && !error && sorted.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-faint">
            <svg className="w-12 h-12 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
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
                  agentId={task.agent_id}
                  project={task.project}
                  status={task.status}
                />
              )}
            </div>
          );
        })}

        <div className="h-4" />
      </div>
      </div>
    </div>
  );
}
