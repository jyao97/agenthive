import { useEffect, useRef, useState } from "react";
import { fetchSkills } from "../lib/api";

const USAGE_KEY = "xy.skillUsage";

function readUsage() {
  try { return JSON.parse(localStorage.getItem(USAGE_KEY) || "{}"); }
  catch { return {}; }
}

export function bumpSkillUsage(name) {
  if (!name) return;
  const u = readUsage();
  u[name] = (u[name] || 0) + 1;
  try { localStorage.setItem(USAGE_KEY, JSON.stringify(u)); } catch { /* ignore */ }
}

export default function SkillPickerPanel({ onSelect, onClose, selected }) {
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const panelRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    fetchSkills()
      .then(d => {
        if (cancelled) return;
        const usage = readUsage();
        const sorted = [...(d.skills || [])].sort((a, b) => {
          const ua = usage[a.name] || 0;
          const ub = usage[b.name] || 0;
          if (ua !== ub) return ub - ua;
          return a.name.localeCompare(b.name);
        });
        setSkills(sorted);
        setLoading(false);
      })
      .catch(e => { if (!cancelled) { setError(e.message || String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const handler = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) onClose?.();
    };
    const keyHandler = (e) => { if (e.key === "Escape") onClose?.(); };
    document.addEventListener("mousedown", handler);
    document.addEventListener("keydown", keyHandler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("keydown", keyHandler);
    };
  }, [onClose]);

  return (
    <div
      ref={panelRef}
      className="absolute bottom-full left-0 mb-2 w-80 max-h-96 overflow-auto rounded-xl bg-surface border border-divider/40 shadow-card z-30"
    >
      <div className="px-3 py-2 text-xs text-dim border-b border-divider/40 sticky top-0 bg-surface flex items-center justify-between">
        <span>Skills</span>
        {selected && (
          <button
            type="button"
            onClick={() => { onSelect(null); onClose?.(); }}
            className="text-xs text-dim hover:text-heading"
          >
            Clear
          </button>
        )}
      </div>
      {loading && <div className="p-3 text-sm text-dim">Loading…</div>}
      {error && <div className="p-3 text-sm text-red-400">Failed: {error}</div>}
      {!loading && !error && skills.length === 0 && (
        <div className="p-3 text-sm text-dim">No skills found in ~/.claude/skills/</div>
      )}
      {skills.map(s => {
        const isSel = s.name === selected;
        return (
          <button
            key={s.name}
            type="button"
            onClick={() => { onSelect(s.name); onClose?.(); }}
            className={`w-full text-left px-3 py-2 border-b border-divider/20 last:border-0 transition-colors ${
              isSel ? "bg-cyan-500/15" : "hover:bg-hover"
            }`}
          >
            <div className={`text-sm font-medium ${isSel ? "text-cyan-300" : "text-heading"}`}>
              /{s.name}
            </div>
            {s.description && (
              <div className="text-xs text-dim line-clamp-2 mt-0.5">{s.description}</div>
            )}
          </button>
        );
      })}
    </div>
  );
}
