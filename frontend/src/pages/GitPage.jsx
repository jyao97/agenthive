import { useState, useEffect, useCallback, useRef, useId } from "react";
import { useNavigate } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import FilterTabs from "../components/FilterTabs";
import useDraft from "../hooks/useDraft";
import {
  fetchProjects as apiFetchProjects,
  fetchGitLog,
  fetchGitBranches,
  fetchGitStatus,
  fetchGitWorktrees,
  checkoutBranch,
  gitPush,
  launchTmuxAgent,
} from "../lib/api";
import { relativeTime } from "../lib/formatters";
import { useToast } from "../contexts/ToastContext";
// --- Merge dropdown (single ghost button → two plain-text options) ---
function MergeDropdown({ branchName, currentName, isMerging, disabled, onMerge }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const id = useId();

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function handleKey(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open]);

  if (isMerging) {
    return (
      <span className="ml-auto flex items-center gap-1 text-xs text-dim">
        <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        Merging...
      </span>
    );
  }

  return (
    <span className="ml-auto relative" ref={ref}>
      <button
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        disabled={disabled}
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={id}
        className="px-2 py-0.5 rounded-full text-xs font-medium transition-colors bg-cyan-500/15 text-cyan-600 hover:bg-cyan-500/25 active:bg-cyan-500/30 dark:bg-cyan-500/10 dark:text-cyan-400 dark:hover:bg-cyan-500/20 dark:active:bg-cyan-500/25 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        Merge…
      </button>

      {open && (
        <div
          id={id}
          role="menu"
          className="absolute right-0 top-full mt-1 z-30 min-w-[220px] rounded-lg bg-elevated border border-edge shadow-lg py-1 animate-in fade-in slide-in-from-top-1 duration-100"
        >
          <button
            role="menuitem"
            className="w-full text-left px-3 py-2 text-xs text-body hover:bg-hover transition-colors"
            onClick={(e) => { e.stopPropagation(); setOpen(false); onMerge(branchName, "into-current"); }}
          >
            Merge <span className="font-mono font-medium">{branchName}</span> → <span className="font-mono font-medium">{currentName}</span>
          </button>
          <button
            role="menuitem"
            className="w-full text-left px-3 py-2 text-xs text-body hover:bg-hover transition-colors"
            onClick={(e) => { e.stopPropagation(); setOpen(false); onMerge(branchName, "into-branch"); }}
          >
            Merge <span className="font-mono font-medium">{currentName}</span> → <span className="font-mono font-medium">{branchName}</span>
          </button>
        </div>
      )}
    </span>
  );
}

