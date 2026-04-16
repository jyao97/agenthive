import { useState, useEffect, useCallback, useRef, memo, useMemo } from "react";
import { Bell, BellOff, Link2, ChevronDown, ChevronUp } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { DndContext, closestCenter, PointerSensor, TouchSensor, useSensor, useSensors, DragOverlay } from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy, arrayMove, defaultAnimateLayoutChanges } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { restrictToVerticalAxis } from "@dnd-kit/modifiers";
import { fetchAgents, stopAgent, deleteAgent, scanAgents, wakeSyncAll, searchMessages, markAgentRead, updateNotificationSettings, fetchUnlinkedSessions, adoptUnlinkedSession, reorderAgents } from "../lib/api";
import { relativeTime } from "../lib/formatters";
import { POLL_INTERVAL, modelDisplayName } from "../lib/constants";
import PageHeader from "../components/PageHeader";
import FilterTabs from "../components/FilterTabs";
import useDraft from "../hooks/useDraft";
import useWebSocket, { useWsEvent, isAgentNotificationsEnabled, setAgentNotificationsEnabled } from "../hooks/useWebSocket";
import usePageVisible from "../hooks/usePageVisible";
import { useToast } from "../contexts/ToastContext";

const FILTER_TABS = [
  { key: "ALL", label: "All" },
  { key: "ACTIVE", label: "Active" },
  { key: "INSIGHTS", label: "Insights" },
  { key: "STOPPED", label: "Stopped" },
];

function noDropAnimation(args) {
  if (args.wasDragging) return false;
  return defaultAnimateLayoutChanges(args);
}

function SortableAgentRow(props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: props.agent.id,
    animateLayoutChanges: noDropAnimation,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.3 : 1,
    WebkitUserSelect: "none",
    userSelect: "none",
  };
  return (
    <div ref={setNodeRef} style={style}>
      <AgentRow {...props} dragHandleProps={{ listeners, attributes }} />
    </div>
  );
}

