import { useState, useEffect, useCallback } from "react";
import { applyClaudeMd } from "../lib/api";

/**
 * Fullscreen diff review modal for proposed CLAUDE.md updates.
 * Shows structured hunks with per-hunk accept/reject checkboxes.
 */
export default function ClaudeMdDiffModal({ data, project, onClose, onApplied }) {
  const { hunks = [], proposed = "", warning, is_new, message } = data;
  const [checked, setChecked] = useState(() => {
    const m = {};
    hunks.forEach((h) => { m[h.id] = true; });
    return m;
  });
  const [applying, setApplying] = useState(false);

  // Escape key to close
  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // Lock body scroll
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, []);

  const toggleHunk = useCallback((id) => {
    setChecked((prev) => ({ ...prev, [id]: !prev[id] }));
  }, []);

  const checkedCount = Object.values(checked).filter(Boolean).length;

  const handleAcceptAll = useCallback(async () => {
    setApplying(true);
    try {
      const res = await applyClaudeMd(project, { mode: "accept_all" });
      onApplied(res.lines);
    } catch (err) {
      onApplied(null, err.message);
    } finally {
      setApplying(false);
    }
  }, [project, onApplied]);

  const handleApplySelected = useCallback(async () => {
    setApplying(true);
    try {
      const ids = Object.entries(checked)
        .filter(([, v]) => v)
        .map(([k]) => Number(k));
      const res = await applyClaudeMd(project, { mode: "selective", accepted_hunk_ids: ids });
      onApplied(res.lines);
    } catch (err) {
      onApplied(null, err.message);
    } finally {
      setApplying(false);
    }
  }, [project, checked, onApplied]);

  // No changes needed
  if (message && hunks.length === 0 && !is_new) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col bg-page">
        <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-divider">
          <h2 className="text-base font-bold text-heading">Proposed CLAUDE.md Updates</h2>
          <button onClick={onClose} className="text-dim hover:text-heading text-xl leading-none">&times;</button>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center space-y-3">
            <p className="text-body text-sm">CLAUDE.md is already up to date</p>
            <button onClick={onClose} className="px-4 py-2 rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors">
              Close
            </button>
          </div>
        </div>
      </div>
    );
  }

  // New file — show full preview
  if (is_new) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col bg-page">
        <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-divider">
          <h2 className="text-base font-bold text-heading">New CLAUDE.md</h2>
          <button onClick={onClose} className="text-dim hover:text-heading text-xl leading-none">&times;</button>
        </div>
        {warning && (
          <div className="px-4 py-2 bg-amber-600/20 text-amber-400 text-xs font-medium">
            {warning}
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-4">
          <pre className="text-xs text-body font-mono whitespace-pre-wrap bg-surface rounded-lg p-4">{proposed}</pre>
        </div>
        <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-t border-divider">
          <button
            disabled={applying}
            onClick={handleAcceptAll}
            className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-semibold transition-colors disabled:opacity-50"
          >
            {applying ? "Writing..." : "Accept"}
          </button>
          <button onClick={onClose} className="px-4 py-2 text-dim hover:text-body text-sm transition-colors">
            Discard
          </button>
        </div>
      </div>
    );
  }

  // Diff review
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-page">
      {/* Title bar */}
      <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-divider">
        <h2 className="text-base font-bold text-heading">Proposed CLAUDE.md Updates</h2>
        <button onClick={onClose} className="text-dim hover:text-heading text-xl leading-none">&times;</button>
      </div>

      {/* Warning banner */}
      {warning && (
        <div className="px-4 py-2 bg-amber-600/20 text-amber-400 text-xs font-medium">
          Warning: {warning}
        </div>
      )}

      {/* Action bar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-divider">
        <button
          disabled={applying}
          onClick={handleAcceptAll}
          className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-semibold transition-colors disabled:opacity-50"
        >
          {applying ? "Applying..." : "Accept All"}
        </button>
        <button
          disabled={applying || checkedCount === 0}
          onClick={handleApplySelected}
          className="px-4 py-2 rounded-lg border border-divider text-body text-sm font-medium hover:bg-elevated transition-colors disabled:opacity-50"
        >
          Apply Selected ({checkedCount})
        </button>
        <button
          onClick={onClose}
          className="px-4 py-2 text-dim hover:text-body text-sm transition-colors"
        >
          Discard All
        </button>
      </div>

      {/* Hunk cards */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {hunks.map((hunk) => (
          <div key={hunk.id} className="rounded-lg bg-surface shadow-card overflow-hidden">
            {/* Hunk header */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-divider">
              <span className="text-xs font-bold text-heading font-mono truncate">{hunk.header}</span>
              <label className="flex items-center gap-1.5 cursor-pointer shrink-0 ml-2">
                <input
                  type="checkbox"
                  checked={!!checked[hunk.id]}
                  onChange={() => toggleHunk(hunk.id)}
                  className="w-4 h-4 rounded accent-cyan-600"
                />
              </label>
            </div>
            {/* Lines */}
            <div className="text-xs font-mono leading-5 overflow-x-auto">
              {hunk.lines.map((line, i) => (
                <div
                  key={i}
                  className={
                    line.type === "added"
                      ? "bg-green-600/15 text-green-300 px-3"
                      : line.type === "removed"
                      ? "bg-red-600/15 text-red-300 px-3"
                      : "text-body px-3"
                  }
                >
                  <span className="select-none inline-block w-4 text-dim">
                    {line.type === "added" ? "+" : line.type === "removed" ? "-" : " "}
                  </span>
                  {line.content}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
