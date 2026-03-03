import { useState, useEffect, useRef } from "react";
import { fetchHealth } from "../lib/api";

/** Polls /api/health — every 3s when unhealthy, every 15s when healthy. */
export default function useHealthStatus() {
  const [health, setHealth] = useState(null);
  const intervalRef = useRef(null);
  const healthyRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    const schedule = (ms) => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = setInterval(check, ms);
    };

    const check = async () => {
      try {
        const data = await fetchHealth();
        if (cancelled) return;
        setHealth(data);
        const ok = data?.status === "ok";
        if (ok !== healthyRef.current) {
          healthyRef.current = ok;
          schedule(ok ? 15000 : 3000);
        }
      } catch (err) {
        if (cancelled) return;
        console.warn("useHealthStatus: health check failed:", err);
        setHealth({ status: "error", db: "unknown", claude_cli: "unknown" });
        if (healthyRef.current) {
          healthyRef.current = false;
          schedule(3000);
        }
      }
    };

    check();
    schedule(15000);
    return () => { cancelled = true; clearInterval(intervalRef.current); };
  }, []);

  return health;
}
