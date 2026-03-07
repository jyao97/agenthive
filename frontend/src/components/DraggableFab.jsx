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

function clampRB(rb, w, h, minBottom = 0) {
  return {
    right: Math.max(0, Math.min(rb.right, window.innerWidth - w)),
    bottom: Math.max(minBottom, Math.min(rb.bottom, window.innerHeight - h)),
  };
}

// Detect the height of fixed/sticky bottom bars (nav bar, input bar)
function detectBottomBarHeight() {
  const candidates = document.querySelectorAll("nav, [class*='glass-bar-nav']");
  let maxH = 0;
  for (const el of candidates) {
    const style = window.getComputedStyle(el);
    if (style.position === "fixed" || style.position === "sticky") {
      const rect = el.getBoundingClientRect();
      const fromBottom = window.innerHeight - rect.top;
      if (fromBottom > 0 && fromBottom < 200) {
        maxH = Math.max(maxH, fromBottom);
      }
    }
  }
  const inputBars = document.querySelectorAll("[class*='pointer-events-none'] > [class*='glass-bar-nav']");
  for (const el of inputBars) {
    const rect = el.getBoundingClientRect();
    const fromBottom = window.innerHeight - rect.top;
    if (fromBottom > 0 && fromBottom < 200) {
      maxH = Math.max(maxH, fromBottom);
    }
  }
  return maxH;
}

export default function DraggableFab({ storageKey, defaultPosition, onClick, onLongPress, className, children }) {
  const fabRef = useRef(null);
  const sizeRef = useRef({ w: 44, h: 44 });
  const [rb, setRB] = useState(null); // {right, bottom}
  const rbRef = useRef(rb);
  rbRef.current = rb;
  const dragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const absStart = useRef({ x: 0, y: 0 });
  const moved = useRef(false);
  const longPressTimer = useRef(null);
  const longPressFired = useRef(false);
  const cachedBarH = useRef(0);
  const onClickRef = useRef(onClick);
  onClickRef.current = onClick;
  const onLongPressRef = useRef(onLongPress);
  onLongPressRef.current = onLongPress;

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
        if (p.x != null && p.y != null) {
          setRB(clampRB(toRB(p, sizeRef.current.w, sizeRef.current.h), sizeRef.current.w, sizeRef.current.h));
          return;
        }
      }
    } catch { /* use default */ }
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

  // Use a stable onStart via ref — avoids re-creating the callback when rb changes
  const onStart = useCallback((e) => {
    if (e.type === "mousedown" && e.button !== 0) return;
    const t = e.touches ? e.touches[0] : e;
    const { w, h } = sizeRef.current;
    const currentRB = rbRef.current;
    dragStart.current = { x: t.clientX, y: t.clientY };
    absStart.current = currentRB ? toAbs(currentRB, w, h) : { x: 0, y: 0 };
    moved.current = false;
    longPressFired.current = false;
    dragging.current = true;
    // Cache bottom bar height once at drag start (expensive DOM query)
    cachedBarH.current = detectBottomBarHeight();
    // Start long-press timer
    clearTimeout(longPressTimer.current);
    longPressTimer.current = setTimeout(() => {
      longPressFired.current = true;
      if (navigator.vibrate) navigator.vibrate(30);
      onLongPressRef.current?.();
    }, 600);
    e.preventDefault();
  }, []); // stable — reads rb via rbRef

  useEffect(() => {
    const onMove = (e) => {
      if (!dragging.current) return;
      const t = e.touches ? e.touches[0] : e;
      const dx = t.clientX - dragStart.current.x;
      const dy = t.clientY - dragStart.current.y;
      if (!moved.current && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) return;
      if (!moved.current) {
        // First move: disable CSS transitions so transform updates are instant
        if (fabRef.current) fabRef.current.style.transition = 'none';
      }
      moved.current = true;
      clearTimeout(longPressTimer.current); // Cancel long-press on drag
      // Direct DOM manipulation — no React re-render during drag
      if (fabRef.current) {
        fabRef.current.style.transform = `translate3d(${dx}px, ${dy}px, 0)`;
      }
    };

    const onEnd = () => {
      if (!dragging.current) return;
      dragging.current = false;
      clearTimeout(longPressTimer.current);
      if (moved.current) {
        // Commit final position to React state (single re-render)
        const el = fabRef.current;
        if (el) {
          const rect = el.getBoundingClientRect();
          el.style.transform = "";
          const { w, h } = sizeRef.current;
          const abs = { x: rect.left, y: rect.top };
          const barH = cachedBarH.current;
          const minBottom = barH > 0 ? barH + 8 : 0;
          const final_ = clampRB(toRB(abs, w, h), w, h, minBottom);
          // Restore CSS transitions after position committed
          requestAnimationFrame(() => { if (el) el.style.transition = ''; });
          setRB(final_);
          try { localStorage.setItem(storageKey, JSON.stringify(final_)); } catch { /* ok */ }
        }
      } else if (!longPressFired.current) {
        // Not dragged, not long-pressed — this was a tap
        onClickRef.current?.();
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

  // Block ALL click events — taps are handled in onEnd above
  const blockClick = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  if (!rb) return null;

  return (
    <button
      ref={fabRef}
      type="button"
      onMouseDown={onStart}
      onTouchStart={onStart}
      onClick={blockClick}
      className={className}
      style={{ position: "fixed", right: rb.right, bottom: rb.bottom, zIndex: 50, touchAction: "none", willChange: "transform" }}
    >
      {children}
    </button>
  );
}
