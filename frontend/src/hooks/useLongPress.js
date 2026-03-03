import { useRef, useCallback } from "react";

/**
 * Hook for detecting long-press gestures on both touch and mouse.
 * Returns event handlers to spread onto the target element.
 *
 * @param {Function} onLongPress - called when press exceeds `delay` ms
 * @param {Function} onTap       - called on a normal short tap
 * @param {number}   delay       - long-press threshold in ms (default 500)
 */
export default function useLongPress(onLongPress, onTap, delay = 500) {
  const timerRef = useRef(null);
  const firedRef = useRef(false);
  const startPos = useRef(null);

  const clear = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const onPointerDown = useCallback(
    (e) => {
      firedRef.current = false;
      startPos.current = { x: e.clientX, y: e.clientY };
      clear();
      timerRef.current = setTimeout(() => {
        firedRef.current = true;
        timerRef.current = null;
        onLongPress?.(e);
      }, delay);
    },
    [onLongPress, delay, clear],
  );

  const onPointerUp = useCallback(
    (e) => {
      clear();
      if (!firedRef.current) {
        onTap?.(e);
      }
    },
    [onTap, clear],
  );

  const onPointerMove = useCallback(
    (e) => {
      // Cancel if finger/cursor moved more than 10px
      if (startPos.current && timerRef.current) {
        const dx = e.clientX - startPos.current.x;
        const dy = e.clientY - startPos.current.y;
        if (dx * dx + dy * dy > 100) {
          clear();
        }
      }
    },
    [clear],
  );

  const onPointerLeave = useCallback(() => clear(), [clear]);
  const onPointerCancel = useCallback(() => clear(), [clear]);

  // Prevent context menu on long-press (mobile)
  const onContextMenu = useCallback((e) => e.preventDefault(), []);

  return {
    onPointerDown,
    onPointerUp,
    onPointerMove,
    onPointerLeave,
    onPointerCancel,
    onContextMenu,
  };
}
