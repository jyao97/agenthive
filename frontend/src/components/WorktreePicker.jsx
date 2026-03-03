import { useState, useEffect } from "react";
import { fetchProjectWorktrees } from "../lib/api";

/**
 * WorktreePicker — toggle between shared main code or an isolated worktree.
 * value: null (shared) or string (worktree name)
 * onChange(value): callback
 * project: project name (to fetch existing worktrees)
 */
export default function WorktreePicker({ value, onChange, project }) {
  const enabled = value !== null;
  const [worktrees, setWorktrees] = useState([]);
  const [fetchError, setFetchError] = useState(null);

  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    fetchProjectWorktrees(project)
      .then((wt) => { if (!cancelled) { setWorktrees(wt); setFetchError(null); } })
      .catch((err) => {
        if (!cancelled) {
          console.warn("WorktreePicker: failed to fetch worktrees:", err);
          setWorktrees([]);
          setFetchError("Could not load worktrees");
        }
      });
    return () => { cancelled = true; };
  }, [project]);

  const toggle = () => {
    if (enabled) {
      onChange(null);
    } else {
      onChange(`wt-${Date.now().toString(36).slice(-5)}`);
    }
  };

  return (
    <div className="space-y-2">
      {/* Toggle */}
      <button
        type="button"
        onClick={toggle}
        className="flex items-center gap-2 text-sm"
      >
        <span
          className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
            enabled ? "bg-violet-500" : "bg-elevated"
          }`}
        >
          <span
            className={`inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform mt-0.5 ${
              enabled ? "translate-x-4.5 ml-0" : "translate-x-0.5"
            }`}
          />
        </span>
        <span className="text-label">
          {enabled ? "Isolated worktree" : "Shared code (main)"}
        </span>
      </button>

      {/* Input + existing worktree chips */}
      {enabled && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-violet-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
            </svg>
            <input
              type="text"
              value={value || ""}
              onChange={(e) => {
                const v = e.target.value.replace(/[^a-z0-9_-]/gi, "").toLowerCase();
                onChange(v || null);
              }}
              placeholder="worktree-name"
              className="flex-1 min-w-0 min-h-[32px] rounded-lg bg-input border border-edge px-2 py-1 text-sm text-heading font-mono placeholder-hint focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 transition-colors"
            />
          </div>
          {fetchError && (
            <p className="text-xs text-red-400">{fetchError}</p>
          )}
          {worktrees.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-dim">or reuse</span>
              {worktrees.slice(0, 4).map((wt) => (
                <button
                  key={wt}
                  type="button"
                  onClick={() => onChange(wt)}
                  className={`text-xs px-2 py-1 rounded font-mono truncate max-w-[120px] transition-colors ${
                    value === wt
                      ? "bg-violet-500/30 text-violet-300 border border-violet-500/50"
                      : "bg-elevated text-dim hover:text-label border border-transparent"
                  }`}
                >
                  {wt}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
