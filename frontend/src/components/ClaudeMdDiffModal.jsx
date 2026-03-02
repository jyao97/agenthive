import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { applyClaudeMd } from "../lib/api";

/**
 * Fullscreen diff review modal for proposed CLAUDE.md updates.
 * Per-line checkboxes + inline editing + assembled final_content.
 * "Edit Final" step lets users rearrange lines before applying.
 */
export default function ClaudeMdDiffModal({ data, project, onClose, onApplied }) {
  const { hunks = [], current = "", proposed = "", warning, is_new, message } = data;
  const [applying, setApplying] = useState(false);
  const [reviewText, setReviewText] = useState(null); // null = diff view, string = edit-final view
  const reviewRef = useRef(null);

  // Build flat line list: { hunkId, lineIdx, type, content, checked, edited, editValue }
  const [lines, setLines] = useState(() => {
    const flat = [];
    hunks.forEach((h) => {
      h.lines.forEach((l, i) => {
        flat.push({
          key: `${h.id}-${i}`,
          hunkId: h.id,
          lineIdx: i,
          type: l.type,
          content: l.content,
          checked: l.type === "added",
          edited: false,
          editValue: l.content,
        });
      });
    });
    return flat;
  });
  const [editingKey, setEditingKey] = useState(null);
  const editRef = useRef(null);

  // Preamble detection: first added lines in first hunk that don't look like CLAUDE.md content
  const [showPreamble, setShowPreamble] = useState(false);
  const preambleKeys = useMemo(() => {
    if (hunks.length === 0) return new Set();
    const keys = new Set();
    const firstHunk = hunks[0];
    for (let i = 0; i < firstHunk.lines.length; i++) {
      const l = firstHunk.lines[i];
      if (l.type !== "added") {
        if (l.type === "context") continue;
        break;
      }
      const t = l.content.trimStart();
      if (t.startsWith("#") || t.startsWith(">") || t.startsWith("- ") || t.startsWith("* ") || t === "---") break;
      keys.add(`${firstHunk.id}-${i}`);
    }
    return keys;
  }, [hunks]);

  // Summary counts
  const addCount = lines.filter((l) => l.type === "added").length;
  const removeCount = lines.filter((l) => l.type === "removed").length;

  useEffect(() => {
    const handler = (e) => {
      if (e.key === "Escape") {
        if (reviewText !== null) setReviewText(null);
        else if (editingKey) setEditingKey(null);
        else onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, editingKey, reviewText]);

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, []);

  useEffect(() => {
    if (editingKey && editRef.current) editRef.current.focus();
  }, [editingKey]);

  useEffect(() => {
    if (reviewText !== null && reviewRef.current) reviewRef.current.focus();
  }, [reviewText]);

  const toggleLine = useCallback((key) => {
    setLines((prev) => prev.map((l) => l.key === key ? { ...l, checked: !l.checked } : l));
  }, []);

  const startEdit = useCallback((key) => {
    setEditingKey(key);
  }, []);

  const commitEdit = useCallback((key, value) => {
    setLines((prev) => prev.map((l) =>
      l.key === key ? { ...l, editValue: value, edited: value !== l.content } : l
    ));
    setEditingKey(null);
  }, []);

  const checkedCount = lines.filter((l) => l.type !== "context" && l.checked).length;
  const totalCheckable = lines.filter((l) => l.type !== "context").length;

  // Assemble final content from line-level selections + edits
  const assembleFinalContent = useCallback(() => {
    const currentLines = current.split("\n");
    const result = [];
    let curIdx = 0;

    for (const hunk of hunks) {
      const m = hunk.header.match(/@@ -(\d+)/);
      const srcStart = m ? parseInt(m[1], 10) - 1 : 0;

      while (curIdx < srcStart && curIdx < currentLines.length) {
        result.push(currentLines[curIdx]);
        curIdx++;
      }

      for (const line of hunk.lines) {
        const fl = lines.find((l) => l.hunkId === hunk.id && l.lineIdx === hunk.lines.indexOf(line));
        if (!fl) continue;

        if (fl.type === "context") {
          result.push(fl.edited ? fl.editValue : fl.content);
          curIdx++;
        } else if (fl.type === "removed") {
          if (fl.checked) {
            curIdx++;
          } else {
            result.push(fl.content);
            curIdx++;
          }
        } else if (fl.type === "added") {
          if (fl.checked) {
            result.push(fl.edited ? fl.editValue : fl.content);
          }
        }
      }
    }

    while (curIdx < currentLines.length) {
      result.push(currentLines[curIdx]);
      curIdx++;
    }

    return result.join("\n");
  }, [current, hunks, lines]);

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

  // Open the edit-final textarea instead of applying directly
  const handleEditFinal = useCallback(() => {
    setReviewText(assembleFinalContent());
  }, [assembleFinalContent]);

  // Apply the edited final content
  const handleApplyFinal = useCallback(async (content) => {
    setApplying(true);
    try {
      const res = await applyClaudeMd(project, { mode: "selective", final_content: content });
      onApplied(res.lines);
    } catch (err) {
      onApplied(null, err.message);
    } finally {
      setApplying(false);
    }
  }, [project, onApplied]);

  // ── No changes needed ──
  if (message && hunks.length === 0 && !is_new) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col bg-page" style={{ paddingTop: "env(safe-area-inset-top, 44px)" }}>
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

  // ── New file — show full preview ──
  if (is_new) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col bg-page" style={{ paddingTop: "env(safe-area-inset-top, 44px)" }}>
        <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-divider">
          <h2 className="text-base font-bold text-heading">New CLAUDE.md</h2>
          <button onClick={onClose} className="text-dim hover:text-heading text-xl leading-none">&times;</button>
        </div>
        {warning && (
          <div className="px-4 py-2 bg-amber-600/20 text-amber-400 text-xs font-medium">{warning}</div>
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
          <button onClick={onClose} className="px-4 py-2 text-dim hover:text-body text-sm transition-colors">Discard</button>
        </div>
      </div>
    );
  }

  // ── Edit Final view: editable textarea of assembled content ──
  if (reviewText !== null) {
    const lineCount = reviewText.split("\n").length;
    return (
      <div className="fixed inset-0 z-50 flex flex-col bg-page" style={{ paddingTop: "env(safe-area-inset-top, 44px)" }}>
        <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-divider">
          <h2 className="text-base font-bold text-heading">Edit Final CLAUDE.md</h2>
          <button onClick={() => setReviewText(null)} className="text-dim hover:text-heading text-xl leading-none">&times;</button>
        </div>
        <div className="shrink-0 px-4 py-2 bg-surface border-b border-divider">
          <p className="text-xs text-dim">
            Rearrange, edit, or remove lines as needed. {lineCount} line{lineCount !== 1 ? "s" : ""}.
          </p>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          <textarea
            ref={reviewRef}
            value={reviewText}
            onChange={(e) => setReviewText(e.target.value)}
            spellCheck={false}
            className="w-full h-full min-h-[60vh] bg-surface text-body text-xs font-mono p-4 rounded-lg border border-divider outline-none focus:border-cyan-500 resize-none leading-relaxed"
          />
        </div>
        <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-t border-divider">
          <button
            disabled={applying}
            onClick={() => handleApplyFinal(reviewText)}
            className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-semibold transition-colors disabled:opacity-50"
          >
            {applying ? "Applying..." : "Apply"}
          </button>
          <button onClick={() => setReviewText(null)} className="px-4 py-2 text-dim hover:text-body text-sm transition-colors">
            Back to Diff
          </button>
        </div>
      </div>
    );
  }

  // ── Diff review with per-line controls ──
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-page" style={{ paddingTop: "env(safe-area-inset-top, 44px)" }}>
      {/* Title bar */}
      <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-divider">
        <h2 className="text-base font-bold text-heading">Proposed CLAUDE.md Updates</h2>
        <button onClick={onClose} className="text-dim hover:text-heading text-xl leading-none">&times;</button>
      </div>

      {warning && (
        <div className="px-4 py-2 bg-amber-600/20 text-amber-400 text-xs font-medium">Warning: {warning}</div>
      )}

      {/* Sticky action bar */}
      <div className="shrink-0 border-b border-divider bg-surface sticky top-0 z-10">
        {/* Summary line */}
        <div className="px-4 pt-2 pb-1 text-xs text-dim">
          <span className="text-green-500">{addCount} addition{addCount !== 1 ? "s" : ""}</span>, <span className="text-red-400">{removeCount} removal{removeCount !== 1 ? "s" : ""}</span>
        </div>
        <div className="flex items-center gap-3 px-4 pb-3">
          <button
            disabled={applying}
            onClick={handleAcceptAll}
            className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-semibold transition-colors disabled:opacity-50"
          >
            {applying ? "Applying..." : "Accept All"}
          </button>
          <button
            disabled={applying || checkedCount === 0}
            onClick={handleEditFinal}
            className="px-4 py-2 rounded-lg border border-divider text-body text-sm font-medium hover:bg-elevated transition-colors disabled:opacity-50"
          >
            Apply Selected ({checkedCount}/{totalCheckable})
          </button>
          <button onClick={onClose} className="px-4 py-2 text-dim hover:text-body text-sm transition-colors">
            Discard All
          </button>
        </div>
      </div>

      {/* Hunk list */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Preamble toggle */}
        {preambleKeys.size > 0 && !showPreamble && (
          <button
            onClick={() => setShowPreamble(true)}
            className="text-xs text-dim hover:text-body transition-colors underline"
          >
            Show raw output ({preambleKeys.size} line{preambleKeys.size !== 1 ? "s" : ""} of agent preamble hidden)
          </button>
        )}
        {preambleKeys.size > 0 && showPreamble && (
          <button
            onClick={() => setShowPreamble(false)}
            className="text-xs text-dim hover:text-body transition-colors underline"
          >
            Hide agent preamble
          </button>
        )}

        {hunks.map((hunk) => (
          <div key={hunk.id} className="rounded-lg bg-surface shadow-card overflow-hidden">
            {/* Hunk header */}
            <div className="px-3 py-1.5 border-b border-divider">
              <span className="text-[11px] font-mono text-dim">{hunk.header}</span>
            </div>
            {/* Lines */}
            <div className="text-sm font-mono leading-relaxed overflow-x-auto">
              {hunk.lines.map((_, i) => {
                const fl = lines.find((l) => l.hunkId === hunk.id && l.lineIdx === i);
                if (!fl) return null;

                // Hide preamble lines unless toggled
                if (preambleKeys.has(fl.key) && !showPreamble) return null;

                const isEditing = editingKey === fl.key;
                const canCheck = fl.type !== "context";
                const canEdit = fl.type !== "removed";
                const dimClass = canCheck && !fl.checked ? "opacity-40" : "";

                // Per-type styling
                let rowClass, prefixChar;
                if (fl.type === "added") {
                  rowClass = "bg-green-50 border-l-4 border-green-500 text-gray-900 dark:bg-green-900/20 dark:border-green-400 dark:text-green-300";
                  prefixChar = "+";
                } else if (fl.type === "removed") {
                  rowClass = "bg-red-50 border-l-4 border-red-400 text-red-700 line-through dark:bg-red-900/20 dark:border-red-400 dark:text-red-400";
                  prefixChar = "\u2212";
                } else {
                  rowClass = "bg-white text-gray-700 dark:bg-gray-800 dark:text-gray-300";
                  prefixChar = " ";
                }

                // Edited line override: blue left border
                if (fl.edited) {
                  rowClass = rowClass.replace(/border-l-4 border-\S+/, "border-l-4 border-blue-400");
                  if (!rowClass.includes("border-l-4")) {
                    rowClass += " border-l-4 border-blue-400";
                  }
                }

                return (
                  <div
                    key={fl.key}
                    className={`flex items-start gap-0 px-3 py-1 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors ${rowClass} ${dimClass}`}
                  >
                    {/* Checkbox column */}
                    <div className="w-7 shrink-0 flex items-center justify-center">
                      {canCheck ? (
                        <input
                          type="checkbox"
                          checked={fl.checked}
                          onChange={() => toggleLine(fl.key)}
                          className="w-3.5 h-3.5 rounded accent-cyan-600 cursor-pointer"
                        />
                      ) : null}
                    </div>
                    {/* Prefix */}
                    <span className="select-none w-4 shrink-0 text-center opacity-50">
                      {prefixChar}
                    </span>
                    {/* Content — editable on tap */}
                    {isEditing ? (
                      <input
                        ref={editRef}
                        type="text"
                        value={fl.editValue}
                        onChange={(e) => {
                          const v = e.target.value;
                          setLines((prev) => prev.map((l) => l.key === fl.key ? { ...l, editValue: v } : l));
                        }}
                        onBlur={() => commitEdit(fl.key, fl.editValue)}
                        onKeyDown={(e) => { if (e.key === "Enter") commitEdit(fl.key, fl.editValue); }}
                        className="flex-1 min-w-0 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 outline-none text-sm font-mono py-0.5 px-2 border border-blue-400 dark:border-blue-400 rounded"
                      />
                    ) : (
                      !fl.content && !fl.editValue ? (
                        <span className="flex-1 h-2" />
                      ) : (
                      <span
                        className={`flex-1 min-w-0 py-0.5 px-1 whitespace-pre-wrap break-words text-xs ${canEdit ? "cursor-pointer" : ""} ${fl.type === "removed" ? "line-through" : ""}`}
                        onClick={canEdit ? () => startEdit(fl.key) : undefined}
                        title={canEdit ? "Click to edit" : undefined}
                      >
                        {fl.edited ? fl.editValue : fl.content}
                      </span>
                      )
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
