import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Bell, BellOff } from "lucide-react";
import { fetchTasksV2, fetchTaskCounts, updateNotificationSettings } from "../lib/api";
import { TASK_PERSPECTIVE_TABS } from "../lib/constants";
import PageHeader from "../components/PageHeader";
import FilterTabs from "../components/FilterTabs";
import useDraft from "../hooks/useDraft";
import usePageVisible from "../hooks/usePageVisible";
import useWebSocket, { isTaskNotificationsEnabled, setTaskNotificationsEnabled } from "../hooks/useWebSocket";
import InboxView from "./tasks/InboxView";
import ExecutingView from "./tasks/ExecutingView";
import ReviewView from "./tasks/ReviewView";
import PlanningView from "./tasks/PlanningView";
import DoneView from "./tasks/DoneView";

const PERSPECTIVE_STATUSES = {
  INBOX: "INBOX",
  PLANNING: "PLANNING",
  EXECUTING: "PENDING,EXECUTING",
  REVIEW: "REVIEW,MERGING,CONFLICT",
  DONE: "COMPLETE,CANCELLED,REJECTED,FAILED,TIMEOUT",
};

const POLL_INTERVALS = {
  INBOX: 5000,
  PLANNING: 5000,
  EXECUTING: 3000,
  REVIEW: 5000,
  DONE: 10000,
};

export default function TasksPage({ theme, onToggleTheme }) {
  const [rawPerspective, setRawPerspective] = useDraft("ui:tasks-v2:perspective", "INBOX");
  // Migrate stale localStorage values from old QUEUE/ACTIVE tabs
  const perspective = (rawPerspective === "QUEUE" || rawPerspective === "ACTIVE") ? "EXECUTING" : rawPerspective;
  const setPerspective = setRawPerspective;
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [counts, setCounts] = useState({});
  const pollRef = useRef(null);
  const countPollRef = useRef(null);
  const visible = usePageVisible();
  const { lastEvent } = useWebSocket();

  // Fetch counts for all perspectives (server-side)
  const loadCounts = useCallback(async () => {
    try {
      const data = await fetchTaskCounts();
      setCounts({
        INBOX: data.INBOX ?? 0,
        PLANNING: data.PLANNING ?? 0,
        EXECUTING: (data.QUEUE ?? 0) + (data.ACTIVE ?? 0),
        REVIEW: data.REVIEW ?? 0,
        DONE: data.DONE ?? 0,
        DONE_COMPLETED: data.DONE_COMPLETED ?? 0,
      });
    } catch (err) {
      console.warn("Failed to load task counts", err);
    }
  }, []);

  // Fetch tasks for current perspective
  const loadTasks = useCallback(async () => {
    try {
      const statuses = PERSPECTIVE_STATUSES[perspective];
      const limit = perspective === "DONE" ? 50 : 100;
      const data = await fetchTasksV2(`statuses=${statuses}&limit=${limit}`);
      setTasks(Array.isArray(data) ? data : []);
    } catch (err) {
      console.warn("Failed to load tasks", err);
    } finally {
      setLoading(false);
    }
  }, [perspective]);

  // Refresh on task_update WebSocket events
  useEffect(() => {
    if (!lastEvent || lastEvent.type !== "task_update") return;
    loadTasks();
    loadCounts();
  }, [lastEvent, loadTasks, loadCounts]);

  // Load on mount + poll
  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    loadTasks();
    loadCounts();
    // DONE perspective contains terminal states — no need to poll repeatedly
    if (perspective !== "DONE") {
      const interval = POLL_INTERVALS[perspective] || 5000;
      pollRef.current = setInterval(loadTasks, interval);
    }
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

  // Double-tap nav: switch to Review tab and scroll to first review task
  useEffect(() => {
    const handler = (e) => {
      if (e.detail?.tab !== "tasks") return;
      if (perspective !== "REVIEW") {
        setPerspective("REVIEW");
        // Wait for re-render then scroll
        setTimeout(() => {
          const el = document.querySelector("[data-review-task]");
          if (el) {
            el.scrollIntoView({ behavior: "smooth", block: "center" });
            el.classList.add("ring-2", "ring-cyan-400");
            setTimeout(() => el.classList.remove("ring-2", "ring-cyan-400"), 1500);
          }
        }, 300);
      } else {
        const el = document.querySelector("[data-review-task]");
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.add("ring-2", "ring-cyan-400");
          setTimeout(() => el.classList.remove("ring-2", "ring-cyan-400"), 1500);
        }
      }
    };
    window.addEventListener("nav-scroll-to-unread", handler);
    return () => window.removeEventListener("nav-scroll-to-unread", handler);
  }, [perspective, setPerspective]);

  // Notification toggle
  const [taskNotifsOn, setTaskNotifsOn] = useState(() => isTaskNotificationsEnabled());
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);

  const showToast = useCallback((message, type = "success") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const handleToggleTaskNotifs = useCallback(() => {
    const next = !taskNotifsOn;
    setTaskNotifsOn(next);
    setTaskNotificationsEnabled(next);
    updateNotificationSettings({ tasks_enabled: next }).catch(() => {});
    showToast(next ? "Task notifications enabled" : "Task notifications disabled");
    window.dispatchEvent(new CustomEvent("task-notifs-changed", { detail: { enabled: next } }));
  }, [taskNotifsOn, showToast]);

  // Cross-pane sync: notification toggle
  useEffect(() => {
    const onNotifsChanged = (e) => setTaskNotifsOn(e.detail.enabled);
    window.addEventListener("task-notifs-changed", onNotifsChanged);
    return () => window.removeEventListener("task-notifs-changed", onNotifsChanged);
  }, []);

  const ViewComponent = {
    INBOX: InboxView,
    PLANNING: PlanningView,
    EXECUTING: ExecutingView,
    REVIEW: ReviewView,
    DONE: DoneView,
  }[perspective] || InboxView;

  return (
    <div className="h-full flex flex-col">
      {/* Toast */}
      {toast && (
        <div className={`fixed left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium pointer-events-none safe-area-toast ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      <PageHeader
        title="Tasks"
        theme={theme}
        onToggleTheme={onToggleTheme}
        showTaskRing
        actions={
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={handleToggleTaskNotifs}
              title={taskNotifsOn ? "Mute all task notifications" : "Unmute all task notifications"}
              className={`w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors ${taskNotifsOn ? "text-cyan-400" : "text-dim"}`}
            >
              {taskNotifsOn ? (
                <Bell className="w-4 h-4" />
              ) : (
                <BellOff className="w-4 h-4" />
              )}
            </button>
          </div>
        }
      >
        <FilterTabs
          tabs={TASK_PERSPECTIVE_TABS}
          active={perspective}
          onChange={setPerspective}
          counts={counts}
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
