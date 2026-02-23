import { useState, useEffect, useCallback, useRef } from "react";

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

export default function GitPage() {
  // --- State ---
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState(null);
  const [commits, setCommits] = useState([]);
  const [branches, setBranches] = useState([]);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loadingCommits, setLoadingCommits] = useState(false);
  const [loadingBranches, setLoadingBranches] = useState(false);
  const [mergingBranch, setMergingBranch] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [error, setError] = useState(null);
  const toastIdRef = useRef(0);
  const tabBarRef = useRef(null);

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
    async function fetchProjects() {
      setLoadingProjects(true);
      setError(null);
      try {
        const res = await fetch("/api/projects");
        if (!res.ok) throw new Error(`Failed to load projects (${res.status})`);
        const data = await res.json();
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
    fetchProjects();
    return () => { cancelled = true; };
  }, []);

  // --- Fetch commits and branches when project changes ---
  useEffect(() => {
    if (!selectedProject) return;
    let cancelled = false;

    async function fetchGitData() {
      setLoadingCommits(true);
      setLoadingBranches(true);
      setCommits([]);
      setBranches([]);

      // Fetch commits
      try {
        const res = await fetch(`/api/git/${selectedProject}/log?limit=30`);
        if (!res.ok) throw new Error(`Failed to load commits (${res.status})`);
        const data = await res.json();
        if (!cancelled) setCommits(data);
      } catch (err) {
        if (!cancelled) setCommits([]);
      } finally {
        if (!cancelled) setLoadingCommits(false);
      }

      // Fetch branches
      try {
        const res = await fetch(`/api/git/${selectedProject}/branches`);
        if (!res.ok) throw new Error(`Failed to load branches (${res.status})`);
        const data = await res.json();
        if (!cancelled) setBranches(data);
      } catch (err) {
        if (!cancelled) setBranches([]);
      } finally {
        if (!cancelled) setLoadingBranches(false);
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
        const res = await fetch(`/api/git/${selectedProject}/merge/${branchName}`, {
          method: "POST",
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          addToast(
            `Merged "${branchName}" successfully.`,
            "success"
          );
          // Refresh commits and branches
          const [commitRes, branchRes] = await Promise.all([
            fetch(`/api/git/${selectedProject}/log?limit=30`),
            fetch(`/api/git/${selectedProject}/branches`),
          ]);
          if (commitRes.ok) setCommits(await commitRes.json());
          if (branchRes.ok) setBranches(await branchRes.json());
        } else {
          const msg =
            data?.detail || data?.message || data?.error || `Merge failed (${res.status})`;
          addToast(msg, "error");
        }
      } catch (err) {
        addToast(`Merge error: ${err.message}`, "error");
      } finally {
        setMergingBranch(null);
      }
    },
    [selectedProject, mergingBranch, addToast]
  );

  // --- Loading skeleton for commits ---
  function CommitSkeleton() {
    return (
      <div className="space-y-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-start gap-3 animate-pulse">
            <div className="w-16 h-4 bg-gray-800 rounded" />
            <div className="flex-1 space-y-1.5">
              <div className="h-4 bg-gray-800 rounded w-3/4" />
              <div className="h-3 bg-gray-800 rounded w-1/3" />
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
          <div key={i} className="h-8 w-28 bg-gray-800 rounded-full animate-pulse" />
        ))}
      </div>
    );
  }

  // --- Render ---
  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <h1 className="text-2xl font-bold text-gray-100">Git</h1>

      {/* Error state */}
      {error && (
        <div className="rounded-xl bg-red-900/30 border border-red-800 p-4 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Project tabs */}
      {loadingProjects ? (
        <div className="flex gap-2 animate-pulse">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-9 w-24 bg-gray-800 rounded-full" />
          ))}
        </div>
      ) : projects.length === 0 ? (
        <div className="rounded-xl bg-gray-900 p-4 text-gray-400 text-sm">
          No projects registered. Add a project to view its git history.
        </div>
      ) : (
        <div
          ref={tabBarRef}
          className="flex gap-2 overflow-x-auto pb-1 scrollbar-none -mx-4 px-4"
        >
          {projects.map((proj) => {
            const isActive = selectedProject === proj.name;
            return (
              <button
                key={proj.name}
                onClick={() => setSelectedProject(proj.name)}
                className={`shrink-0 px-4 py-2 rounded-full text-sm font-medium transition-colors whitespace-nowrap ${
                  isActive
                    ? "bg-violet-600 text-white"
                    : "bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200"
                }`}
              >
                {proj.display_name || proj.name}
              </button>
            );
          })}
        </div>
      )}

      {/* Branches section */}
      {selectedProject && (
        <div className="rounded-xl bg-gray-900 p-4 space-y-3">
          <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
            Branches
          </h2>
          {loadingBranches ? (
            <BranchSkeleton />
          ) : branches.length === 0 ? (
            <p className="text-sm text-gray-500">No branches found.</p>
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
                        ? "bg-violet-600/20 border-violet-500/50 text-violet-300"
                        : "bg-gray-800 border-gray-700 text-gray-300"
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
                        className="w-4 h-4 text-violet-400 shrink-0"
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
                            ? "bg-violet-700/50 text-violet-300 cursor-wait"
                            : "bg-violet-600/30 text-violet-300 hover:bg-violet-600/60 hover:text-white"
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

      {/* Commit log section */}
      {selectedProject && (
        <div className="rounded-xl bg-gray-900 p-4 space-y-3">
          <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
            Commit Log
          </h2>
          {loadingCommits ? (
            <CommitSkeleton />
          ) : commits.length === 0 ? (
            <p className="text-sm text-gray-500">No commits found.</p>
          ) : (
            <div className="space-y-1 max-h-[60vh] overflow-y-auto -mx-1 px-1 scrollbar-thin">
              {commits.map((commit, idx) => (
                <div
                  key={commit.hash || idx}
                  className="flex items-start gap-3 py-2 border-b border-gray-800 last:border-b-0"
                >
                  {/* Short hash */}
                  <span className="font-mono text-xs text-violet-400 shrink-0 pt-0.5 select-all">
                    {(commit.hash || "").slice(0, 7)}
                  </span>

                  {/* Message + meta */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-100 leading-snug break-words">
                      {commit.message}
                    </p>
                    <div className="flex items-center gap-2 mt-0.5 text-xs text-gray-500">
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
        <div className="fixed top-4 right-4 left-4 sm:left-auto sm:w-80 z-50 space-y-2 pointer-events-none">
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
        .scrollbar-thin::-webkit-scrollbar-thumb { background: #374151; border-radius: 2px; }
        .scrollbar-thin::-webkit-scrollbar-thumb:hover { background: #4b5563; }
        @keyframes slide-in {
          from { opacity: 0; transform: translateY(-8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-slide-in { animation: slide-in 0.2s ease-out; }
      `}</style>
    </div>
  );
}