export default function GitPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();

  // --- State ---
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useDraft("ui:git:project", null);
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
  const [checkingOut, setCheckingOut] = useState(null);
  const [pushing, setPushing] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [cleanupMode, setCleanupMode] = useState(false);       // branch selection mode
  const [selectedCleanup, setSelectedCleanup] = useState(new Set()); // selected branch names
  const [error, setError] = useState(null);
  const toast = useToast();
  const addToast = useCallback((message, type) => type === "error" ? toast.error(message) : toast.success(message), [toast]);

  // --- Fetch projects ---
  useEffect(() => {
    let cancelled = false;
    async function fetchProjectsList() {
      setLoadingProjects(true);
      setError(null);
      try {
        const data = await apiFetchProjects();
        if (!cancelled) {
          // Sort by latest agent activity (most recent first)
          const sorted = [...data].sort((a, b) => {
            const ta = a.last_activity ? new Date(a.last_activity).getTime() : 0;
            const tb = b.last_activity ? new Date(b.last_activity).getTime() : 0;
            return tb - ta;
          });
          setProjects(sorted);
          if (sorted.length > 0) {
            // Keep saved selection if it still exists in the list
            setSelectedProject((prev) => {
              if (prev && sorted.some((p) => p.name === prev)) return prev;
              return sorted[0].name;
            });
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

  // --- Merge handler (spawns an agent) ---
  // direction: "into-current" = merge branch into current, "into-branch" = merge current into branch
  const handleMerge = useCallback(
    async (branchName, direction = "into-current") => {
      if (!selectedProject || mergingBranch) return;
      setMergingBranch(branchName);
      try {
        const currentBranch = branches.find((b) => b.current)?.name || "current branch";
        const prompt =
          direction === "into-current"
            ? `Merge the branch "${branchName}" into the current branch (${currentBranch}). ` +
              `Steps: 1) git fetch origin, 2) git merge ${branchName} --no-edit. ` +
              `If there are merge conflicts, resolve them intelligently by reading both versions and picking the correct resolution. ` +
              `After resolving, stage and commit. Report the result.`
            : `Merge the current branch (${currentBranch}) into "${branchName}". ` +
              `Steps: 1) git fetch origin, 2) git checkout ${branchName}, 3) git merge ${currentBranch} --no-edit. ` +
              `If there are merge conflicts, resolve them intelligently by reading both versions and picking the correct resolution. ` +
              `After resolving, stage and commit. Then checkout back to ${currentBranch}. Report the result.`;
        const agent = await launchTmuxAgent({
          project: selectedProject,
          mode: "AUTO",
          skip_permissions: true,
          prompt,
        });
        navigate(`/agents/${agent.id}`);
      } catch (err) {
        addToast(`Merge error: ${err.message}`, "error");
      } finally {
        setMergingBranch(null);
      }
    },
    [selectedProject, mergingBranch, branches, addToast, navigate]
  );

  // --- Checkout handler ---
  const handleCheckout = useCallback(
    async (branchName) => {
      if (!selectedProject || checkingOut) return;
      setCheckingOut(branchName);
      try {
        await checkoutBranch(selectedProject, branchName);
        addToast(`Switched to ${branchName}`, "success");
        // Refresh branches and status
        const [branchRes, statusRes] = await Promise.allSettled([
          fetchGitBranches(selectedProject).catch(() => []),
          fetchGitStatus(selectedProject).catch(() => null),
        ]);
        setBranches(branchRes.status === "fulfilled" ? branchRes.value : []);
        setStatus(statusRes.status === "fulfilled" ? statusRes.value : null);
      } catch (err) {
        addToast(`Checkout error: ${err.message}`, "error");
      } finally {
        setCheckingOut(null);
      }
    },
    [selectedProject, checkingOut, addToast]
  );

  // --- Stage, commit & push handler (spawns an agent) ---
  const handleCommitAndPush = useCallback(async () => {
    if (!selectedProject || pushing) return;
    const currentBranch = branches.find((b) => b.current)?.name || "current branch";
    setPushing(true);
    try {
      const agent = await launchTmuxAgent({
        project: selectedProject,
        mode: "AUTO",
        skip_permissions: true,
        prompt:
          `Review the uncommitted changes, stage them, commit, and push. ` +
          `Steps: 1) Run git diff and git status to review all changes. ` +
          `2) Stage all relevant changes with git add (skip secrets/.env files). ` +
          `3) Write a clear, concise commit message summarizing the changes. ` +
          `4) Commit and push to origin/${currentBranch}. ` +
          `Report what was committed and pushed.`,
      });
      navigate(`/agents/${agent.id}`);
    } catch (err) {
      addToast(`Push error: ${err.message}`, "error");
    } finally {
      setPushing(false);
    }
  }, [selectedProject, pushing, branches, addToast, navigate]);

  // --- Direct push handler (no agent needed) ---
  const handleDirectPush = useCallback(async () => {
    if (!selectedProject || pushing) return;
    setPushing(true);
    try {
      await gitPush(selectedProject);
      addToast("Pushed to origin", "success");
      const [statusRes, commitRes] = await Promise.allSettled([
        fetchGitStatus(selectedProject).catch(() => null),
        fetchGitLog(selectedProject).catch(() => []),
      ]);
      setStatus(statusRes.status === "fulfilled" ? statusRes.value : null);
      setCommits(commitRes.status === "fulfilled" ? commitRes.value : []);
    } catch (err) {
      addToast(`Push error: ${err.message}`, "error");
    } finally {
      setPushing(false);
    }
  }, [selectedProject, pushing, addToast]);

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
      const agent = await launchTmuxAgent({
        project: selectedProject,
        mode: "AUTO",
        skip_permissions: true,
        prompt:
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

  // --- Branch cleanup: toggle / select / confirm ---
  const toggleCleanupMode = useCallback(() => {
    setCleanupMode((v) => { if (!v) setSelectedCleanup(new Set()); return !v; });
  }, []);

  const toggleCleanupItem = useCallback((key) => {
    setSelectedCleanup((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  const handleCleanupConfirm = useCallback(async () => {
    if (!selectedProject || cleaning || selectedCleanup.size === 0) return;
    const currentBranch = branches.find((b) => b.current)?.name || "main";
    const branchList = [...selectedCleanup].join(", ");

    setCleaning(true);
    try {
      const agent = await launchTmuxAgent({
        project: selectedProject,
        mode: "AUTO",
        skip_permissions: true,
        prompt:
          `Clean up the selected branches for this project. The main branch is "${currentBranch}".` +
          `\n\nBranches to process: ${branchList}` +
          `\n\nSteps:` +
          `\n1) Checkout ${currentBranch}: 'git checkout ${currentBranch}'.` +
          `\n2) For each selected branch: ` +
          `check if it has useful changes not yet in ${currentBranch} using 'git log ${currentBranch}..<branch> --oneline'. ` +
          `If it has commits, try 'git merge <branch> --no-edit'. If merge conflicts occur, abort with 'git merge --abort' and skip that branch. ` +
          `\n3) After merging (or skipping), delete each selected branch with 'git branch -D <branch>'.` +
          `\n4) Run 'git branch -a' to show the final state.` +
          `\nReport a summary of what was merged, deleted, and skipped.`,
      });
      navigate(`/agents/${agent.id}`);
    } catch (err) {
      addToast(`Cleanup error: ${err.message}`, "error");
    } finally {
      setCleaning(false);
      setCleanupMode(false);
      setSelectedCleanup(new Set());
    }
  }, [selectedProject, cleaning, selectedCleanup, branches, addToast, navigate]);

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
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <PageHeader title="Git" theme={theme} onToggleTheme={onToggleTheme} hideMonitor>
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
      <div className="max-w-2xl mx-auto w-full">
      <div className="pb-24 p-4 space-y-4">

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
                    ? "bg-purple-400 text-white cursor-wait dark:bg-purple-700/50 dark:text-purple-300"
                    : "bg-purple-600 text-white hover:bg-purple-700 dark:bg-purple-600 dark:text-white dark:hover:bg-purple-500"
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
                        ? "bg-cyan-50 border-cyan-300 text-heading dark:bg-cyan-600/20 dark:border-cyan-500/50 dark:text-cyan-300"
                        : "bg-purple-50 border-purple-300 text-heading dark:bg-purple-500/10 dark:border-purple-500/30 dark:text-purple-300"
                    }`}
                  >
                    <svg className="w-3.5 h-3.5 shrink-0 opacity-60" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
                    </svg>
                    <span className="font-mono text-xs truncate">{name}</span>
                    {wt.branch && (
                      <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${isMain ? "bg-cyan-600 text-white dark:bg-cyan-600 dark:text-white" : "bg-purple-600 text-white dark:bg-purple-600 dark:text-white"}`}>
                        {wt.branch}
                      </span>
                    )}
                    {wt.detached && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500 text-white dark:bg-amber-500 dark:text-white">detached</span>
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
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-body uppercase tracking-wide">
              Branches
            </h2>
            {!loadingBranches && branches.length > 1 && (
              <div className="flex items-center gap-1.5">
                {cleanupMode && (
                  <button
                    onClick={handleCleanupConfirm}
                    disabled={cleaning || selectedCleanup.size === 0}
                    className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                      cleaning
                        ? "bg-red-400/20 text-red-400 cursor-wait"
                        : selectedCleanup.size === 0
                          ? "bg-gray-500/10 text-gray-400 cursor-not-allowed"
                          : "bg-red-600 text-white hover:bg-red-700 dark:bg-red-600 dark:hover:bg-red-500"
                    }`}
                  >
                    {cleaning ? (
                      <span className="flex items-center gap-1">
                        <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                        Creating...
                      </span>
                    ) : (
                      `Confirm (${selectedCleanup.size})`
                    )}
                  </button>
                )}
                <button
                  onClick={toggleCleanupMode}
                  disabled={cleaning}
                  className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                    cleanupMode
                      ? "bg-gray-500/15 text-body hover:bg-gray-500/25 dark:bg-gray-500/10 dark:hover:bg-gray-500/20"
                      : "bg-red-500/15 text-red-500 hover:bg-red-500/25 active:bg-red-500/30 dark:bg-red-500/10 dark:text-red-400 dark:hover:bg-red-500/20 dark:active:bg-red-500/25"
                  }`}
                >
                  {cleanupMode ? "Cancel" : "Clean"}
                </button>
              </div>
            )}
          </div>
          {loadingBranches ? (
            <BranchSkeleton />
          ) : branches.length === 0 ? (
            <p className="text-sm text-dim">No branches found.</p>
          ) : (
            <div className="space-y-1.5">
              {branches.map((branch) => {
                const isCurrent = branch.current;
                const isMerging = mergingBranch === branch.name;
                const isCheckingOut = checkingOut === branch.name;
                const currentName = branches.find((b) => b.current)?.name || "current";
                const isSelected = selectedCleanup.has(branch.name);
                return (
                  <div
                    key={branch.name}
                    onClick={cleanupMode && !isCurrent ? () => toggleCleanupItem(branch.name) : undefined}
                    onDoubleClick={!cleanupMode && !isCurrent ? () => handleCheckout(branch.name) : undefined}
                    className={`flex items-center gap-2 rounded-lg text-sm px-3 py-2 border transition-colors ${
                      cleanupMode && isSelected
                        ? "bg-red-50 border-red-400 text-heading dark:bg-red-600/15 dark:border-red-500/50 dark:text-red-300"
                        : isCurrent
                          ? "bg-cyan-50 border-cyan-400 text-heading dark:bg-cyan-600/20 dark:border-cyan-500/50 dark:text-cyan-300"
                          : cleanupMode && !isCurrent
                            ? "bg-input border-edge text-heading cursor-pointer hover:border-red-400/50 dark:hover:border-red-500/30"
                            : "bg-input border-edge text-heading cursor-pointer hover:border-cyan-400/50 dark:hover:border-cyan-500/30"
                    }`}
                    title={cleanupMode ? (isCurrent ? "Current branch (cannot clean)" : "Click to select for cleanup") : (isCurrent ? "Current branch" : "Double-click to checkout")}
                  >
                    {/* Checkbox in cleanup mode, branch icon otherwise */}
                    {cleanupMode ? (
                      <input
                        type="checkbox"
                        checked={isSelected}
                        disabled={isCurrent}
                        onChange={() => !isCurrent && toggleCleanupItem(branch.name)}
                        onClick={(e) => e.stopPropagation()}
                        className="w-3.5 h-3.5 shrink-0 rounded accent-red-500 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                      />
                    ) : (
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
                    )}

                    <span className="font-mono text-xs truncate max-w-[160px]">
                      {branch.name}
                    </span>

                    {isCurrent && !cleanupMode && (
                      <svg
                        className="w-4 h-4 text-cyan-600 dark:text-cyan-400 shrink-0"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth={2.5}
                        viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}

                    {!cleanupMode && isCheckingOut && (
                      <span className="ml-auto flex items-center gap-1 text-xs text-dim">
                        <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                        Switching...
                      </span>
                    )}

                    {!cleanupMode && !isCurrent && !isCheckingOut && (
                      <MergeDropdown
                        branchName={branch.name}
                        currentName={currentName}
                        isMerging={isMerging}
                        disabled={!!mergingBranch}
                        onMerge={handleMerge}
                      />
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
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-body uppercase tracking-wide">
              Status
            </h2>
            {!loadingStatus && status && (!status.clean || (status.ahead != null && status.ahead > 0)) && (
              <button
                onClick={!status.clean ? handleCommitAndPush : handleDirectPush}
                disabled={pushing}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors flex items-center gap-1.5 ${
                  pushing
                    ? "bg-cyan-500/10 text-cyan-400 cursor-wait"
                    : "bg-cyan-500/15 text-cyan-600 hover:bg-cyan-500/25 active:bg-cyan-500/30 dark:bg-cyan-500/10 dark:text-cyan-400 dark:hover:bg-cyan-500/20 dark:active:bg-cyan-500/25"
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                {pushing ? (
                  <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                ) : (
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0-12l-4 4m4-4l4 4" />
                  </svg>
                )}
                {pushing
                  ? (status.clean ? "Pushing..." : "Creating...")
                  : (!status.clean
                    ? "Commit & Push"
                    : `Push \u2191${status.ahead}`)}
              </button>
            )}
          </div>
          {loadingStatus ? (
            <div className="h-6 w-40 bg-skel rounded animate-pulse" />
          ) : !status ? (
            <p className="text-sm text-dim">Could not fetch status.</p>
          ) : (
            <div className="space-y-2">
              {/* Working tree line */}
              <div className="flex items-center gap-2">
                <span className={`inline-block w-2 h-2 rounded-full ${status.clean ? "bg-green-500" : "bg-amber-500"}`} />
                <span className={`text-sm ${status.clean ? "text-green-400" : "text-amber-400"}`}>
                  {status.clean ? "Working tree clean" : "Uncommitted changes"}
                </span>
                <span className="text-xs text-dim ml-1">on {status.branch}</span>
              </div>

              {/* Sync status line — only when ahead */}
              {status.ahead != null && status.ahead > 0 && (
                <div className="flex items-center gap-2">
                  <span className="inline-block w-2 h-2 rounded-full bg-cyan-500" />
                  <span className="text-sm text-cyan-400">
                    {status.ahead} {status.ahead === 1 ? "commit" : "commits"} ahead of origin
                  </span>
                </div>
              )}

              {/* File lists (when dirty) */}
              {!status.clean && (
                <>
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
                </>
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
                  <span className="font-mono text-xs text-dim shrink-0 pt-0.5 select-all">
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

      {/* Inline style for scrollbar hiding */}
      <style>{`
        .scrollbar-none::-webkit-scrollbar { display: none; }
        .scrollbar-none { -ms-overflow-style: none; scrollbar-width: none; }
        .scrollbar-thin::-webkit-scrollbar { width: 4px; }
        .scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
        .scrollbar-thin::-webkit-scrollbar-thumb { background: var(--color-elevated); border-radius: 2px; }
        .scrollbar-thin::-webkit-scrollbar-thumb:hover { background: var(--color-hover); }
      `}</style>
      </div>
      </div>
      </div>
    </div>
  );
}
