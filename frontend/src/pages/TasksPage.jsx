import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { fetchTasksV2 } from "../lib/api";
import { TASK_PERSPECTIVE_TABS } from "../lib/constants";
import PageHeader from "../components/PageHeader";
import FilterTabs from "../components/FilterTabs";
import useDraft from "../hooks/useDraft";
import usePageVisible from "../hooks/usePageVisible";
import useWebSocket from "../hooks/useWebSocket";
import InboxView from "./tasks/InboxView";
import QueueView from "./tasks/QueueView";
import ActiveView from "./tasks/ActiveView";
import ReviewView from "./tasks/ReviewView";
import DoneView from "./tasks/DoneView";

const PERSPECTIVE_STATUSES = {
  INBOX: "INBOX",
  QUEUE: "PENDING",
  ACTIVE: "EXECUTING",
  REVIEW: "REVIEW,MERGING,CONFLICT",
  DONE: "COMPLETE,CANCELLED,REJECTED,FAILED,TIMEOUT",
};

const POLL_INTERVALS = {
  INBOX: 5000,
  QUEUE: 5000,
  ACTIVE: 3000,
  REVIEW: 5000,
  DONE: 10000,
};

export default function TasksPage({ theme, onToggleTheme }) {
  const [perspective, setPerspective] = useDraft("ui:tasks-v2:perspective", "INBOX");
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [counts, setCounts] = useState({});
  const pollRef = useRef(null);
  const countPollRef = useRef(null);
  const visible = usePageVisible();
  const { lastEvent } = useWebSocket();

  // Refresh on task_update WebSocket events
  useEffect(() => {
    if (!lastEvent || lastEvent.type !== "task_update") return;
    loadTasks();
    loadCounts();
  }, [lastEvent, loadTasks, loadCounts]);

  // Fetch counts for all perspectives (lightweight)
  const loadCounts = useCallback(async () => {
    try {
      const all = await fetchTasksV2();
      const arr = Array.isArray(all) ? all : [];
      setCounts({
        INBOX: arr.filter((t) => t.status === "INBOX").length,
        QUEUE: arr.filter((t) => t.status === "PENDING").length,
        ACTIVE: arr.filter((t) => t.status === "EXECUTING").length,
        REVIEW: arr.filter((t) => ["REVIEW", "MERGING", "CONFLICT"].includes(t.status)).length,
        DONE: arr.filter((t) => ["COMPLETE", "CANCELLED", "REJECTED", "FAILED", "TIMEOUT"].includes(t.status)).length,
      });
    } catch {
      // silently fail count refresh
    }
  }, []);

  // Fetch tasks for current perspective
  const loadTasks = useCallback(async () => {
    try {
      const statuses = PERSPECTIVE_STATUSES[perspective];
      const limit = perspective === "DONE" ? 50 : 100;
      const data = await fetchTasksV2(`statuses=${statuses}&limit=${limit}`);
      setTasks(Array.isArray(data) ? data : []);
    } catch {
      // keep stale data on error
    } finally {
      setLoading(false);
    }
  }, [perspective]);

  // Load on mount + poll
  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    loadTasks();
    loadCounts();
    const interval = POLL_INTERVALS[perspective] || 5000;
    pollRef.current = setInterval(loadTasks, interval);
    countPollRef.current = setInterval(loadCounts, 10000);
    return () => {
      clearInterval(pollRef.current);
      clearInterval(countPollRef.current);
    };
  }, [loadTasks, loadCounts, visible, perspective]);

  const onRefresh = useCallback(() => {
    loadTasks();
    loadCounts();
  }, [loadTasks, loadCounts]);

  const ViewComponent = {
    INBOX: InboxView,
    QUEUE: QueueView,
    ACTIVE: ActiveView,
    REVIEW: ReviewView,
    DONE: DoneView,
  }[perspective] || InboxView;

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Tasks" theme={theme} onToggleTheme={onToggleTheme}>
        <FilterTabs
          tabs={TASK_PERSPECTIVE_TABS}
          active={perspective}
          onChange={setPerspective}
          counts={{ ...counts, DONE: undefined }}
        />
      </PageHeader>

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        <div className="max-w-2xl mx-auto w-full">
          <div className="pb-20 px-4 py-3">
            {loading && tasks.length === 0 && (
              <div className="flex justify-center py-12">
                <span className="text-dim text-sm animate-pulse">Loading...</span>
              </div>
            )}
            {!loading && <ViewComponent tasks={tasks} loading={loading} onRefresh={onRefresh} />}
          </div>
        </div>
      </div>
    </div>
  );
}
