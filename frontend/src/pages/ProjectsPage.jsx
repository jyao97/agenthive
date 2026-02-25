import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { fetchAllFolders, fetchTrashFolders, createProject, archiveProject } from "../lib/api";
import { relativeTime } from "../lib/formatters";
import BotIcon from "../components/BotIcon";
import PageHeader from "../components/PageHeader";

function botState(folder) {
  if (!folder.active) return "idle";
  if ((folder.agent_active || 0) > 0) return "running";
  if (folder.agent_count > 0) return "completed";
  return "idle";
}

function FolderCard({ folder, onClick, onActivate, onArchive, busy }) {
  const state = botState(folder);

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left rounded-xl bg-surface shadow-card p-5 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      <div className="flex items-start gap-4">
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
          {folder.description && (
            <p className="text-xs text-label mt-1 line-clamp-2">{folder.description}</p>
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
}

export default function ProjectsPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const [folders, setFolders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null); // folder name currently being toggled
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState("ALL");
  const [trashCount, setTrashCount] = useState(0);

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
      setRefreshing(false);
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
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load]);

  const activeCount = folders.filter((f) => f.active).length;
  const inactiveCount = folders.filter((f) => !f.active).length;

  const filtered = folders
    .filter((f) => filter === "ALL" || (filter === "ACTIVE" ? f.active : !f.active))
    .sort((a, b) => {
      if (a.active !== b.active) return a.active ? -1 : 1;
      return a.name.localeCompare(b.name);
    });

  const FILTER_TABS = [
    { key: "ALL", label: "All", count: folders.length },
    { key: "ACTIVE", label: "Active", count: activeCount },
    { key: "INACTIVE", label: "Inactive", count: inactiveCount },
  ];

  const refreshButton = (
    <button
      type="button"
      onClick={() => { setRefreshing(true); load(); }}
      title="Refresh"
      className="p-2 rounded-lg text-label hover:text-heading hover:bg-input transition-colors"
    >
      <svg className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 1 1-2.64-6.36L21 3v5h-5" />
      </svg>
    </button>
  );

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Projects" theme={theme} onToggleTheme={onToggleTheme} actions={refreshButton}>
        <div className="flex gap-1 px-4 pb-3">
          {FILTER_TABS.map((tab) => {
            const isActive = filter === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setFilter(tab.key)}
                className={`min-h-[36px] px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-cyan-600 text-white"
                    : "bg-surface text-label hover:bg-input hover:text-body"
                }`}
              >
                {tab.label}
                <span className={`ml-1.5 text-xs ${isActive ? "text-cyan-200" : "text-faint"}`}>
                  {tab.count}
                </span>
              </button>
            );
          })}
        </div>
      </PageHeader>
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
