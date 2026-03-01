import { useState, useEffect, useCallback, useMemo, memo } from "react";
import { useNavigate, useNavigationType } from "react-router-dom";
import { DndContext, closestCenter, PointerSensor, TouchSensor, useSensor, useSensors, DragOverlay } from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy, arrayMove } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { fetchAllFolders, fetchTrashFolders, createProject, archiveProject, scanProjects } from "../lib/api";
import { relativeTime } from "../lib/formatters";
import BotIcon from "../components/BotIcon";
import PageHeader from "../components/PageHeader";
import FilterTabs from "../components/FilterTabs";
import useDraft from "../hooks/useDraft";
import usePageVisible from "../hooks/usePageVisible";

function botState(folder) {
  if (!folder.active) return "idle";
  if ((folder.agent_active || 0) > 0) return "running";
  if (folder.agent_count > 0) return "completed";
  return "idle";
}

function DragHandle({ listeners, attributes }) {
  return (
    <button
      type="button"
      {...listeners}
      {...attributes}
      className="touch-none p-1 -ml-2 mr-1 rounded text-faint hover:text-label transition-colors cursor-grab active:cursor-grabbing"
      onClick={(e) => e.stopPropagation()}
    >
      <svg className="w-4 h-4" viewBox="0 0 16 16" fill="currentColor">
        <circle cx="5" cy="3" r="1.5" /><circle cx="11" cy="3" r="1.5" />
        <circle cx="5" cy="8" r="1.5" /><circle cx="11" cy="8" r="1.5" />
        <circle cx="5" cy="13" r="1.5" /><circle cx="11" cy="13" r="1.5" />
      </svg>
    </button>
  );
}

function SortableFolderCard(props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: props.folder.name });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };
  return (
    <div ref={setNodeRef} style={style}>
      <FolderCard {...props} dragHandleProps={{ listeners, attributes }} />
    </div>
  );
}

const FolderCard = memo(function FolderCard({ folder, onClick, onActivate, onArchive, busy, dragHandleProps }) {
  const state = botState(folder);

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left rounded-xl bg-surface shadow-card p-5 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      <div className="flex items-start gap-4">
        {dragHandleProps && <DragHandle {...dragHandleProps} />}
        <BotIcon state={state} className="w-10 h-10 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold text-heading truncate">
              {folder.display_name || folder.name}
            </h3>
            {folder.active ? (
              <span className="shrink-0 px-2 py-0.5 text-[10px] font-bold uppercase rounded-full bg-emerald-500/15 text-emerald-400 tracking-wide">
                Active
              </span>
            ) : (
              <span className="shrink-0 px-2 py-0.5 text-[10px] font-bold uppercase rounded-full bg-zinc-500/15 text-zinc-400 tracking-wide">
                Inactive
              </span>
            )}
            {folder.process_running && (
              <span className="shrink-0 w-2 h-2 rounded-full bg-emerald-400 animate-pulse" title="Processes active" />
            )}
          </div>
          {folder.git_remote && (
            <p className="text-xs text-dim truncate mt-0.5">{folder.git_remote}</p>
          )}
        </div>
      </div>

      {/* Stats row */}
      <div className="flex items-center gap-4 mt-4 text-xs">
        {folder.agent_count > 0 && (
          <span className="text-label">
            <span className="font-medium text-heading">{folder.agent_count}</span> agent{folder.agent_count !== 1 ? "s" : ""}
          </span>
        )}
        {folder.active && (folder.agent_active || 0) > 0 && (
          <span className="text-cyan-400">{folder.agent_active} active</span>
        )}
        {folder.active && (folder.task_total || 0) > 0 && (
          <span className="text-label">
            <span className="font-medium text-heading">{folder.task_total}</span> tasks
          </span>
        )}
        {!folder.active && folder.agent_count === 0 && (
          <span className="text-dim">No history</span>
        )}
        <span className="ml-auto flex items-center gap-2">
          {folder.last_activity && (
            <span className="text-dim">{relativeTime(folder.last_activity)}</span>
          )}
          {/* Activate / Archive button */}
          {!folder.active ? (
            <button
              type="button"
              disabled={busy}
              onClick={(e) => { e.stopPropagation(); onActivate(folder.name); }}
              className="px-2.5 py-1 text-[11px] font-semibold rounded-lg bg-cyan-600 text-white hover:bg-cyan-500 disabled:opacity-50 transition-colors"
            >
              {busy ? "..." : "Activate"}
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={(e) => { e.stopPropagation(); onArchive(folder.name); }}
              className="px-2.5 py-1 text-[11px] font-semibold rounded-lg bg-amber-600/20 text-amber-400 hover:bg-amber-600/30 disabled:opacity-50 transition-colors"
            >
              {busy ? "..." : "Archive"}
            </button>
          )}
        </span>
      </div>
    </button>
  );
});

