import {
  forwardRef, useEffect, useImperativeHandle, useLayoutEffect,
  useMemo, useRef, useState,
} from "react";
import { createPortal } from "react-dom";

import { fetchSkills } from "../lib/api";

const LRU_KEY = "xy.skillUsage";
const LRU_MAX = 20;

function readLRU() {
  try {
    const raw = localStorage.getItem(LRU_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function bumpLRU(name) {
  const lru = readLRU();
  lru[name] = Date.now();
  const entries = Object.entries(lru).sort((a, b) => b[1] - a[1]).slice(0, LRU_MAX);
  try {
    localStorage.setItem(LRU_KEY, JSON.stringify(Object.fromEntries(entries)));
  } catch { /* quota — ignore */ }
}

function sourceLabel(source) {
  if (source === "personal") return "personal";
  if (source === "project") return "project";
  if (source === "command") return "builtin";
  if (source === "bundled") return "bundled";
  if (source && source.startsWith("plugin:")) return source.slice(7);
  return source || "";
}

function sourceColor(source) {
  if (source === "personal") return "text-cyan-400";
  if (source === "project") return "text-emerald-400";
  if (source === "command") return "text-amber-400";
  if (source === "bundled") return "text-faint";
  if (source && source.startsWith("plugin:")) return "text-purple-400";
  return "text-faint";
}

const SkillPickerPanel = forwardRef(function SkillPickerPanel(
  { project, query = "", anchorEl, onSelect, onClose },
  ref,
) {
  const pickerRef = useRef(null);
  const [pos, setPos] = useState(null);
  const [skills, setSkills] = useState(null);
  const [error, setError] = useState(null);
  const [activeIdx, setActiveIdx] = useState(0);

  // Load skills (once per project)
  useEffect(() => {
    let cancelled = false;
    fetchSkills(project)
      .then((data) => {
        if (cancelled) return;
        setSkills(Array.isArray(data?.skills) ? data.skills : []);
      })
      .catch((err) => {
        if (!cancelled) setError(err?.message || "failed to load skills");
      });
    return () => { cancelled = true; };
  }, [project]);

  // Pin to top edge of anchor (the input bar). Re-runs when query changes
  // because the input bar's height grows as the user types multi-line.
  useLayoutEffect(() => {
    if (!anchorEl) return;
    const rect = anchorEl.getBoundingClientRect();
    const pickerW = Math.min(rect.width, 360);
    setPos({
      bottom: window.innerHeight - rect.top + 6,
      left: rect.left + (rect.width - pickerW) / 2,
      width: pickerW,
    });
  }, [anchorEl, query, skills]);

  // Outside click closes (clicks inside the input bar are ignored — those
  // are the user typing, which already drives picker visibility)
  useEffect(() => {
    const handler = (e) => {
      if (anchorEl?.contains(e.target)) return;
      if (pickerRef.current && !pickerRef.current.contains(e.target)) onClose();
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [onClose, anchorEl]);

  const lru = useMemo(() => readLRU(), [skills]);

  const filtered = useMemo(() => {
    if (!skills) return [];
    const q = query.trim().toLowerCase();
    const matches = q
      ? skills.filter((s) =>
          s.name?.toLowerCase().includes(q) ||
          s.description?.toLowerCase().includes(q))
      : skills.slice();
    matches.sort((a, b) => {
      const la = lru[a.name] || 0;
      const lb = lru[b.name] || 0;
      if (la !== lb) return lb - la;
      return (a.name || "").localeCompare(b.name || "");
    });
    return matches;
  }, [skills, query, lru]);

  // Reset highlight when filter changes
  useEffect(() => { setActiveIdx(0); }, [query, skills]);

  const commit = (skill) => {
    if (!skill) return;
    bumpLRU(skill.name);
    onSelect(skill.name);
  };

  // Imperative API — parent (textarea) drives keyboard nav
  useImperativeHandle(ref, () => ({
    next: () => setActiveIdx((i) => Math.min(i + 1, Math.max(0, filtered.length - 1))),
    prev: () => setActiveIdx((i) => Math.max(i - 1, 0)),
    commit: () => commit(filtered[activeIdx]),
    hasItems: () => filtered.length > 0,
  }), [filtered, activeIdx]);

  if (!anchorEl) return null;

  const posStyle = pos
    ? { position: "fixed", left: pos.left, bottom: pos.bottom, width: pos.width }
    : { visibility: "hidden", position: "fixed" };

  const picker = (
    <div
      ref={pickerRef}
      data-card
      className="bg-surface border border-divider rounded-2xl shadow-xl overflow-hidden z-[9999] flex flex-col"
      style={posStyle}
    >
      <div className="px-3 py-1.5 flex items-center justify-between border-b border-divider">
        <span className="text-[11px] font-semibold text-faint uppercase tracking-wide">Skills</span>
        <span className="text-[10px] text-faint">↑↓ Enter · Esc</span>
      </div>
      <div className="max-h-[260px] overflow-y-auto py-1">
        {error && (
          <div className="px-3 py-2 text-xs text-red-400">{error}</div>
        )}
        {!error && skills === null && (
          <div className="px-3 py-2 text-xs text-faint">Loading...</div>
        )}
        {!error && skills && filtered.length === 0 && (
          <div className="px-3 py-2 text-xs text-faint">No matching skills</div>
        )}
        {!error && filtered.map((skill, i) => (
          <button
            key={`${skill.source}:${skill.name}`}
            type="button"
            onMouseDown={(e) => { e.preventDefault(); commit(skill); }}
            onMouseEnter={() => setActiveIdx(i)}
            className={`w-full text-left px-3 py-1.5 transition-colors flex flex-col gap-0.5 ${
              i === activeIdx ? "bg-input" : "hover:bg-input"
            }`}
          >
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-sm font-medium text-heading truncate">/{skill.name}</span>
              <span className={`text-[10px] font-medium uppercase tracking-wide shrink-0 ${sourceColor(skill.source)}`}>
                {sourceLabel(skill.source)}
              </span>
            </div>
            {skill.description && (
              <span className="text-xs text-dim line-clamp-2">{skill.description}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );

  return createPortal(picker, document.body);
});

export default SkillPickerPanel;