const AgentRow = memo(function AgentRow({ agent, onClick, selecting, selected, onToggle, dragHandleProps }) {
  const navigate = useNavigate();

  const handleClick = () => {
    if (selecting) {
      onToggle(agent.id);
    } else {
      onClick();
    }
  };

  return (
    <button
      type="button"
      data-agent-id={agent.id}
      data-unread={agent.unread_count > 0 ? "1" : undefined}
      onClick={handleClick}
      className={`w-full text-left rounded-2xl bg-surface shadow-card overflow-hidden transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover ${
        selecting && selected ? "ring-1 ring-cyan-500" : ""
      }`}
    >
      <div className="flex items-start gap-3 px-5 py-[18px]">
      {/* Status ring — doubles as drag handle */}
      {dragHandleProps && (
        <button
          type="button"
          {...dragHandleProps.listeners}
          {...dragHandleProps.attributes}
          className="touch-none -ml-1 mr-0 rounded-full cursor-grab active:cursor-grabbing self-center"
          onClick={(e) => e.stopPropagation()}
        >
          <div className={`w-2.5 h-2.5 rounded-full ${
            agent.status === "EXECUTING" ? "bg-cyan-400 animate-breathe"
              : agent.status === "IDLE" ? "bg-emerald-300/70"
              : agent.status === "ERROR" ? "bg-red-400"
              : "bg-zinc-500"
          }`} />
        </button>
      )}
      {/* Selection checkbox */}
      {selecting && (
        <div className="shrink-0 flex items-center justify-center w-6 h-6 mt-0.5">
          <div
            className={`w-[22px] h-[22px] rounded-full border-2 flex items-center justify-center transition-colors ${
              selected
                ? "bg-cyan-500 border-cyan-500"
                : "border-edge bg-transparent"
            }`}
          >
            {selected && (
              <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
        </div>
      )}

      <div className="min-w-0 flex-1">
        {/* Title + time */}
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-base font-medium leading-snug text-heading truncate">
            {agent.name}
          </h3>
          <span className="text-[11px] text-faint shrink-0 mt-0.5">
            {agent.last_message_at ? relativeTime(agent.last_message_at) : ""}
          </span>
        </div>
        {/* Preview */}
        <p className="text-sm text-dim mt-1 truncate">
          {agent.last_message_preview || "No messages yet"}
        </p>
        {/* Tags — same order as InboxCard, status shown by left strip */}
        <div className="flex flex-wrap items-center gap-1 mt-1.5">
          <span
            className="text-[10px] text-cyan-400 font-medium px-1.5 py-px rounded-full bg-cyan-500/15 truncate cursor-pointer hover:bg-cyan-500/25 transition-colors"
            onClick={(e) => { e.stopPropagation(); navigate(`/projects/${encodeURIComponent(agent.project)}`); }}
            title={agent.project}
          >{agent.project}</span>
          {agent.worktree && (
            <span className="text-[10px] font-medium px-1.5 py-px rounded-full bg-purple-500/15 text-purple-500 dark:text-purple-400 flex items-center gap-0.5">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
              </svg>
              Worktree
            </span>
          )}
          {agent.skip_permissions && (
            <span className="text-[10px] font-medium px-1.5 py-px rounded-full bg-amber-500/15 text-amber-500 dark:text-amber-400">
              Auto
            </span>
          )}
          {agent.model && (
            <span className="text-[10px] text-dim font-medium px-1.5 py-px rounded-full bg-elevated">
              {modelDisplayName(agent.model)}
            </span>
          )}
          {agent.effort && (
            <span className="text-[10px] text-dim font-medium px-1.5 py-px rounded-full bg-elevated">
              {agent.effort.charAt(0).toUpperCase() + agent.effort.slice(1)}
            </span>
          )}
          {agent.has_pending_suggestions && (
            <span className="text-[10px] font-semibold px-1.5 py-px rounded-full bg-amber-500/15 text-amber-500 dark:text-amber-400">
              insights
            </span>
          )}
          {agent.insight_status === "failed" && !agent.has_pending_suggestions && (
            <span className="text-[10px] font-semibold px-1.5 py-px rounded-full bg-red-500/15 text-red-500 dark:text-red-400">
              failed
            </span>
          )}
          {agent.insight_status === "generating" && !agent.has_pending_suggestions && (
            <span className="text-[10px] font-semibold px-1.5 py-px rounded-full bg-blue-500/15 text-blue-400 animate-pulse">
              generating
            </span>
          )}
          {agent.unread_count > 0 && (
            <span className="text-[10px] font-bold px-1.5 py-px rounded-full bg-cyan-500 text-white">
              {agent.unread_count}
            </span>
          )}
        </div>
      </div>
      </div>
    </button>
  );
});

export default function AgentsPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const visible = usePageVisible();
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useDraft("ui:agents:filter", "ALL");
  const [search, setSearch] = useDraft("ui:agents:search", "");
  const pollRef = useRef(null);

  // Multi-select state
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [bulkStopping, setBulkStopping] = useState(false);
  const toast = useToast();

  const showToast = useCallback((message, type = "success") => {
    if (type === "error") toast.error(message);
    else toast.success(message);
  }, [toast]);

  // Notification toggle
  const [agentNotifsOn, setAgentNotifsOn] = useState(() => isAgentNotificationsEnabled());

  const handleToggleAgentNotifs = useCallback(() => {
    const next = !agentNotifsOn;
    setAgentNotifsOn(next);
    setAgentNotificationsEnabled(next);
    updateNotificationSettings({ agents_enabled: next }).catch(() => console.warn("Failed to update notification settings"));
    showToast(next ? "Agent notifications enabled" : "Agent notifications disabled");
    window.dispatchEvent(new CustomEvent("agent-notifs-changed", { detail: { enabled: next } }));
  }, [agentNotifsOn, showToast]);

  // Message content search
  const [searchResults, setSearchResults] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchTimerRef = useRef(null);

  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    const q = search.trim();
    if (q.length < 2) {
      setSearchResults(null);
      setSearchLoading(false);
      return;
    }
    setSearchLoading(true);
    searchTimerRef.current = setTimeout(() => {
      searchMessages(q)
        .then((data) => {
          setSearchResults(data);
          setSearchLoading(false);
        })
        .catch((err) => {
          console.error('searchMessages failed:', err);
          setSearchResults(null);
          setSearchLoading(false);
        });
    }, 300);
    return () => { if (searchTimerRef.current) clearTimeout(searchTimerRef.current); };
  }, [search]);

  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetchAgents();
      setAgents(Array.isArray(data) ? data : []);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // --- Unlinked sessions ---
  const [unlinked, setUnlinked] = useState([]);
  const [unlinkedOpen, setUnlinkedOpen] = useState(true);
  const [adoptingId, setAdoptingId] = useState(null);

  const loadUnlinked = useCallback(async () => {
    try {
      const data = await fetchUnlinkedSessions();
      setUnlinked(Array.isArray(data) ? data : []);
    } catch {
      // Silently ignore — not critical
    }
  }, []);

  const handleAdopt = useCallback(async (session) => {
    const fileKey = (session.file || "").replace(/\.json$/, "") || session.session_id;
    setAdoptingId(fileKey);
    try {
      await adoptUnlinkedSession(fileKey, {
        project: session.project_name,
      });
      showToast(`Session confirmed → syncing ${session.project_name}`);
      setUnlinked((prev) => prev.filter((s) => s !== session));
      load(); // Refresh agent list
      window.dispatchEvent(new CustomEvent("agents-data-changed"));
    } catch (err) {
      showToast(err.message || "Failed to adopt session", "error");
    } finally {
      setAdoptingId(null);
    }
  }, [showToast, load]);

  // Cross-pane sync: notification toggle + data refresh
  useEffect(() => {
    const onNotifsChanged = (e) => setAgentNotifsOn(e.detail.enabled);
    const onDataChanged = () => { load(); loadUnlinked(); };
    window.addEventListener("agent-notifs-changed", onNotifsChanged);
    window.addEventListener("agents-data-changed", onDataChanged);
    return () => {
      window.removeEventListener("agent-notifs-changed", onNotifsChanged);
      window.removeEventListener("agents-data-changed", onDataChanged);
    };
  }, [load, loadUnlinked]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    // Fire scan + wake in parallel, then wait for sync loops to import
    await Promise.allSettled([scanAgents(), wakeSyncAll()]);
    // Give sync loops time to read JSONL and write to DB (like chat view's 800ms)
    await new Promise((r) => setTimeout(r, 1000));
    await load();
    setTimeout(() => setRefreshing(false), 400);
  }, [load]);

  useEffect(() => {
    if (!visible) return;
    load();
    loadUnlinked();
    pollRef.current = setInterval(() => { load(); loadUnlinked(); }, POLL_INTERVAL);
    return () => clearInterval(pollRef.current);
  }, [load, loadUnlinked, visible]);

  // Real-time status updates via WebSocket (agent_update events)
  useWsEvent(useCallback((event) => {
    if (event.type !== "agent_update") return;
    const { agent_id, status } = event.data;
    setAgents((prev) =>
      prev.map((a) => (a.id === agent_id ? { ...a, status } : a))
    );
  }, []));

  // Double-tap nav: scroll to first unread agent
  useEffect(() => {
    const handler = (e) => {
      if (e.detail?.tab !== "agents") return;
      const el = document.querySelector("[data-unread='1']");
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("ring-2", "ring-cyan-400");
      setTimeout(() => el.classList.remove("ring-2", "ring-cyan-400"), 1500);
    };
    window.addEventListener("nav-scroll-to-unread", handler);
    return () => window.removeEventListener("nav-scroll-to-unread", handler);
  }, []);

  const statusFiltered = useMemo(() =>
    filter === "ALL"
      ? agents
      : filter === "ACTIVE"
        ? agents.filter((a) => a.status !== "STOPPED")
        : filter === "INSIGHTS"
          ? agents.filter((a) => a.has_pending_suggestions || a.insight_status === "failed" || a.insight_status === "generating")
          : agents.filter((a) => a.status === "STOPPED"),
    [agents, filter]);

  // DnD state — must be before filtered memo
  const [optimisticIds, setOptimisticIds] = useState(null);
  const prevAgentsRef = useRef(agents);
  if (agents !== prevAgentsRef.current) {
    prevAgentsRef.current = agents;
    if (optimisticIds) setOptimisticIds(null);
  }
  const [activeDragId, setActiveDragId] = useState(null);

  const filtered = useMemo(() => {
    let list = search.trim()
      ? statusFiltered.filter((a) => {
          const q = search.toLowerCase();
          return (
            a.id?.toLowerCase().includes(q) ||
            a.name?.toLowerCase().includes(q) ||
            a.project?.toLowerCase().includes(q) ||
            a.last_message_preview?.toLowerCase().includes(q)
          );
        })
      : statusFiltered;
    list = [...list].sort((a, b) => {
      if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
      return new Date(b.created_at) - new Date(a.created_at);
    });
    if (optimisticIds) {
      const map = Object.fromEntries(list.map(a => [a.id, a]));
      return optimisticIds.map(id => map[id]).filter(Boolean);
    }
    return list;
  }, [statusFiltered, search, optimisticIds]);
  const dndSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 350, tolerance: 10 } }),
  );
  const handleDragStart = useCallback((event) => {
    if (navigator.vibrate) navigator.vibrate(10);
    setActiveDragId(event.active.id);
  }, []);
  const handleDragEnd = useCallback((event) => {
    const { active, over } = event;
    if (!over || active.id === over.id) { setActiveDragId(null); return; }
    const ids = filtered.map(a => a.id);
    const oldIdx = ids.indexOf(active.id);
    const newIdx = ids.indexOf(over.id);
    if (oldIdx === -1 || newIdx === -1) { setActiveDragId(null); return; }
    const newIds = arrayMove(ids, oldIdx, newIdx);
    setOptimisticIds(newIds);
    setActiveDragId(null);
    reorderAgents(newIds).then(() => load());
  }, [filtered, load]);
  const handleDragCancel = useCallback(() => setActiveDragId(null), []);

  const enterSelectMode = () => {
    setSelecting(true);
    setSelected(new Set());
  };

  const exitSelectMode = () => {
    setSelecting(false);
    setSelected(new Set());
  };

  const toggleOne = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    setSelected(new Set(filtered.map((a) => a.id)));
  };

  const deselectAll = () => {
    setSelected(new Set());
  };

  const allSelected = filtered.length > 0 && selected.size === filtered.length;

  const filterCounts = useMemo(() => ({
    ALL: agents.length,
    ACTIVE: agents.filter(a => a.status !== "STOPPED").length,
    STOPPED: agents.filter(a => a.status === "STOPPED").length,
    INSIGHTS: agents.filter(a => a.has_pending_suggestions || a.insight_status === "failed" || a.insight_status === "generating").length,
  }), [agents]);

  // Count how many selected agents are stoppable (not already stopped)
  const stoppableSelected = filtered.filter(
    (a) => selected.has(a.id) && a.status !== "STOPPED"
  );

  const unreadSelected = filtered.filter(
    (a) => selected.has(a.id) && a.unread_count > 0
  );

  const [bulkMarking, setBulkMarking] = useState(false);

  const handleBulkMarkRead = async () => {
    if (unreadSelected.length === 0) return;
    setBulkMarking(true);
    let marked = 0;
    let failed = 0;
    for (const agent of unreadSelected) {
      try {
        await markAgentRead(agent.id);
        marked++;
      } catch {
        failed++;
      }
    }
    setBulkMarking(false);
    if (failed > 0) {
      showToast(`Marked ${marked} read, failed ${failed}`, "error");
    } else {
      showToast(`Marked ${marked} agent${marked !== 1 ? "s" : ""} as read`);
    }
    setSelected(new Set());
    setSelecting(false);
    load();
    window.dispatchEvent(new CustomEvent("agents-data-changed"));
  };

  const handleBulkStop = async () => {
    if (stoppableSelected.length === 0) return;
    setBulkStopping(true);
    let stopped = 0;
    let failed = 0;
    for (const agent of stoppableSelected) {
      try {
        await stopAgent(agent.id);
        stopped++;
      } catch {
        failed++;
      }
    }
    setBulkStopping(false);
    if (failed > 0) {
      showToast(`Stopped ${stopped}, failed ${failed}`, "error");
    } else {
      showToast(`Stopped ${stopped} agent${stopped !== 1 ? "s" : ""}`);
    }
    setSelected(new Set());
    setSelecting(false);
    load();
    window.dispatchEvent(new CustomEvent("agents-data-changed"));
  };

  const deletableSelected = filtered.filter(
    (a) => selected.has(a.id) && (a.status === "STOPPED" || a.status === "ERROR")
  );

  const [bulkDeleting, setBulkDeleting] = useState(false);

  const handleBulkDelete = async () => {
    if (deletableSelected.length === 0) return;
    if (!confirm(`Permanently delete ${deletableSelected.length} agent${deletableSelected.length !== 1 ? "s" : ""} and all their messages? This cannot be undone.`)) return;
    setBulkDeleting(true);
    let deleted = 0;
    let failed = 0;
    for (const agent of deletableSelected) {
      try {
        await deleteAgent(agent.id);
        deleted++;
      } catch {
        failed++;
      }
    }
    setBulkDeleting(false);
    if (failed > 0) {
      showToast(`Deleted ${deleted}, failed ${failed}`, "error");
    } else {
      showToast(`Deleted ${deleted} agent${deleted !== 1 ? "s" : ""}`);
    }
    setSelected(new Set());
    setSelecting(false);
    load();
    window.dispatchEvent(new CustomEvent("agents-data-changed"));
  };

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Agents"
        theme={theme}
        onToggleTheme={onToggleTheme}
        actions={!selecting ? (
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={handleToggleAgentNotifs}
              title={agentNotifsOn ? "Mute all agent notifications" : "Unmute all agent notifications"}
              className={`w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors ${agentNotifsOn ? "text-cyan-400" : "text-dim"}`}
            >
              {agentNotifsOn ? (
                <Bell className="w-4 h-4" />
              ) : (
                <BellOff className="w-4 h-4" />
              )}
            </button>
            <button
              type="button"
              onClick={handleRefresh}
              title="Refresh"
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
            >
              <svg className={`w-4 h-4 text-label ${refreshing ? "animate-spin" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>
        ) : undefined}
        selectAction={!selecting && agents.length > 0 ? (
          <button
            type="button"
            onClick={enterSelectMode}
            title="Select agents"
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-dim hover:text-heading hover:bg-input transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
            </svg>
          </button>
        ) : undefined}
      >
        {!selecting ? (
          <FilterTabs
            tabs={FILTER_TABS}
            active={filter}
            onChange={setFilter}
            counts={filterCounts}
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

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <div className="max-w-2xl mx-auto w-full">
      {/* Search bar */}
      <div className="px-4 pt-3 pb-1">
        <div className="relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-faint pointer-events-none" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="8" />
            <path strokeLinecap="round" d="m21 21-4.35-4.35" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search agents & messages..."
            className="w-full h-9 pl-9 pr-8 rounded-lg bg-surface border border-divider text-sm text-body placeholder-hint focus:outline-none focus:ring-1 focus:ring-cyan-500"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-faint hover:text-label transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" d="M6 18 18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Message search results */}
      {search.trim().length >= 2 && (
        <div className="px-4 py-2">
          {searchLoading && (
            <p className="text-xs text-dim animate-pulse">Searching messages...</p>
          )}
          {searchResults && searchResults.results && searchResults.results.length > 0 && (
            <div className="space-y-1 mb-2">
              <p className="text-xs text-dim font-medium">
                {searchResults.total} message{searchResults.total !== 1 ? "s" : ""} found
              </p>
              {/* Group results by agent */}
              {Object.entries(
                searchResults.results.reduce((acc, r) => {
                  const key = r.agent_id;
                  if (!acc[key]) acc[key] = { agent_name: r.agent_name, project: r.project, items: [] };
                  acc[key].items.push(r);
                  return acc;
                }, {})
              ).map(([agentId, group]) => (
                <div key={agentId} className="rounded-lg bg-surface border border-divider overflow-hidden">
                  <button
                    type="button"
                    onClick={() => navigate(`/agents/${agentId}`)}
                    className="w-full text-left px-3 py-2 bg-elevated hover:bg-hover transition-colors"
                  >
                    <span className="text-xs font-semibold text-heading">{group.agent_name}</span>
                    <span className="text-[10px] text-dim ml-2">{group.project}</span>
                    <span className="text-[10px] text-faint ml-1">({group.items.length})</span>
                  </button>
                  {group.items.slice(0, 3).map((r) => (
                    <button
                      key={r.message_id}
                      type="button"
                      onClick={() => navigate(`/agents/${r.agent_id}`)}
                      className="w-full text-left px-3 py-1.5 border-t border-divider hover:bg-hover transition-colors"
                    >
                      <p className="text-xs text-body line-clamp-2">{r.content_snippet}</p>
                      <span className="text-[10px] text-faint">{relativeTime(r.created_at)}</span>
                    </button>
                  ))}
                  {group.items.length > 3 && (
                    <button
                      type="button"
                      onClick={() => navigate(`/agents/${agentId}`)}
                      className="w-full text-left px-3 py-1 border-t border-divider text-[10px] text-cyan-400 hover:text-cyan-300 transition-colors"
                    >
                      +{group.items.length - 3} more
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
          {searchResults && searchResults.results && searchResults.results.length === 0 && !searchLoading && (
            <p className="text-xs text-dim">No messages match "{search.trim()}"</p>
          )}
        </div>
      )}

      {/* Unlinked sessions banner */}
      {unlinked.length > 0 && !selecting && (
        <div className="mx-4 mt-2 rounded-xl bg-surface border border-edge overflow-hidden">
          <button
            type="button"
            onClick={() => setUnlinkedOpen((v) => !v)}
            className="w-full flex items-center gap-2 px-4 py-2.5 text-left hover:bg-hover transition-colors"
          >
            <Link2 className="w-4 h-4 text-violet-500 dark:text-violet-400 shrink-0" />
            <span className="text-sm font-medium text-violet-600 dark:text-violet-300 flex-1">
              {unlinked.length} session{unlinked.length !== 1 ? "s" : ""} detected
            </span>
            {unlinkedOpen
              ? <ChevronUp className="w-4 h-4 text-faint" />
              : <ChevronDown className="w-4 h-4 text-faint" />
            }
          </button>
          {unlinkedOpen && (
            <div className="border-t border-divider divide-y divide-divider">
              {unlinked.map((s) => {
                const fk = (s.file || "").replace(/\.json$/, "") || s.session_id;
                return (
                  <div key={fk} className="px-4 py-2.5 flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-heading truncate">
                          {s.tmux_session || s.project_name || "unknown"}
                        </span>
                        <span className="text-xs text-faint shrink-0">
                          {s.project_name}
                        </span>
                      </div>
                      <p className="text-xs text-dim truncate mt-0.5">
                        {s.session_id ? `${s.session_id.slice(0, 12)}… · ` : ""}pane {s.tmux_pane || "?"}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleAdopt(s)}
                      disabled={adoptingId === fk}
                      className="shrink-0 px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-xs font-medium transition-colors disabled:opacity-50"
                    >
                      {adoptingId === fk ? "Linking…" : "Confirm"}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Agent list */}
      <div className={`${selecting ? "pb-32" : "pb-24"} px-4 py-2 space-y-3`}>
        {loading && agents.length === 0 && (
          <div className="flex justify-center py-12">
            <span className="text-dim text-sm animate-pulse">Loading agents...</span>
          </div>
        )}

        {error && (
          <div className="bg-red-950/40 border border-red-800 rounded-xl p-4">
            <p className="text-red-400 text-sm">Failed to fetch agents: {error}</p>
            <button type="button" onClick={load} className="mt-2 text-xs text-red-300 underline hover:text-red-200">
              Retry
            </button>
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-faint">
            <svg className="w-12 h-12 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            <p className="text-sm">No agents yet</p>
            <p className="text-xs mt-1 text-ghost">Create one from the New tab</p>
          </div>
        )}

        <DndContext
          sensors={dndSensors}
          collisionDetection={closestCenter}
          modifiers={[restrictToVerticalAxis]}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDragCancel={handleDragCancel}
        >
          <SortableContext items={filtered.map(a => a.id)} strategy={verticalListSortingStrategy}>
            <div className="space-y-3">
              {filtered.map((agent) => (
                <SortableAgentRow
                  key={agent.id}
                  agent={agent}
                  onClick={() => navigate(`/agents/${agent.id}`)}
                  selecting={selecting}
                  selected={selected.has(agent.id)}
                  onToggle={toggleOne}
                />
              ))}
            </div>
          </SortableContext>
          <DragOverlay dropAnimation={null}>
            {activeDragId && filtered.find(a => a.id === activeDragId) ? (
              <div className="opacity-90 scale-[1.02] shadow-xl rounded-2xl">
                <AgentRow
                  agent={filtered.find(a => a.id === activeDragId)}
                  onClick={() => {}}
                  selecting={false}
                  selected={false}
                  onToggle={() => {}}
                />
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>

        <div className="h-4" />
      </div>
      </div>
      </div>

      {/* Bottom toolbar in selection mode */}
      {selecting && selected.size > 0 && (
        <div className="fixed bottom-20 left-0 right-0 z-20 px-4 pb-2">
          <div className="max-w-xl mx-auto bg-surface border border-divider rounded-xl shadow-lg p-3 flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={handleBulkMarkRead}
              disabled={bulkMarking || unreadSelected.length === 0}
              className="flex-1 flex items-center justify-center gap-2 min-h-[40px] rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 18h10a2 2 0 002-2V8H3v8a2 2 0 002 2zM17 8h2a2 2 0 010 4h-2M8 2v3M12 2v3" />
              </svg>
              {bulkMarking
                ? "Marking..."
                : unreadSelected.length === 0
                  ? "Read"
                  : `Read ${unreadSelected.length}`
              }
            </button>
            <button
              type="button"
              onClick={handleBulkStop}
              disabled={bulkStopping || stoppableSelected.length === 0}
              className="flex-1 flex items-center justify-center gap-2 min-h-[40px] rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
              {bulkStopping
                ? "Stopping..."
                : stoppableSelected.length === 0
                  ? "Stop"
                  : `Stop ${stoppableSelected.length}`
              }
            </button>
            <button
              type="button"
              onClick={handleBulkDelete}
              disabled={bulkDeleting || deletableSelected.length === 0}
              className="flex-1 flex items-center justify-center gap-2 min-h-[40px] rounded-lg bg-red-900 hover:bg-red-800 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              {bulkDeleting
                ? "Deleting..."
                : deletableSelected.length === 0
                  ? "Delete"
                  : `Delete ${deletableSelected.length}`
              }
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
