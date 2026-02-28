import { useState, useRef, useCallback, useEffect } from "react";

/**
 * Fullscreen image viewer with gesture support:
 * - Pinch-to-zoom and pan
 * - Double-tap to toggle fit / 100% zoom
 * - Swipe down to dismiss
 * - Left/right swipe to navigate multiple images
 */
export default function ImageLightbox({ images, initialIndex = 0, onClose }) {
  const [currentIndex, setCurrentIndex] = useState(initialIndex);
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const [animating, setAnimating] = useState(false);
  const [dismissY, setDismissY] = useState(0);
  const [dismissOpacity, setDismissOpacity] = useState(1);

  const containerRef = useRef(null);
  const imgRef = useRef(null);

  // Gesture tracking refs — mutable state that doesn't trigger re-renders
  const touchState = useRef({
    lastTapTime: 0,
    lastTapPos: { x: 0, y: 0 },
    initialPinchDist: 0,
    initialScale: 1,
    initialTranslate: { x: 0, y: 0 },
    panStart: { x: 0, y: 0 },
    panStartTranslate: { x: 0, y: 0 },
    isPinching: false,
    isPanning: false,
    isSwiping: false,
    swipeStartY: 0,
    swipeStartX: 0,
    moved: false,
  });

  // Keep current values in refs for event handlers (avoids stale closures)
  const scaleRef = useRef(scale);
  const translateRef = useRef(translate);
  const isZoomedRef = useRef(scale > 1.05);
  const currentIndexRef = useRef(currentIndex);
  const dismissYRef = useRef(dismissY);

  scaleRef.current = scale;
  translateRef.current = translate;
  isZoomedRef.current = scale > 1.05;
  currentIndexRef.current = currentIndex;
  dismissYRef.current = dismissY;

  const isZoomed = scale > 1.05;

  // Reset transform when switching images
  const resetTransform = useCallback((animate = true) => {
    if (animate) setAnimating(true);
    setScale(1);
    setTranslate({ x: 0, y: 0 });
    setDismissY(0);
    setDismissOpacity(1);
    if (animate) setTimeout(() => setAnimating(false), 250);
  }, []);

  // Navigate to a different image
  const goTo = useCallback(
    (index) => {
      if (index < 0 || index >= images.length) return;
      resetTransform(false);
      setCurrentIndex(index);
    },
    [images.length, resetTransform]
  );

  // Clamp translate so image doesn't go off-screen too far
  const clampTranslate = useCallback(
    (tx, ty, s) => {
      if (s <= 1) return { x: 0, y: 0 };
      const container = containerRef.current;
      if (!container) return { x: tx, y: ty };
      const cw = container.clientWidth;
      const ch = container.clientHeight;
      const maxX = Math.max(0, (cw * (s - 1)) / 2);
      const maxY = Math.max(0, (ch * (s - 1)) / 2);
      return {
        x: Math.max(-maxX, Math.min(maxX, tx)),
        y: Math.max(-maxY, Math.min(maxY, ty)),
      };
    },
    []
  );

  const fingerDist = (t1, t2) =>
    Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);

  const midpoint = (t1, t2) => ({
    x: (t1.clientX + t2.clientX) / 2,
    y: (t1.clientY + t2.clientY) / 2,
  });

  // --- Attach non-passive touch listeners via ref (needed for preventDefault) ---
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const onTouchStart = (e) => {
      const ts = touchState.current;
      ts.moved = false;

      if (e.touches.length === 2) {
        ts.isPinching = true;
        ts.isPanning = false;
        ts.isSwiping = false;
        ts.initialPinchDist = fingerDist(e.touches[0], e.touches[1]);
        ts.initialScale = scaleRef.current;
        ts.initialTranslate = { ...translateRef.current };
      } else if (e.touches.length === 1) {
        ts.isPinching = false;
        const touch = e.touches[0];
        ts.panStart = { x: touch.clientX, y: touch.clientY };
        ts.panStartTranslate = { ...translateRef.current };
        ts.swipeStartX = touch.clientX;
        ts.swipeStartY = touch.clientY;

        if (isZoomedRef.current) {
          ts.isPanning = true;
          ts.isSwiping = false;
        } else {
          ts.isPanning = false;
          ts.isSwiping = true;
        }
      }
    };

    const onTouchMove = (e) => {
      e.preventDefault(); // works because listener is { passive: false }
      const ts = touchState.current;
      ts.moved = true;

      if (ts.isPinching && e.touches.length === 2) {
        const newDist = fingerDist(e.touches[0], e.touches[1]);
        const ratio = newDist / ts.initialPinchDist;
        const newScale = Math.max(1, Math.min(5, ts.initialScale * ratio));

        const mid = midpoint(e.touches[0], e.touches[1]);
        const container = containerRef.current;
        if (container) {
          const rect = container.getBoundingClientRect();
          const cx = mid.x - rect.left - rect.width / 2;
          const cy = mid.y - rect.top - rect.height / 2;
          const scaleChange = newScale / ts.initialScale;
          const tx = ts.initialTranslate.x + cx - cx * scaleChange;
          const ty = ts.initialTranslate.y + cy - cy * scaleChange;
          setTranslate(clampTranslate(tx, ty, newScale));
        }
        setScale(newScale);
      } else if (ts.isPanning && e.touches.length === 1) {
        const touch = e.touches[0];
        const dx = touch.clientX - ts.panStart.x;
        const dy = touch.clientY - ts.panStart.y;
        const tx = ts.panStartTranslate.x + dx;
        const ty = ts.panStartTranslate.y + dy;
        setTranslate(clampTranslate(tx, ty, scaleRef.current));
      } else if (ts.isSwiping && e.touches.length === 1) {
        const touch = e.touches[0];
        const dy = touch.clientY - ts.swipeStartY;
        if (dy > 0) {
          setDismissY(dy);
          setDismissOpacity(Math.max(0.2, 1 - dy / 300));
        }
      }
    };

    const onTouchEnd = (e) => {
      const ts = touchState.current;

      if (ts.isPinching) {
        ts.isPinching = false;
        if (scaleRef.current < 1.1) {
          setAnimating(true);
          setScale(1);
          setTranslate({ x: 0, y: 0 });
          setTimeout(() => setAnimating(false), 250);
        }
        return;
      }

      if (ts.isSwiping && e.changedTouches.length === 1) {
        const touch = e.changedTouches[0];
        const dx = touch.clientX - ts.swipeStartX;
        const dy = touch.clientY - ts.swipeStartY;

        // Swipe down to dismiss
        if (dy > 100 && Math.abs(dx) < dy) {
          onClose();
          return;
        }

        // Reset dismiss feedback
        if (dismissYRef.current > 0) {
          setAnimating(true);
          setDismissY(0);
          setDismissOpacity(1);
          setTimeout(() => setAnimating(false), 250);
        }

        // Swipe left/right to navigate
        if (images.length > 1 && Math.abs(dx) > 80 && Math.abs(dy) < 80) {
          if (dx < -80) goTo(currentIndexRef.current + 1);
          else if (dx > 80) goTo(currentIndexRef.current - 1);
          return;
        }
      }

      // Double-tap detection
      if (!ts.moved && e.changedTouches.length === 1) {
        const touch = e.changedTouches[0];
        const now = Date.now();
        const dt = now - ts.lastTapTime;
        const tapDist = Math.hypot(
          touch.clientX - ts.lastTapPos.x,
          touch.clientY - ts.lastTapPos.y
        );

        if (dt < 300 && tapDist < 30) {
          ts.lastTapTime = 0;
          setAnimating(true);
          if (isZoomedRef.current) {
            setScale(1);
            setTranslate({ x: 0, y: 0 });
          } else {
            const container = containerRef.current;
            if (container) {
              const rect = container.getBoundingClientRect();
              const tapX = touch.clientX - rect.left - rect.width / 2;
              const tapY = touch.clientY - rect.top - rect.height / 2;
              const newScale = 2;
              const tx = -tapX * (newScale - 1);
              const ty = -tapY * (newScale - 1);
              setScale(newScale);
              setTranslate(clampTranslate(tx, ty, newScale));
            } else {
              setScale(2);
            }
          }
          setTimeout(() => setAnimating(false), 250);
        } else {
          ts.lastTapTime = now;
          ts.lastTapPos = { x: touch.clientX, y: touch.clientY };
        }
      }

      ts.isPanning = false;
      ts.isSwiping = false;
    };

    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("touchend", onTouchEnd, { passive: true });

    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", onTouchEnd);
    };
  }, [clampTranslate, goTo, images.length, onClose]);

  // Close on Escape
  useEffect(() => {
    const handler = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // Prevent body scroll when lightbox is open
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  const current = images[currentIndex];
  if (!current) return null;

  const transformStyle = {
    transform: `translate(${translate.x}px, ${translate.y + dismissY}px) scale(${scale})`,
    transition: animating ? "transform 0.25s ease-out" : "none",
    willChange: "transform",
  };

  return (
    <div
      ref={containerRef}
      className="fixed inset-0 z-50 flex items-center justify-center select-none"
      style={{
        backgroundColor: `rgba(0,0,0,${dismissOpacity * 0.95})`,
        transition: animating ? "background-color 0.25s ease-out" : "none",
        touchAction: "none",
      }}
      onClick={(e) => {
        if (!isZoomed && !touchState.current.moved && e.target === containerRef.current) {
          onClose();
        }
      }}
    >
      {/* Close button */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        className="absolute top-4 right-4 z-10 w-10 h-10 flex items-center justify-center rounded-full bg-white/10 text-white/80 hover:bg-white/20 transition-colors"
        style={{ marginTop: "env(safe-area-inset-top, 0px)" }}
      >
        <svg
          className="w-6 h-6"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>

      {/* Image counter */}
      {images.length > 1 && (
        <div
          className="absolute top-4 left-1/2 -translate-x-1/2 z-10 px-3 py-1 rounded-full bg-black/50 text-white/80 text-xs font-medium"
          style={{ marginTop: "env(safe-area-inset-top, 0px)" }}
        >
          {currentIndex + 1} / {images.length}
        </div>
      )}

      {/* Image */}
      <img
        ref={imgRef}
        src={current.src}
        alt={current.filename || ""}
        draggable={false}
        className="max-h-[90vh] max-w-[90vw] object-contain pointer-events-none select-none"
        style={transformStyle}
      />

      {/* Navigation dots for multiple images */}
      {images.length > 1 && (
        <div
          className="absolute bottom-6 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5"
          style={{ marginBottom: "env(safe-area-inset-bottom, 0px)" }}
        >
          {images.map((_, i) => (
            <div
              key={i}
              className={`rounded-full transition-all ${
                i === currentIndex
                  ? "w-2 h-2 bg-white"
                  : "w-1.5 h-1.5 bg-white/40"
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
