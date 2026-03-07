import { useState, useRef, useCallback, useEffect } from "react";

const DRAG_THRESHOLD = 8;

// Position stored as {right, bottom} — offset from viewport edges.
// This ensures the button stays in the same relative position on resize.

function toAbs(rb, w, h) {
  return { x: window.innerWidth - rb.right - w, y: window.innerHeight - rb.bottom - h };
}

function toRB(abs, w, h) {
  return { right: window.innerWidth - abs.x - w, bottom: window.innerHeight - abs.y - h };
}

function clampRB(rb, w, h) {
  return {
    right: Math.max(0, Math.min(rb.right, window.innerWidth - w)),
    bottom: Math.max(0, Math.min(rb.bottom, window.innerHeight - h)),
  };
}

export default function DraggableFab({ storageKey, defaultPosition, onClick, className, children }) {
  const fabRef = useRef(null);
  const sizeRef = useRef({ w: 44, h: 44 });
  const [rb, setRB] = useState(null); // {right, bottom}
  const dragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const absStart = useRef({ x: 0, y: 0 });
  const moved = useRef(false);

  // Resolve position on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const p = JSON.parse(saved);
        if (p.right != null && p.bottom != null) {
          setRB(clampRB(p, sizeRef.current.w, sizeRef.current.h));
          return;
        }
        // Migrate old {x, y} format
        if (p.x != null && p.y != null) {
          setRB(clampRB(toRB(p, sizeRef.current.w, sizeRef.current.h), sizeRef.current.w, sizeRef.current.h));
          return;
        }
      }
    } catch { /* use default */ }
    // Default position is given as {x, y} absolute — convert to {right, bottom}
    const dp = typeof defaultPosition === "function" ? defaultPosition() : defaultPosition;
    setRB(toRB(dp, sizeRef.current.w, sizeRef.current.h));
  }, [storageKey, defaultPosition]);

  // Measure actual size after first render
  useEffect(() => {
    if (fabRef.current) {
      const rect = fabRef.current.getBoundingClientRect();
      sizeRef.current = { w: rect.width, h: rect.height };
    }
  });

  const onStart = useCallback((e) => {
    if (e.type === "mousedown" && e.button !== 0) return;
    const t = e.touches ? e.touches[0] : e;
    const { w, h } = sizeRef.current;
    dragStart.current = { x: t.clientX, y: t.clientY };
    absStart.current = rb ? toAbs(rb, w, h) : { x: 0, y: 0 };
    moved.current = false;
    dragging.current = true;
    e.preventDefault();
  }, [rb]);

  useEffect(() => {
    const { w, h } = sizeRef.current;

    const onMove = (e) => {
      if (!dragging.current) return;
      const t = e.touches ? e.touches[0] : e;
      const dx = t.clientX - dragStart.current.x;
      const dy = t.clientY - dragStart.current.y;
      if (!moved.current && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) return;
      moved.current = true;
      const abs = { x: absStart.current.x + dx, y: absStart.current.y + dy };
      setRB(clampRB(toRB(abs, w, h), w, h));
    };

    const onEnd = () => {
      if (!dragging.current) return;
      dragging.current = false;
      if (moved.current) {
        setRB((p) => {
          if (p) {
            try { localStorage.setItem(storageKey, JSON.stringify(p)); } catch { /* ok */ }
          }
          return p;
        });
      }
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onEnd);
    window.addEventListener("touchmove", onMove, { passive: false });
    window.addEventListener("touchend", onEnd);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onEnd);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onEnd);
    };
  }, [storageKey]);

  const handleClick = useCallback((e) => {
    if (moved.current) { e.preventDefault(); e.stopPropagation(); return; }
    onClick?.(e);
  }, [onClick]);

  if (!rb) return null;

  return (
    <button
      ref={fabRef}
      type="button"
      onMouseDown={onStart}
      onTouchStart={onStart}
      onClick={handleClick}
      className={className}
      style={{ position: "fixed", right: rb.right, bottom: rb.bottom, zIndex: 50, touchAction: "none" }}
    >
      {children}
    </button>
  );
}
