import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Bell, BellOff } from "lucide-react";
import { fetchTasksV2, fetchTaskCounts, updateNotificationSettings, dispatchTask, cancelTask, updateTaskV2 } from "../lib/api";
import { TASK_PERSPECTIVE_TABS } from "../lib/constants";
import PageHeader from "../components/PageHeader";
import FilterTabs from "../components/FilterTabs";
import useDraft from "../hooks/useDraft";
import usePageVisible from "../hooks/usePageVisible";
import useWebSocket, { isTaskNotificationsEnabled, setTaskNotificationsEnabled, registerViewingTasks, unregisterViewingTasks } from "../hooks/useWebSocket";
import { useToast } from "../contexts/ToastContext";
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

// Move targets per perspective
const MOVE_OPTIONS = {
  INBOX:     [{ label: "Planning", status: "PLANNING" }],
  PLANNING:  [{ label: "Inbox", status: "INBOX" }],
  EXECUTING: [],
  REVIEW:    [],
  DONE:      [{ label: "Inbox", status: "INBOX" }],
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

  // --- Selection & expansion state ---
  const [selectedTaskId, setSelectedTaskId] = useState(null);
  const [expandedTaskId, setExpandedTaskId] = useState(null);
  const [showMoveMenu, setShowMoveMenu] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  // Clear selection & expansion when perspective changes
  useEffect(() => { setSelectedTaskId(null); setExpandedTaskId(null); setShowMoveMenu(false); }, [perspective]);

  const handleSelectTask = useCallback((taskId) => {
    setSelectedTaskId((prev) => prev === taskId ? null : taskId);
    setShowMoveMenu(false);
  }, []);

  const handleExpandTask = useCallback((taskId) => {
    setExpandedTaskId((prev) => prev === taskId ? null : taskId);
  }, []);

  const selectedTask = useMemo(
    () => selectedTaskId ? tasks.find((t) => t.id === selectedTaskId) : null,
    [selectedTaskId, tasks]
  );

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

  // Register viewing for notification suppression
  useEffect(() => { registerViewingTasks(); return () => unregisterViewingTasks(); }, []);

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
  const toast = useToast();

  const showToast = useCallback((message, type = "success") => {
    if (type === "error") toast.error(message);
    else toast.success(message);
  }, [toast]);

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

  // --- Floating action bar handlers ---
  const handleMove = useCallback(async (targetStatus) => {
    if (!selectedTaskId || actionLoading) return;
    setActionLoading(true);
    try {
      await updateTaskV2(selectedTaskId, { status: targetStatus });
      showToast(`Moved to ${targetStatus.charAt(0) + targetStatus.slice(1).toLowerCase()}`);
      setSelectedTaskId(null);
      setShowMoveMenu(false);
      onRefresh();
    } catch (err) {
      showToast(`Move failed: ${err.message}`, "error");
    } finally {
      setActionLoading(false);
    }
  }, [selectedTaskId, actionLoading, showToast, onRefresh]);

  const handleDispatch = useCallback(async () => {
    if (!selectedTaskId || actionLoading) return;
    setActionLoading(true);
    try {
      await dispatchTask(selectedTaskId);
      showToast("Task dispatched");
      setSelectedTaskId(null);
      onRefresh();
    } catch (err) {
      showToast(`Dispatch failed: ${err.message}`, "error");
    } finally {
      setActionLoading(false);
    }
  }, [selectedTaskId, actionLoading, showToast, onRefresh]);

  const handleDelete = useCallback(async () => {
    if (!selectedTaskId || actionLoading) return;
    if (!confirm("Delete this task?")) return;
    setActionLoading(true);
    try {
      await cancelTask(selectedTaskId);
      showToast("Task deleted");
      setSelectedTaskId(null);
      onRefresh();
    } catch (err) {
      showToast(`Delete failed: ${err.message}`, "error");
    } finally {
      setActionLoading(false);
    }
  }, [selectedTaskId, actionLoading, showToast, onRefresh]);

  const handleEdit = useCallback(() => {
    if (!selectedTaskId) return;
    setExpandedTaskId(selectedTaskId);
    setSelectedTaskId(null);
  }, [selectedTaskId]);

  const ViewComponent = {
    INBOX: InboxView,
    PLANNING: PlanningView,
    EXECUTING: ExecutingView,
    REVIEW: ReviewView,
    DONE: DoneView,
  }[perspective] || InboxView;

  const moveOptions = MOVE_OPTIONS[perspective] || [];
  const canDispatch = (perspective === "INBOX" || perspective === "PLANNING") && selectedTask?.project_name;

  return (
    <div className="h-full flex flex-col">
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
            {!loading && (
              <ViewComponent
                tasks={tasks}
                loading={loading}
                onRefresh={onRefresh}
                selectedTaskId={selectedTaskId}
                onSelectTask={handleSelectTask}
                expandedTaskId={expandedTaskId}
                onExpandTask={handleExpandTask}
              />
            )}
          </div>
        </div>
      </div>

      {/* ── Floating Action Bar ── */}
      {selectedTask && perspective !== "DONE" && (
        <div className="fixed bottom-24 left-1/2 -translate-x-1/2 z-50 animate-bar-slide-up">
          <div className="relative flex items-center gap-0.5 bg-gray-900 dark:bg-gray-800 rounded-2xl px-1.5 py-1.5 shadow-2xl border border-gray-700/50">

            {/* Move */}
            {moveOptions.length > 0 && (
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setShowMoveMenu((v) => !v)}
                  disabled={actionLoading}
                  className="px-3 py-2 rounded-xl text-sm text-gray-200 hover:bg-gray-700/60 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 7h2l2 13h10l2-13h2M8 7V5a2 2 0 012-2h4a2 2 0 012 2v2" />
                  </svg>
                  Move
                </button>

                {/* Move submenu */}
                {showMoveMenu && (
                  <div className="absolute bottom-full mb-2 left-0 bg-gray-800 dark:bg-gray-700 rounded-xl p-1 shadow-xl min-w-[140px] border border-gray-600/50">
                    {moveOptions.map((opt) => (
                      <button
                        key={opt.status}
                        type="button"
                        onClick={() => handleMove(opt.status)}
                        className="w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-gray-600/50 rounded-lg transition-colors"
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Dispatch */}
            {canDispatch && (
              <>
                <div className="w-px h-5 bg-gray-700" />
                <button
                  type="button"
                  onClick={handleDispatch}
                  disabled={actionLoading}
                  className="px-3 py-2 rounded-xl text-sm text-cyan-400 hover:bg-gray-700/60 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                >
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                  Dispatch
                </button>
              </>
            )}

            {/* Delete */}
            <div className="w-px h-5 bg-gray-700" />
            <button
              type="button"
              onClick={handleDelete}
              disabled={actionLoading}
              className="px-3 py-2 rounded-xl text-sm text-red-400 hover:bg-gray-700/60 transition-colors flex items-center gap-1.5 disabled:opacity-50"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              Delete
            </button>

            {/* Edit (More) */}
            <div className="w-px h-5 bg-gray-700" />
            <button
              type="button"
              onClick={handleEdit}
              className="px-3 py-2 rounded-xl text-sm text-gray-200 hover:bg-gray-700/60 transition-colors flex items-center gap-1.5"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
              Edit
            </button>

            {/* Dismiss */}
            <div className="w-px h-5 bg-gray-700" />
            <button
              type="button"
              onClick={() => { setSelectedTaskId(null); setShowMoveMenu(false); }}
              className="px-2 py-2 rounded-xl text-sm text-gray-400 hover:bg-gray-700/60 transition-colors"
              aria-label="Dismiss"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
