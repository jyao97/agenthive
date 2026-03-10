import { useState, useRef, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";

/**
 * A tag badge that, when tapped, shows a floating pill-selector popover.
 * Uses portal + absolute positioning (page coords) to avoid both
 * stacking-context clipping and iOS keyboard viewport issues.
 */
export default function TagPicker({ options, value, onSelect, className, children, extra }) {
  const [open, setOpen] = useState(false);
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState(null);
  const ref = useRef(null);
  const popRef = useRef(null);
  const closeTimer = useRef(null);

  const handleOpen = (e) => {
    e.stopPropagation();
    if (open) { handleClose(); return; }
    setOpen(true);
    requestAnimationFrame(() => requestAnimationFrame(() => setVisible(true)));
  };

  const handleClose = useCallback(() => {
    setVisible(false);
    clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => setOpen(false), 250);
  }, []);

  const handleSelect = (optValue) => (e) => {
    e.stopPropagation();
    onSelect(optValue);
    handleClose();
  };

  // Close on click/touch outside
  useEffect(() => {
    if (!open) return;
    const onOutside = (e) => {
      if (ref.current?.contains(e.target)) return;
      if (popRef.current?.contains(e.target)) return;
      handleClose();
    };
    document.addEventListener("pointerdown", onOutside, true);
    return () => document.removeEventListener("pointerdown", onOutside, true);
  }, [open, handleClose]);

  // Track tag position via RAF — uses page-absolute coords (scrollY)
  // so keyboard open/close doesn't affect positioning
  useEffect(() => {
    if (!open) return;
    let raf;
    const track = () => {
      const el = ref.current;
      if (el) {
        const rect = el.getBoundingClientRect();
        const top = rect.bottom + window.scrollY + 6;
        const left = rect.left + window.scrollX;
        setPos(prev => {
          if (prev && Math.abs(prev.top - top) < 0.5 && Math.abs(prev.left - left) < 0.5) return prev;
          return { top, left };
        });
      }
      raf = requestAnimationFrame(track);
    };
    raf = requestAnimationFrame(track);
    return () => cancelAnimationFrame(raf);
  }, [open]);

  useEffect(() => () => clearTimeout(closeTimer.current), []);

  return (
    <>
      <span ref={ref} className={className} onClick={handleOpen}>
        {children}
      </span>
      {open && pos && createPortal(
        <div
          ref={popRef}
          className={`absolute z-[9999] rounded-xl bg-surface shadow-lg ring-1 ring-edge/40 p-1 transform-gpu transition-[transform,opacity] duration-250 ease-[cubic-bezier(0.22,1.15,0.36,1)] origin-top-left ${
            visible ? "opacity-100 scale-100" : "opacity-0 scale-95"
          }`}
          style={{
            top: pos.top,
            left: pos.left,
            width: extra ? "min(180px, 70vw)" : undefined,
            maxWidth: extra ? undefined : "min(280px, 85vw)",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex flex-wrap gap-0.5">
            {options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`px-2.5 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-all duration-150 ${
                  String(opt.value) === String(value)
                    ? "bg-cyan-500 text-white shadow-sm"
                    : "text-dim hover:text-heading hover:bg-elevated active:scale-95"
                }`}
                onClick={handleSelect(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {extra}
        </div>,
        document.body
      )}
    </>
  );
}
