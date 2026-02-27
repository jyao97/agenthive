import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import FilterTabs from "../components/FilterTabs";
import {
  fetchProjects as apiFetchProjects,
  fetchGitLog,
  fetchGitBranches,
  fetchGitStatus,
  fetchGitWorktrees,
  mergeGitBranch,
  createAgent,
} from "../lib/api";

/** Format a date string into a human-readable relative time. */
function relativeTime(dateStr) {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = Math.max(0, now - then);
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(months / 12);
  return `${years}y ago`;
}

/** A small toast notification component. */
function Toast({ toast, onDismiss }) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), 4000);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  const bgColor =
    toast.type === "success"
      ? "bg-green-600/90 border-green-500"
      : "bg-red-600/90 border-red-500";

  return (
    <div
      className={`${bgColor} border rounded-lg px-4 py-3 text-sm text-white shadow-lg backdrop-blur-sm animate-slide-in`}
    >
      <div className="flex items-start gap-2">
        <span className="shrink-0 mt-0.5">
          {toast.type === "success" ? (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          )}
        </span>
        <span className="leading-snug">{toast.message}</span>
      </div>
    </div>
  );
}

export default function GitPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();

  // --- State ---
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState(null);
  const [commits, setCommits] = useState([]);
  const [branches, setBranches] = useState([]);
  const [status, setStatus] = useState(null);
  const [worktrees, setWorktrees] = useState([]);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loadingCommits, setLoadingCommits] = useState(false);
  const [loadingBranches, setLoadingBranches] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [loadingWorktrees, setLoadingWorktrees] = useState(false);
  const [mergingBranch, setMergingBranch] = useState(null);
  const [mergingAll, setMergingAll] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [error, setError] = useState(null);
  const toastIdRef = useRef(0);

  // --- Toast helpers ---
  const addToast = useCallback((message, type) => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, message, type }]);
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // --- Fetch projects ---
  useEffect(() => {
    let cancelled = false;
    async function fetchProjectsList() {
      setLoadingProjects(true);
      setError(null);
      try {
        const data = await apiFetchProjects();
        if (!cancelled) {
          setProjects(data);
          if (data.length > 0) {
            setSelectedProject(data[0].name);
          }
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoadingProjects(false);
      }
    }
    fetchProjectsList();
    return () => { cancelled = true; };
  }, []);

  // --- Fetch commits and branches when project changes ---
  useEffect(() => {
    if (!selectedProject) return;
    let cancelled = false;

    async function fetchGitData() {
      setLoadingCommits(true);
      setLoadingBranches(true);
      setLoadingStatus(true);
      setLoadingWorktrees(true);
      setCommits([]);
      setBranches([]);
      setStatus(null);
      setWorktrees([]);

      // Fetch all in parallel
      const [commitRes, branchRes, statusRes, wtRes] = await Promise.allSettled([
        fetchGitLog(selectedProject).catch(() => []),
        fetchGitBranches(selectedProject).catch(() => []),
        fetchGitStatus(selectedProject).catch(() => null),
        fetchGitWorktrees(selectedProject).catch(() => []),
      ]);

      if (!cancelled) {
        setCommits(commitRes.status === "fulfilled" ? commitRes.value : []);
        setBranches(branchRes.status === "fulfilled" ? branchRes.value : []);
        setStatus(statusRes.status === "fulfilled" ? statusRes.value : null);
        setWorktrees(wtRes.status === "fulfilled" ? wtRes.value : []);
        setLoadingCommits(false);
        setLoadingBranches(false);
        setLoadingStatus(false);
        setLoadingWorktrees(false);
      }
    }

    fetchGitData();
    return () => { cancelled = true; };
  }, [selectedProject]);

  // --- Merge handler ---
  const handleMerge = useCallback(
    async (branchName) => {
      if (!selectedProject || mergingBranch) return;
      setMergingBranch(branchName);
      try {
        const data = await mergeGitBranch(selectedProject, branchName);
        addToast(`Merged "${branchName}" successfully.`, "success");
        // Refresh commits, branches, and status
        const [newCommits, newBranches, newStatus, newWt] = await Promise.all([
          fetchGitLog(selectedProject).catch(() => []),
          fetchGitBranches(selectedProject).catch(() => []),
          fetchGitStatus(selectedProject).catch(() => null),
          fetchGitWorktrees(selectedProject).catch(() => []),
        ]);
        setCommits(newCommits);
        setBranches(newBranches);
        setStatus(newStatus);
        setWorktrees(newWt);
      } catch (err) {
        addToast(`Merge error: ${err.message}`, "error");
      } finally {
        setMergingBranch(null);
      }
    },
    [selectedProject, mergingBranch, addToast]
  );

  // --- Merge All worktrees handler ---
  const handleMergeAll = useCallback(async () => {
    if (!selectedProject || mergingAll) return;
    const nonMainWt = worktrees.filter((_, i) => i !== 0);
    if (nonMainWt.length === 0) return;

    const branchList = nonMainWt
      .map((wt) => wt.branch)
      .filter(Boolean)
      .join(", ");

    setMergingAll(true);
    try {
      const agent = await createAgent({
        project: selectedProject,
        name: `merge-worktrees`,
        mode: "AUTO",
        skip_permissions: true,
        initial_message:
          `Merge all worktree branches into main and clean up. ` +
          `The branches to merge are: ${branchList}. ` +
          `For each branch: 1) checkout main, 2) git merge <branch>, ` +
          `3) git worktree remove <path> (use --force if needed), ` +
          `4) git branch -d <branch>. ` +
          `List worktrees first with 'git worktree list' to get exact paths.`,
      });
      navigate(`/agents/${agent.id}`);
    } catch (err) {
      addToast(`Merge All error: ${err.message}`, "error");
    } finally {
      setMergingAll(false);
    }
  }, [selectedProject, mergingAll, worktrees, addToast, navigate]);

  // --- Loading skeleton for commits ---
  function CommitSkeleton() {
    return (
      <div className="space-y-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-start gap-3 animate-pulse">
            <div className="w-16 h-4 bg-skel rounded" />
            <div className="flex-1 space-y-1.5">
              <div className="h-4 bg-skel rounded w-3/4" />
              <div className="h-3 bg-skel rounded w-1/3" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  // --- Loading skeleton for branches ---
  function BranchSkeleton() {
    return (
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-8 w-28 bg-skel rounded-full animate-pulse" />
        ))}
      </div>
    );
  }

  // --- Render ---
  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Git" theme={theme} onToggleTheme={onToggleTheme}>
        {loadingProjects ? (
          <div className="flex gap-1.5 px-4 pb-3 animate-pulse">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-9 w-24 bg-skel rounded-full" />
            ))}
          </div>
        ) : projects.length > 0 ? (
          <FilterTabs
            tabs={projects.map((p) => ({ key: p.name, label: p.display_name || p.name }))}
            active={selectedProject}
            onChange={setSelectedProject}
          />
        ) : null}
      </PageHeader>
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <div className="pb-20 p-4 space-y-4">

      {/* Error state */}
      {error && (
        <div className="rounded-xl bg-red-900/30 border border-red-800 p-4 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* No projects message */}
      {!loadingProjects && projects.length === 0 && (
        <div className="rounded-xl bg-surface shadow-card p-4 text-label text-sm">
          No projects registered. Add a project to view its git history.
        </div>
      )}

      {/* Worktrees section */}
      {selectedProject && (
        <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-body uppercase tracking-wide">
              Worktrees
            </h2>
            {!loadingWorktrees && worktrees.length > 1 && (
              <button
                onClick={handleMergeAll}
                disabled={mergingAll}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  mergingAll
                    ? "bg-purple-700/50 text-purple-300 cursor-wait"
                    : "bg-purple-600/30 text-purple-300 hover:bg-purple-600/60 hover:text-white"
                }`}
              >
                {mergingAll ? (
                  <span className="flex items-center gap-1">
                    <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    Creating...
                  </span>
                ) : (
                  "Merge All"
                )}
              </button>
            )}
          </div>
          {loadingWorktrees ? (
            <div className="flex flex-wrap gap-2">
              {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="h-8 w-32 bg-skel rounded-full animate-pulse" />
              ))}
            </div>
          ) : worktrees.length <= 1 ? (
            <p className="text-sm text-dim">No additional worktrees.</p>
          ) : (
            <div className="space-y-1.5">
              {worktrees.map((wt, idx) => {
                const isMain = idx === 0;
                const name = wt.path ? wt.path.split("/").pop() : "unknown";
                return (
                  <div
                    key={wt.path || idx}
                    className={`flex items-center gap-2 rounded-lg text-sm px-3 py-2 border transition-colors ${
                      isMain
                        ? "bg-cyan-600/20 border-cyan-500/50 text-cyan-300"
                        : "bg-purple-500/10 border-purple-500/30 text-purple-300"
                    }`}
                  >
                    <svg className="w-3.5 h-3.5 shrink-0 opacity-60" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
                    </svg>
                    <span className="font-mono text-xs truncate">{name}</span>
                    {wt.branch && (
                      <span className={`text-xs px-1.5 py-0.5 rounded ${isMain ? "bg-cyan-600/30 text-cyan-200" : "bg-purple-500/20 text-purple-200"}`}>
                        {wt.branch}
                      </span>
                    )}
                    {wt.detached && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300">detached</span>
                    )}
                    {wt.commit && (
                      <span className="font-mono text-xs text-dim ml-auto">{wt.commit}</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Branches section */}
      {selectedProject && (
        <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
          <h2 className="text-sm font-semibold text-body uppercase tracking-wide">
            Branches
          </h2>
          {loadingBranches ? (
            <BranchSkeleton />
          ) : branches.length === 0 ? (
            <p className="text-sm text-dim">No branches found.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {branches.map((branch) => {
                const isCurrent = branch.current;
                const isMerging = mergingBranch === branch.name;
                return (
                  <div
                    key={branch.name}
                    className={`flex items-center gap-2 rounded-full text-sm px-3 py-1.5 border transition-colors ${
                      isCurrent
                        ? "bg-cyan-600/20 border-cyan-500/50 text-cyan-300"
                        : "bg-input border-edge text-body"
                    }`}
                  >
                    {/* Branch icon */}
                    <svg
                      className="w-3.5 h-3.5 shrink-0 opacity-60"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={2}
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z"
                      />
                    </svg>

                    <span className="font-mono text-xs truncate max-w-[160px]">
                      {branch.name}
                    </span>

                    {isCurrent && (
                      <svg
                        className="w-4 h-4 text-cyan-400 shrink-0"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth={2.5}
                        viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}

                    {!isCurrent && (
                      <button
                        onClick={() => handleMerge(branch.name)}
                        disabled={!!mergingBranch}
                        className={`ml-1 px-2 py-0.5 rounded-full text-xs font-medium transition-colors ${
                          isMerging
                            ? "bg-cyan-700/50 text-cyan-300 cursor-wait"
                            : "bg-cyan-600/30 text-cyan-300 hover:bg-cyan-600/60 hover:text-white"
                        } disabled:opacity-50 disabled:cursor-not-allowed`}
                      >
                        {isMerging ? (
                          <span className="flex items-center gap-1">
                            <svg
                              className="w-3 h-3 animate-spin"
                              fill="none"
                              viewBox="0 0 24 24"
                            >
                              <circle
                                className="opacity-25"
                                cx="12"
                                cy="12"
                                r="10"
                                stroke="currentColor"
                                strokeWidth="4"
                              />
                              <path
                                className="opacity-75"
                                fill="currentColor"
                                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                              />
                            </svg>
                            Merging
                          </span>
                        ) : (
                          "Merge"
                        )}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Working tree status */}
      {selectedProject && (
        <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
          <h2 className="text-sm font-semibold text-body uppercase tracking-wide">
            Status
          </h2>
          {loadingStatus ? (
            <div className="h-6 w-40 bg-skel rounded animate-pulse" />
          ) : !status ? (
            <p className="text-sm text-dim">Could not fetch status.</p>
          ) : status.clean ? (
            <div className="flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full bg-green-500" />
              <span className="text-sm text-green-400">
                Working tree clean
              </span>
              <span className="text-xs text-dim ml-1">on {status.branch}</span>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-amber-500" />
                <span className="text-sm text-amber-400">Uncommitted changes</span>
                <span className="text-xs text-dim ml-1">on {status.branch}</span>
              </div>

              {status.staged.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-green-400 mb-1">
                    Staged ({status.staged.length})
                  </p>
                  <div className="space-y-0.5">
                    {status.staged.map((f, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className="shrink-0 w-4 text-center font-mono text-green-400">{f.status}</span>
                        <span className="font-mono text-heading truncate">{f.path}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {status.unstaged.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-red-400 mb-1">
                    Modified ({status.unstaged.length})
                  </p>
                  <div className="space-y-0.5">
                    {status.unstaged.map((f, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className="shrink-0 w-4 text-center font-mono text-red-400">{f.status}</span>
                        <span className="font-mono text-heading truncate">{f.path}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {status.untracked.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-dim mb-1">
                    Untracked ({status.untracked.length})
                  </p>
                  <div className="space-y-0.5">
                    {status.untracked.map((f, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className="shrink-0 w-4 text-center font-mono text-dim">?</span>
                        <span className="font-mono text-label truncate">{f}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Commit log section */}
      {selectedProject && (
        <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
          <h2 className="text-sm font-semibold text-body uppercase tracking-wide">
            Commit Log
          </h2>
          {loadingCommits ? (
            <CommitSkeleton />
          ) : commits.length === 0 ? (
            <p className="text-sm text-dim">No commits found.</p>
          ) : (
            <div className="space-y-1 max-h-[60vh] overflow-y-auto -mx-1 px-1 scrollbar-thin">
              {commits.map((commit, idx) => (
                <div
                  key={commit.hash || idx}
                  className="flex items-start gap-3 py-2 border-b border-divider last:border-b-0"
                >
                  {/* Short hash */}
                  <span className="font-mono text-xs text-cyan-400 shrink-0 pt-0.5 select-all">
                    {(commit.hash || "").slice(0, 7)}
                  </span>

                  {/* Message + meta */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-heading leading-snug break-words">
                      {commit.message}
                    </p>
                    <div className="flex items-center gap-2 mt-0.5 text-xs text-dim">
                      <span className="truncate max-w-[140px]">{commit.author}</span>
                      <span className="shrink-0">{relativeTime(commit.date)}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Toast container */}
      {toasts.length > 0 && (
        <div className="fixed right-4 left-4 sm:left-auto sm:w-80 z-50 space-y-2 pointer-events-none safe-area-toast">
          {toasts.map((toast) => (
            <div key={toast.id} className="pointer-events-auto">
              <Toast toast={toast} onDismiss={dismissToast} />
            </div>
          ))}
        </div>
      )}

      {/* Inline style for scrollbar hiding and toast animation */}
      <style>{`
        .scrollbar-none::-webkit-scrollbar { display: none; }
        .scrollbar-none { -ms-overflow-style: none; scrollbar-width: none; }
        .scrollbar-thin::-webkit-scrollbar { width: 4px; }
        .scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
        .scrollbar-thin::-webkit-scrollbar-thumb { background: var(--color-elevated); border-radius: 2px; }
        .scrollbar-thin::-webkit-scrollbar-thumb:hover { background: var(--color-hover); }
        @keyframes slide-in {
          from { opacity: 0; transform: translateY(-8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-slide-in { animation: slide-in 0.2s ease-out; }
      `}</style>
      </div>
      </div>
    </div>
  );
}
