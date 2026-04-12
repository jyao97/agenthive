import { useState, useEffect, useCallback, useRef, useMemo, useLayoutEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { fetchTasksV2, fetchTaskCounts, dispatchTask, cancelTask, batchProcessTasks } from "../lib/api";
import PageHeader from "../components/PageHeader";
import usePageVisible from "../hooks/usePageVisible";
import useWebSocket, { useWsEvent, registerViewingTasks, unregisterViewingTasks } from "../hooks/useWebSocket";
import { useToast } from "../contexts/ToastContext";
import InboxView from "./tasks/InboxView";
import { CardSwipeContext } from "../components/cards/CardShell";

const INBOX_POLL_INTERVAL = 5000;

export default function TasksPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [counts, setCounts] = useState({});
  const pollRef = useRef(null);
  const countPollRef = useRef(null);
  const visible = usePageVisible();
  useWebSocket(); // ensure connection is alive

  // --- Multi-select state ---
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [expandedTaskId, setExpandedTaskId] = useState(() => {
    try { return localStorage.getItem("inbox:expandedTaskId") || null; } catch { return null; }
  });
  const [actionLoading, setActionLoading] = useState(false);

  // Scroll position persistence
  const inboxScrollRef = useRef(null);
  const scrollSaveTimer = useRef(null);
  const scrollRestored = useRef(false);
  const SCROLL_SAVE_DEBOUNCE = 200;


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
    setExpandedTaskId((prev) => {
      const next = prev === taskId ? null : taskId;
      try {
        if (next) localStorage.setItem("inbox:expandedTaskId", next);
        else localStorage.removeItem("inbox:expandedTaskId");
      } catch { /* ignore */ }
      return next;
    });
  }, []);

  // Fetch counts for all perspectives (server-side)
  const loadCounts = useCallback(async () => {
    try {
      const data = await fetchTaskCounts();
      setCounts({
        INBOX: data.INBOX ?? 0,
        EXECUTING: (data.QUEUE ?? 0) + (data.ACTIVE ?? 0),
        DONE: data.DONE ?? 0,
        DONE_COMPLETED: data.DONE_COMPLETED ?? 0,
      });
    } catch (err) {
      console.warn("Failed to load task counts", err);
    }
  }, []);

  // Fetch inbox tasks
  const loadTasks = useCallback(async () => {
    try {
      const data = await fetchTasksV2(`statuses=INBOX&limit=100`);
      setTasks(Array.isArray(data) ? data : []);
    } catch (err) {
      console.warn("Failed to load tasks", err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Refresh on task_update WebSocket events
  const loadTasksRef = useRef(loadTasks);
  loadTasksRef.current = loadTasks;
  const loadCountsRef = useRef(loadCounts);
  loadCountsRef.current = loadCounts;
  useWsEvent((event) => {
    if (event.type !== "task_update") return;
    loadTasksRef.current();
    loadCountsRef.current();
  });

  // Register viewing for notification suppression
  useEffect(() => { registerViewingTasks(); return () => unregisterViewingTasks(); }, []);

  // Load on mount + poll
  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    loadTasks();
    loadCounts();
    pollRef.current = setInterval(loadTasks, INBOX_POLL_INTERVAL);
    countPollRef.current = setInterval(loadCounts, 10000);
    return () => {
      clearInterval(pollRef.current);
      clearInterval(countPollRef.current);
    };
  }, [loadTasks, loadCounts, visible]);

  const onRefresh = useCallback(() => {
    loadTasks();
    loadCounts();
  }, [loadTasks, loadCounts]);


  const toast = useToast();

  const showToast = useCallback((message, type = "success") => {
    if (type === "error") toast.error(message);
    else toast.success(message);
  }, [toast]);

  // --- Bulk action handlers ---
  const dispatchableCount = useMemo(() => {
    return tasks.filter((t) => selected.has(t.id) && t.project_name).length;
  }, [tasks, selected]);

  const handleBulkStart = useCallback(async () => {
    const dispatchable = tasks.filter((t) => selected.has(t.id) && t.project_name);
    if (dispatchable.length === 0 || actionLoading) return;
    setActionLoading(true);
    try {
      await Promise.all(dispatchable.map(t => dispatchTask(t.id)));
      showToast(`Started ${dispatchable.length} task${dispatchable.length > 1 ? "s" : ""}`);
      exitSelectMode();
      onRefresh();
    } catch (err) {
      showToast(`Start failed: ${err.message}`, "error");
    } finally {
      setActionLoading(false);
    }
  }, [tasks, selected, actionLoading, exitSelectMode, showToast, onRefresh]);

  // --- AI batch process (triage agent) ---
  const [batchProcessing, setBatchProcessing] = useState(false);

  const handleBatchProcess = useCallback(async (taskIds) => {
    if (batchProcessing) return;
    setBatchProcessing(true);
    try {
      const res = await batchProcessTasks(taskIds);
      if (res.agent_id) {
        if (selecting) exitSelectMode();
        navigate(`/agents/${res.agent_id}`);
      } else {
        showToast(res.message || "No tasks to process");
      }
    } catch (err) {
      showToast(`Batch process failed: ${err.message}`, "error");
    } finally {
      setBatchProcessing(false);
    }
  }, [batchProcessing, selecting, exitSelectMode, showToast, navigate]);

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

  // Debounced scroll position save
  const handleInboxScroll = useCallback(() => {
    const el = inboxScrollRef.current;
    if (!el) return;
    clearTimeout(scrollSaveTimer.current);
    scrollSaveTimer.current = setTimeout(() => {
      try { localStorage.setItem("inbox:scrollTop", String(el.scrollTop)); } catch { /* ignore */ }
    }, SCROLL_SAVE_DEBOUNCE);
  }, []);

  // Save scroll position on unmount
  useEffect(() => {
    return () => {
      clearTimeout(scrollSaveTimer.current);
      const el = inboxScrollRef.current;
      if (el) {
        try { localStorage.setItem("inbox:scrollTop", String(el.scrollTop)); } catch { /* ignore */ }
      }
    };
  }, []);

  // Restore scroll position after first load
  useLayoutEffect(() => {
    if (loading || tasks.length === 0 || scrollRestored.current) return;
    scrollRestored.current = true;
    try {
      const savedPos = localStorage.getItem("inbox:scrollTop");
      const savedCount = localStorage.getItem("inbox:taskCount");
      if (savedPos && savedCount && Number(savedCount) === tasks.length) {
        const el = inboxScrollRef.current;
        if (el) el.scrollTop = Number(savedPos);
      }
    } catch { /* ignore */ }
  }, [loading, tasks.length]);

  // Keep saved task count in sync for future visits
  useEffect(() => {
    if (!loading && tasks.length > 0) {
      try { localStorage.setItem("inbox:taskCount", String(tasks.length)); } catch { /* ignore */ }
    }
  }, [loading, tasks.length]);

  // Clear expanded task if the task no longer exists in the list
  useEffect(() => {
    if (!loading && expandedTaskId && tasks.length > 0 && !tasks.some(t => t.id === expandedTaskId)) {
      setExpandedTaskId(null);
      try { localStorage.removeItem("inbox:expandedTaskId"); } catch { /* ignore */ }
    }
  }, [loading, tasks, expandedTaskId]);

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Inbox"
        theme={theme}
        onToggleTheme={onToggleTheme}
        showQueueButton
        hideMonitor
        actions={!selecting ? (
          <button
            type="button"
            onClick={() => handleBatchProcess()}
            disabled={batchProcessing}
            title="AI batch process — refine prompts & assign projects"
            className={`h-7 px-2.5 flex items-center gap-1.5 rounded-full text-[11px] font-semibold transition-all ${
              batchProcessing
                ? "bg-gradient-to-r from-cyan-500 to-blue-500 text-white animate-pulse shadow-md shadow-cyan-500/25"
                : "bg-gradient-to-r from-cyan-500/15 to-blue-500/15 text-cyan-500 dark:text-cyan-400 hover:from-cyan-500/25 hover:to-blue-500/25 active:scale-95"
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            AI
          </button>
        ) : undefined}
        selectAction={!selecting ? (
          <button
            type="button"
            onClick={() => enterSelectMode()}
            title="Select tasks"
            className="w-8 h-8 flex items-center justify-center rounded-lg text-dim hover:text-heading hover:bg-input transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
            </svg>
          </button>
        ) : undefined}
      >
        {selecting && (
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
        ref={inboxScrollRef}
        className="flex-1 overflow-y-auto overflow-x-hidden"
        onScroll={handleInboxScroll}
        onClick={(e) => {
          if (expandedTaskId && !e.target.closest("[data-card]")) setExpandedTaskId(null);
        }}
      >
        <div className="max-w-2xl mx-auto w-full">
          <CardSwipeContext.Provider value={null}>
            <div className="pb-24 px-4 py-3">
              {loading && tasks.length === 0 && (
                <div className="flex justify-center py-12">
                  <span className="text-dim text-sm animate-pulse">Loading...</span>
                </div>
              )}
              {!loading && (
                <InboxView
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
            {/* AI batch process selected */}
            <button
              type="button"
              onClick={() => handleBatchProcess([...selected])}
              disabled={batchProcessing || actionLoading}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1.5 transition-all ${
                batchProcessing
                  ? "bg-gradient-to-r from-cyan-500 to-blue-500 text-white animate-pulse"
                  : "bg-gradient-to-r from-cyan-600 to-blue-600 text-white hover:from-cyan-500 hover:to-blue-500"
              } disabled:opacity-50`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              AI {selected.size}
            </button>
            {/* Start */}
            {dispatchableCount > 0 && (
              <button
                type="button"
                onClick={handleBulkStart}
                disabled={actionLoading}
                className="px-3 py-1.5 rounded-lg text-sm font-medium bg-cyan-600 text-white hover:bg-cyan-500 disabled:opacity-50 transition-colors"
              >
                Start {dispatchableCount}
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
