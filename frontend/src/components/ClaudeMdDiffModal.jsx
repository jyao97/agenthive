import { useState, useEffect, useCallback, useRef } from "react";
import { applyClaudeMd } from "../lib/api";

/**
 * Fullscreen diff review modal for proposed CLAUDE.md updates.
 * Per-line checkboxes + inline editing + assembled final_content.
 */
export default function ClaudeMdDiffModal({ data, project, onClose, onApplied }) {
  const { hunks = [], current = "", proposed = "", warning, is_new, message } = data;
  const [applying, setApplying] = useState(false);

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
          checked: l.type !== "context", // only added/removed get checkboxes, default checked
          edited: false,
          editValue: l.content,
        });
      });
    });
    return flat;
  });
  const [editingKey, setEditingKey] = useState(null);
  const editRef = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") { if (editingKey) setEditingKey(null); else onClose(); } };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, editingKey]);

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, []);

  useEffect(() => {
    if (editingKey && editRef.current) editRef.current.focus();
  }, [editingKey]);

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
    // Walk through hunks, building result from current + accepted changes
    const result = [];
    // Track position in current file
    let curIdx = 0;

    for (const hunk of hunks) {
      // Parse hunk header for source start line: @@ -start,count ...
      const m = hunk.header.match(/@@ -(\d+)/);
      const srcStart = m ? parseInt(m[1], 10) - 1 : 0; // 0-indexed

      // Copy unchanged lines before this hunk
      while (curIdx < srcStart && curIdx < currentLines.length) {
        result.push(currentLines[curIdx]);
        curIdx++;
      }

      // Process hunk lines
      for (const line of hunk.lines) {
        const fl = lines.find((l) => l.hunkId === hunk.id && l.lineIdx === hunk.lines.indexOf(line));
        if (!fl) continue;

        if (fl.type === "context") {
          result.push(fl.edited ? fl.editValue : fl.content);
          curIdx++;
        } else if (fl.type === "removed") {
          if (fl.checked) {
            // Accept removal: skip this current line
            curIdx++;
          } else {
            // Reject removal: keep the current line
            result.push(fl.content);
            curIdx++;
          }
        } else if (fl.type === "added") {
          if (fl.checked) {
            // Accept addition
            result.push(fl.edited ? fl.editValue : fl.content);
          }
          // Unchecked added lines: skip (don't add them)
        }
      }
    }

    // Copy remaining lines after last hunk
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

  const handleApplySelected = useCallback(async () => {
    setApplying(true);
    try {
      const finalContent = assembleFinalContent();
      const res = await applyClaudeMd(project, { mode: "selective", final_content: finalContent });
      onApplied(res.lines);
    } catch (err) {
      onApplied(null, err.message);
    } finally {
      setApplying(false);
    }
  }, [project, onApplied, assembleFinalContent]);

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

  // Diff review with per-line controls
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-page">
      <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-divider">
        <h2 className="text-base font-bold text-heading">Proposed CLAUDE.md Updates</h2>
        <button onClick={onClose} className="text-dim hover:text-heading text-xl leading-none">&times;</button>
      </div>

      {warning && (
        <div className="px-4 py-2 bg-amber-600/20 text-amber-400 text-xs font-medium">Warning: {warning}</div>
      )}

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
          Apply Selected ({checkedCount}/{totalCheckable})
        </button>
        <button onClick={onClose} className="px-4 py-2 text-dim hover:text-body text-sm transition-colors">
          Discard All
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {hunks.map((hunk) => (
          <div key={hunk.id} className="rounded-lg bg-surface shadow-card overflow-hidden">
            <div className="px-3 py-2 border-b border-divider">
              <span className="text-xs font-bold text-heading font-mono truncate">{hunk.header}</span>
            </div>
            <div className="text-xs font-mono leading-6 overflow-x-auto">
              {hunk.lines.map((_, i) => {
                const fl = lines.find((l) => l.hunkId === hunk.id && l.lineIdx === i);
                if (!fl) return null;
                const isEditing = editingKey === fl.key;
                const canCheck = fl.type !== "context";
                const canEdit = fl.type !== "removed";
                const borderClass = fl.edited ? "border-l-2 border-blue-400" : "";
                const bgClass = fl.type === "added"
                  ? "bg-green-600/15 text-green-300"
                  : fl.type === "removed"
                  ? "bg-red-600/15 text-red-300"
                  : "text-body";
                const dimClass = canCheck && !fl.checked ? "opacity-40" : "";

                return (
                  <div key={fl.key} className={`flex items-center gap-0 ${bgClass} ${borderClass} ${dimClass}`}>
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
                    <span className="select-none w-4 shrink-0 text-dim text-center">
                      {fl.type === "added" ? "+" : fl.type === "removed" ? "−" : " "}
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
                        className="flex-1 min-w-0 bg-transparent outline-none text-xs font-mono py-0.5 px-1 border border-cyan-500/50 rounded"
                      />
                    ) : (
                      <span
                        className={`flex-1 min-w-0 py-0.5 px-1 truncate ${canEdit ? "cursor-pointer hover:bg-white/5 rounded" : ""}`}
                        onClick={canEdit ? () => startEdit(fl.key) : undefined}
                        title={canEdit ? "Click to edit" : undefined}
                      >
                        {fl.edited ? fl.editValue : fl.content}
                        {!fl.content && !fl.editValue && <span className="text-dim italic">empty line</span>}
                      </span>
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
