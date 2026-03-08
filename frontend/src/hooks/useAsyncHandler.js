import { useState, useCallback } from "react";

/**
 * Manages loading IDs and error state for async task actions.
 * Replaces the repeated try/catch/finally + Set management pattern
 * across ExecutingView, InboxView, PlanningView, ReviewView.
 */
export default function useAsyncHandler() {
  const [loadingIds, setLoadingIds] = useState(new Set());
  const [error, setError] = useState(null);

  const handle = useCallback(async (id, fn, errorMsg = "Action failed") => {
    setError(null);
    setLoadingIds((s) => new Set(s).add(id));
    try {
      await fn();
    } catch (err) {
      setError(err.message || errorMsg);
    } finally {
      setLoadingIds((s) => { const n = new Set(s); n.delete(id); return n; });
    }
  }, []);

  return { loadingIds, error, setError, handle };
}
