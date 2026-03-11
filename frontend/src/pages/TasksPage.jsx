import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, BellOff, Sparkles } from "lucide-react";
import { fetchTasksV2, fetchTaskCounts, updateNotificationSettings, dispatchTask, cancelTask, updateTaskV2, batchProcessTasks } from "../lib/api";
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
import { CardSwipeContext } from "../components/cards/CardShell";

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
  const navigate = useNavigate();
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

  // --- Multi-select state ---
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [expandedTaskId, setExpandedTaskId] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Clear selection & expansion when perspective changes
  useEffect(() => {
    setSelecting(false);
    setSelected(new Set());
    setExpandedTaskId(null);
  }, [perspective]);

  const enterSelectMode = useCallback((preSelectId) => {
    setSelecting(true);
    setSelected(preSelectId ? new Set([preSelectId]) : new Set());
    setExpandedTaskId(null);
  }, []);

  const exitSelectMode = useCallback(() => {
    setSelecting(false);
    setSelected(new Set());
  }, []);

  const toggleOne = useCallback((id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelected(new Set(tasks.map((t) => t.id)));
  }, [tasks]);

  const deselectAll = useCallback(() => {
    setSelected(new Set());
  }, []);

  const allSelected = tasks.length > 0 && selected.size === tasks.length;

  const handleExpandTask = useCallback((taskId) => {
    setExpandedTaskId((prev) => prev === taskId ? null : taskId);
  }, []);

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

  // --- Bulk action handlers ---
  const moveOptions = MOVE_OPTIONS[perspective] || [];

  const dispatchableCount = useMemo(() => {
    if (perspective !== "INBOX" && perspective !== "PLANNING") return 0;
    return tasks.filter((t) => selected.has(t.id) && t.project_name).length;
  }, [perspective, tasks, selected]);

  const handleBulkMove = useCallback(async (targetStatus) => {
    if (selected.size === 0 || actionLoading) return;
    setActionLoading(true);
    try {
      await Promise.all([...selected].map(id => updateTaskV2(id, { status: targetStatus })));
      showToast(`Moved ${selected.size} task${selected.size > 1 ? "s" : ""}`);
      exitSelectMode();
      onRefresh();
    } catch (err) {
      showToast(`Move failed: ${err.message}`, "error");
    } finally {
      setActionLoading(false);
    }
  }, [selected, actionLoading, exitSelectMode, showToast, onRefresh]);

  const handleBulkDispatch = useCallback(async () => {
    const dispatchable = tasks.filter((t) => selected.has(t.id) && t.project_name);
    if (dispatchable.length === 0 || actionLoading) return;
    setActionLoading(true);
    try {
      await Promise.all(dispatchable.map(t => dispatchTask(t.id)));
      showToast(`Dispatched ${dispatchable.length} task${dispatchable.length > 1 ? "s" : ""}`);
      exitSelectMode();
      onRefresh();
    } catch (err) {
      showToast(`Dispatch failed: ${err.message}`, "error");
    } finally {
      setActionLoading(false);
    }
  }, [tasks, selected, actionLoading, exitSelectMode, showToast, onRefresh]);

  const handleBulkDelete = useCallback(async () => {
    if (selected.size === 0 || actionLoading) return;
    if (!confirm(`Delete ${selected.size} task${selected.size > 1 ? "s" : ""}?`)) return;
    setActionLoading(true);
    try {
      await Promise.all([...selected].map(id => cancelTask(id)));
      showToast(`Deleted ${selected.size} task${selected.size > 1 ? "s" : ""}`);
      exitSelectMode();
      onRefresh();
    } catch (err) {
      showToast(`Delete failed: ${err.message}`, "error");
    } finally {
      setActionLoading(false);
    }
  }, [selected, actionLoading, exitSelectMode, showToast, onRefresh]);

  // --- AI batch process ---
  const [batchProcessing, setBatchProcessing] = useState(false);

  const handleBatchProcess = useCallback(async () => {
    if (batchProcessing) return;
    setBatchProcessing(true);
    try {
      const res = await batchProcessTasks();
      if (res.agent_id) {
        navigate(`/agents/${res.agent_id}`);
      } else {
        showToast(res.message || "No tasks to process");
      }
    } catch (err) {
      showToast(`Batch process failed: ${err.message}`, "error");
    } finally {
      setBatchProcessing(false);
    }
  }, [batchProcessing, showToast, navigate]);

  const ViewComponent = {
    INBOX: InboxView,
    PLANNING: PlanningView,
    EXECUTING: ExecutingView,
    REVIEW: ReviewView,
    DONE: DoneView,
  }[perspective] || InboxView;

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Tasks"
        theme={theme}
        onToggleTheme={onToggleTheme}
        showTaskRing
        actions={!selecting ? (
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
        ) : undefined}
        selectAction={!selecting && tasks.length > 0 ? (
          <button
            type="button"
            onClick={enterSelectMode}
            title="Select tasks"
            className="w-8 h-8 flex items-center justify-center rounded-lg text-dim hover:text-heading hover:bg-input transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
            </svg>
          </button>
        ) : undefined}
      >
        {!selecting ? (
          <FilterTabs
            tabs={TASK_PERSPECTIVE_TABS}
            active={perspective}
            onChange={setPerspective}
            counts={counts}
            rightAction={perspective === "INBOX" && (counts?.INBOX ?? 0) > 0 ? (
              <button
                type="button"
                onClick={handleBatchProcess}
                disabled={batchProcessing}
                title="AI batch process — refine prompts & move to Planning"
                className={`min-h-[36px] min-w-[36px] flex items-center justify-center rounded-full transition-all shadow-sm ${
                  batchProcessing
                    ? "bg-cyan-500 text-white animate-pulse shadow-cyan-500/30"
                    : "bg-gradient-to-br from-cyan-500 to-blue-500 text-white hover:from-cyan-400 hover:to-blue-400 active:scale-95"
                }`}
              >
                <Sparkles className="w-4 h-4" />
              </button>
            ) : undefined}
          />
        ) : (
          <div className="flex items-center justify-between px-4 pb-2">
            <button
              type="button"
              onClick={allSelected ? deselectAll : selectAll}
              className="text-sm font-medium text-cyan-400 hover:text-cyan-300 transition-colors px-2 py-1"
            >
              {allSelected ? "Deselect All" : "Select All"}
            </button>
            <span className="text-sm text-label">
              {selected.size > 0 ? `${selected.size} selected` : "Select items"}
            </span>
            <button
              type="button"
              onClick={exitSelectMode}
              className="text-sm font-semibold text-cyan-400 hover:text-cyan-300 transition-colors px-2 py-1"
            >
              Done
            </button>
          </div>
        )}
      </PageHeader>

      <div
        className="flex-1 overflow-y-auto overflow-x-hidden"
        onClick={(e) => {
          if (expandedTaskId && !e.target.closest("[data-card]")) setExpandedTaskId(null);
        }}
      >
        <div className="max-w-2xl mx-auto w-full">
          <CardSwipeContext.Provider value={null}>
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
                  selecting={selecting}
                  selected={selected}
                  onToggle={toggleOne}
                  expandedTaskId={expandedTaskId}
                  onExpandTask={handleExpandTask}
                />
              )}
            </div>
          </CardSwipeContext.Provider>
        </div>
      </div>

      {/* ── Bulk Action Bar ── */}
      {selecting && selected.size > 0 && (
        <div className="fixed bottom-20 left-0 right-0 z-20 px-4 pb-2 animate-bar-slide-up">
          <div className="max-w-xl mx-auto bg-surface border border-divider rounded-xl shadow-lg p-3 flex items-center justify-center gap-3">
            {/* Move */}
            {moveOptions.length > 0 && (
              <button
                type="button"
                onClick={() => handleBulkMove(moveOptions[0].status)}
                disabled={actionLoading}
                className="px-3 py-1.5 rounded-lg text-sm font-medium bg-cyan-600 text-white hover:bg-cyan-500 disabled:opacity-50 transition-colors"
              >
                {moveOptions[0].label} {selected.size}
              </button>
            )}
            {/* Dispatch */}
            {dispatchableCount > 0 && (
              <button
                type="button"
                onClick={handleBulkDispatch}
                disabled={actionLoading}
                className="px-3 py-1.5 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-500 disabled:opacity-50 transition-colors"
              >
                Dispatch {dispatchableCount}
              </button>
            )}
            {/* Delete */}
            <button
              type="button"
              onClick={handleBulkDelete}
              disabled={actionLoading}
              className="px-3 py-1.5 rounded-lg text-sm font-medium bg-red-600 text-white hover:bg-red-500 disabled:opacity-50 transition-colors"
            >
              Delete {selected.size}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
