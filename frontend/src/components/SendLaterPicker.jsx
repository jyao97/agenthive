import { useState, useEffect, useRef } from "react";
import { DATE_SHORT } from "../lib/formatters";

export default function SendLaterPicker({ onSelect, onClose, onClear }) {
  const [customValue, setCustomValue] = useState("");
  const pickerRef = useRef(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target)) onClose();
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [onClose]);

  const presets = [
    { label: "30 minutes", minutes: 30 },
    { label: "1 hour", minutes: 60 },
    { label: "2 hours", minutes: 120 },
    { label: "4 hours", minutes: 240 },
  ];

  const tomorrowMorning = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    return d;
  };

  const handlePreset = (minutes) => {
    const d = new Date(Date.now() + minutes * 60000);
    onSelect(d.toISOString());
  };

  const handleTomorrow = () => {
    onSelect(tomorrowMorning().toISOString());
  };

  const handleDateChange = (e) => {
    const val = e.target.value;
    if (!val) return;
    setCustomValue(val);
    const d = new Date(val);
    if (!isNaN(d.getTime()) && d > new Date()) {
      onSelect(d.toISOString());
    }
  };

  const localMin = (() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`; })();

  const displayTime = customValue
    ? new Date(customValue).toLocaleString([], DATE_SHORT)
    : null;

  return (
    <div
      ref={pickerRef}
      className="absolute bottom-12 right-0 w-56 bg-surface border border-divider rounded-xl shadow-lg overflow-hidden z-50"
    >
      <div className="px-3 py-2 border-b border-divider flex items-center justify-between">
        <span className="text-xs font-semibold text-heading">Remind At</span>
        {onClear && (
          <button type="button" onClick={() => { onClear(); onClose(); }}
            className="text-[10px] text-red-400 hover:text-red-300 font-medium">
            Clear
          </button>
        )}
      </div>
      <div className="py-1">
        {presets.map((p) => (
          <button
            key={p.minutes}
            type="button"
            onClick={() => handlePreset(p.minutes)}
            className="w-full text-left px-3 py-2 text-sm text-body hover:bg-input transition-colors flex items-center gap-2"
          >
            <svg className="w-4 h-4 text-amber-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6l4 2m6-2a10 10 0 11-20 0 10 10 0 0120 0z" />
            </svg>
            {p.label}
          </button>
        ))}
        <button
          type="button"
          onClick={handleTomorrow}
          className="w-full text-left px-3 py-2 text-sm text-body hover:bg-input transition-colors flex items-center gap-2"
        >
          <svg className="w-4 h-4 text-orange-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
          </svg>
          Tomorrow 9 AM
        </button>
      </div>
      <div className="border-t border-divider px-3 py-2">
        <div className="relative">
          <input
            type="datetime-local"
            value={customValue}
            onChange={handleDateChange}
            min={localMin}
            className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
            style={{ zIndex: 1 }}
          />
          <div
            className="w-full rounded-lg bg-amber-500 hover:bg-amber-400 text-white text-sm py-1.5 font-medium transition-colors flex items-center justify-center gap-2"
          >
            <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            {displayTime || "Pick a time"}
          </div>
        </div>
      </div>
    </div>
  );
}