const SORT_OPTIONS = [
  { value: "custom", label: "Custom" },
  { value: "name-asc", label: "Name A-Z" },
  { value: "name-desc", label: "Name Z-A" },
  { value: "updated-new", label: "Newest" },
  { value: "updated-old", label: "Oldest" },
];

export default function ProjectsPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const navType = useNavigationType();
  const visible = usePageVisible();
  const [folders, setFolders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null); // folder name currently being toggled
  const [refreshing, setRefreshing] = useState(false);
  const [scanResult, setScanResult] = useState(null);
  const [filter, setFilter] = useDraft("ui:projects:filter", "ALL");
  const [trashCount, setTrashCount] = useState(0);
  const [sortMode, setSortMode] = useState(() => localStorage.getItem("projects-sort-mode") || "custom");
  const [customOrder, setCustomOrder] = useState(() => {
    try { return JSON.parse(localStorage.getItem("projects-custom-order")) || []; }
    catch { return []; }
  });
  const [activeDragId, setActiveDragId] = useState(null);
  // Pre-compute whether we'll auto-navigate so the first render returns null (no flash)
  const [autoNavigating, setAutoNavigating] = useState(
    () => navType !== "POP" && !!localStorage.getItem("lastViewed:projects")
  );

  const load = useCallback(async () => {
    try {
      const [data, trash] = await Promise.all([
        fetchAllFolders(),
        fetchTrashFolders(),
      ]);
      setFolders(data);
      setTrashCount(trash.length);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleActivate = async (name) => {
    setBusy(name);
    try {
      await createProject({ name });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const handleArchive = async (name) => {
    setBusy(name);
    try {
      await archiveProject(name);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    if (!visible) return;
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load, visible]);

  // Auto-navigate to last-viewed project on tab switch.
  // POP = browser back / swipe-back → clear memory and stay on list.
  // PUSH/REPLACE = tab click → push detail so back gesture returns to list.
  //
  // CRITICAL: The push must be deferred via requestAnimationFrame so that
  // the NavLink replace (tab switch → /projects) commits to browser history
  // BEFORE the push to /projects/:name.  In React Router v7 + React 19,
  // a navigate() inside useEffect can supersede a pending replace from the
  // same render cycle, resulting in /projects never entering the history
  // stack (swipe-back skips straight to the previous tab).
  useEffect(() => {
    if (navType === "POP") {
      localStorage.removeItem("lastViewed:projects");
      setAutoNavigating(false);
      return;
    }
    const last = localStorage.getItem("lastViewed:projects");
    if (last) {
      // Defer push to next animation frame so the /projects replace commits first
      const id = requestAnimationFrame(() => {
        navigate(`/projects/${encodeURIComponent(last)}`);
      });
      return () => cancelAnimationFrame(id);
    }
    setAutoNavigating(false);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync customOrder when folders load — append any new projects
  useEffect(() => {
    if (folders.length === 0) return;
    setCustomOrder((prev) => {
      const names = folders.map((f) => f.name);
      const existing = prev.filter((n) => names.includes(n));
      const newNames = names.filter((n) => !prev.includes(n)).sort();
      if (newNames.length === 0 && existing.length === prev.length) return prev;
      const updated = [...existing, ...newNames];
      localStorage.setItem("projects-custom-order", JSON.stringify(updated));
      return updated;
    });
  }, [folders]);

  const handleSortChange = useCallback((value) => {
    setSortMode(value);
    localStorage.setItem("projects-sort-mode", value);
  }, []);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 5 } }),
  );

  const handleDragStart = useCallback((event) => setActiveDragId(event.active.id), []);
  const handleDragEnd = useCallback((event) => {
    setActiveDragId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    setCustomOrder((prev) => {
      const oldIdx = prev.indexOf(active.id);
      const newIdx = prev.indexOf(over.id);
      if (oldIdx === -1 || newIdx === -1) return prev;
      const updated = arrayMove(prev, oldIdx, newIdx);
      localStorage.setItem("projects-custom-order", JSON.stringify(updated));
      return updated;
    });
  }, []);
  const handleDragCancel = useCallback(() => setActiveDragId(null), []);

  const activeCount = folders.filter((f) => f.active).length;
  const inactiveCount = folders.filter((f) => !f.active).length;

  const filtered = useMemo(() => {
    const base = folders.filter((f) => filter === "ALL" || (filter === "ACTIVE" ? f.active : !f.active));
    switch (sortMode) {
      case "name-asc":
        return [...base].sort((a, b) => (a.display_name || a.name).localeCompare(b.display_name || b.name));
      case "name-desc":
        return [...base].sort((a, b) => (b.display_name || b.name).localeCompare(a.display_name || a.name));
      case "updated-new":
        return [...base].sort((a, b) => {
          if (!a.last_activity && !b.last_activity) return 0;
          if (!a.last_activity) return 1;
          if (!b.last_activity) return -1;
          return new Date(b.last_activity) - new Date(a.last_activity);
        });
      case "updated-old":
        return [...base].sort((a, b) => {
          if (!a.last_activity && !b.last_activity) return 0;
          if (!a.last_activity) return 1;
          if (!b.last_activity) return -1;
          return new Date(a.last_activity) - new Date(b.last_activity);
        });
      case "custom":
      default:
        return [...base].sort((a, b) => {
          const ai = customOrder.indexOf(a.name);
          const bi = customOrder.indexOf(b.name);
          if (ai === -1 && bi === -1) return a.name.localeCompare(b.name);
          if (ai === -1) return 1;
          if (bi === -1) return -1;
          return ai - bi;
        });
    }
  }, [folders, filter, sortMode, customOrder]);

  const FILTER_TABS = [
    { key: "ALL", label: "All" },
    { key: "ACTIVE", label: "Active" },
    { key: "INACTIVE", label: "Inactive" },
  ];

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setScanResult(null);
    try {
      const result = await scanProjects();
      await load();
      if (result.added?.length > 0) {
        setScanResult(result);
        setTimeout(() => setScanResult(null), 4000);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setTimeout(() => setRefreshing(false), 400);
    }
  }, [load]);

  const headerButtons = (
    <button
      type="button"
      onClick={handleRefresh}
      disabled={refreshing}
      title="Refresh & scan"
      className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors disabled:opacity-50"
    >
      <svg className={`w-4 h-4 text-label ${refreshing ? "animate-spin" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
      </svg>
    </button>
  );

  const sortDropdown = (
    <select
      value={sortMode}
      onChange={(e) => handleSortChange(e.target.value)}
      className="w-full h-[36px] pl-3.5 pr-5 text-sm font-medium leading-[36px] rounded-full bg-surface text-label border-none outline-none appearance-none text-center"
      style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239ca3af' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E\")", backgroundRepeat: "no-repeat", backgroundPosition: "right 6px center" }}
    >
      {SORT_OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );

  // Prevent flash of list page while auto-navigating to remembered project
  if (autoNavigating) return null;

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Projects" theme={theme} onToggleTheme={onToggleTheme} actions={headerButtons}>
        <div className="flex items-center px-4 pb-3 gap-1.5">
          <div className="flex gap-1.5 overflow-x-auto no-scrollbar min-w-0">
            {FILTER_TABS.map((tab) => {
              const isActive = filter === tab.key;
              const count = { ALL: folders.length, ACTIVE: activeCount, INACTIVE: inactiveCount }[tab.key];
              return (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setFilter(tab.key)}
                  className={`shrink-0 min-h-[36px] px-3 py-1.5 rounded-full text-sm font-medium transition-colors whitespace-nowrap ${
                    isActive
                      ? "bg-cyan-600 text-white"
                      : "bg-surface text-label hover:bg-input hover:text-body"
                  }`}
                >
                  {tab.label}
                  {count != null && (
                    <span className={`ml-1.5 text-xs ${isActive ? "text-cyan-200" : "text-faint"}`}>
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="ml-auto max-w-[7rem]">{sortDropdown}</div>
        </div>
      </PageHeader>
      {/* Scan result toast */}
      {scanResult && (
        <div className="fixed left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium safe-area-toast bg-cyan-600 text-white">
          {scanResult.added.length > 0
            ? `Added ${scanResult.added.length} project${scanResult.added.length !== 1 ? "s" : ""}: ${scanResult.added.join(", ")}`
            : `Scanned ${scanResult.scanned} folders — no new projects`}
          {scanResult.skipped_archived?.length > 0 &&
            ` (${scanResult.skipped_archived.length} archived skipped)`}
        </div>
      )}

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <div className="pb-20 p-4 max-w-2xl mx-auto w-full">

      {loading && folders.length === 0 && (
        <div className="flex justify-center py-12">
          <span className="text-dim text-sm animate-pulse">Loading projects...</span>
        </div>
      )}

      {error && (
        <div className="bg-red-950/40 border border-red-800 rounded-xl p-4 mb-4">
          <p className="text-red-400 text-sm">Failed to fetch projects: {error}</p>
          <button type="button" onClick={load} className="mt-2 text-xs text-red-300 underline hover:text-red-200">
            Retry
          </button>
        </div>
      )}

      {!loading && !error && filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-faint">
          <svg className="w-12 h-12 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
          </svg>
          <p className="text-sm">
            {folders.length === 0 ? "No project folders found" : `No ${filter.toLowerCase()} projects`}
          </p>
        </div>
      )}

      {sortMode === "custom" ? (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDragCancel={handleDragCancel}
        >
          <SortableContext items={filtered.map((f) => f.name)} strategy={verticalListSortingStrategy}>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {filtered.map((folder) => (
                <SortableFolderCard
                  key={folder.name}
                  folder={folder}
                  onClick={() => navigate(`/projects/${encodeURIComponent(folder.name)}`)}
                  onActivate={handleActivate}
                  onArchive={handleArchive}
                  busy={busy === folder.name}
                />
              ))}
            </div>
          </SortableContext>
          <DragOverlay>
            {activeDragId ? (
              <div className="opacity-90 scale-105 shadow-xl rounded-xl">
                <FolderCard
                  folder={filtered.find((f) => f.name === activeDragId)}
                  onClick={() => {}}
                  onActivate={() => {}}
                  onArchive={() => {}}
                  busy={false}
                />
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {filtered.map((folder) => (
            <FolderCard
              key={folder.name}
              folder={folder}
              onClick={() => navigate(`/projects/${encodeURIComponent(folder.name)}`)}
              onActivate={handleActivate}
              onArchive={handleArchive}
              busy={busy === folder.name}
            />
          ))}
        </div>
      )}

      {trashCount > 0 && (
        <button
          type="button"
          onClick={() => navigate("/projects/trash")}
          className="block mx-auto mt-8 text-sm text-faint hover:text-dim transition-colors"
        >
          Deleted projects ({trashCount})
        </button>
      )}
      </div>
      </div>
    </div>
  );
}
