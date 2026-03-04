import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import useHealthStatus from "../hooks/useHealthStatus";
import { useMonitor } from "../contexts/MonitorContext";
import { restartServer, fetchHealth } from "../lib/api";

/* ── Task Stats Popover ── */
function TaskStatsPopover({ taskStats, onClose, containerRef }) {
  useEffect(() => {
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose, containerRef]);

  const wTotal = taskStats?.weekly_total ?? 0;
  const wCompleted = taskStats?.weekly_completed ?? 0;
  const wFailed = taskStats?.weekly_failed ?? 0;
  const wTimeout = taskStats?.weekly_timeout ?? 0;
  const wCancelled = taskStats?.weekly_cancelled ?? 0;
  const wRejected = taskStats?.weekly_rejected ?? 0;
  const wPct = taskStats?.weekly_success_pct ?? 0;

  const ringColor = wTotal === 0 ? "#9ca3af" : wPct >= 80 ? "#22c55e" : wPct >= 50 ? "#eab308" : "#f87171";

  const rows = [
    { label: "Completed", count: wCompleted, color: "#22c55e" },
    { label: "Failed",    count: wFailed,    color: "#f87171" },
    { label: "Timeout",   count: wTimeout,   color: "#f59e0b" },
    { label: "Cancelled", count: wCancelled, color: "#9ca3af" },
    { label: "Rejected",  count: wRejected,  color: "#a78bfa" },
  ].filter(r => r.count > 0);

  return (
    <div ref={popRef} className="absolute right-0 top-full mt-2 z-50" style={{ minWidth: 220 }}>
      {/* Arrow */}
      <div className="absolute -top-1.5 right-3"
        style={{ width: 12, height: 12, transform: "rotate(45deg)", background: "var(--color-surface)", borderTop: "1px solid var(--color-edge)", borderLeft: "1px solid var(--color-edge)" }} />
      {/* Card */}
      <div className="bg-surface border border-edge rounded-xl shadow-lg overflow-hidden" style={{ boxShadow: "0 8px 30px var(--color-shadow)" }}>
        {/* Header — big ring + percentage */}
        <div className="px-4 pt-4 pb-3 flex items-center gap-3">
          <svg width="44" height="44" viewBox="0 0 44 44">
            <circle cx="22" cy="22" r="17" fill="transparent" stroke={ringColor} strokeWidth="3.5" opacity={0.18} />
            <circle cx="22" cy="22" r="17" fill="transparent" stroke={ringColor} strokeWidth="3.5"
              strokeLinecap="round"
              strokeDasharray={2 * Math.PI * 17}
              strokeDashoffset={2 * Math.PI * 17 * (1 - wPct / 100)}
              transform="rotate(-90 22 22)"
              style={{ transition: "stroke-dashoffset 0.6s ease" }} />
            <text x="22" y="22" textAnchor="middle" dominantBaseline="central"
              fill={ringColor} style={{ fontSize: "12px", fontWeight: 700 }}>
              {wPct}%
            </text>
          </svg>
          <div>
            <div className="text-heading text-sm font-semibold">Weekly Success Rate</div>
            <div className="text-dim text-xs mt-0.5">{wTotal} tasks this week</div>
          </div>
        </div>

        {/* Divider */}
        <div className="border-t border-divider" />

        {/* Breakdown rows */}
        <div className="px-4 py-2.5 space-y-1.5">
          {rows.length === 0 ? (
            <div className="text-dim text-xs py-1">No completed tasks this week</div>
          ) : rows.map(r => (
            <div key={r.label} className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: r.color }} />
                <span className="text-body">{r.label}</span>
              </div>
              <span className="text-heading font-medium tabular-nums">{r.count}</span>
            </div>
          ))}
        </div>

        {/* Progress bar */}
        {wTotal > 0 && (
          <div className="px-4 pb-3">
            <div className="h-1.5 rounded-full overflow-hidden flex" style={{ backgroundColor: "var(--color-input)" }}>
              {wCompleted > 0 && <div style={{ width: `${(wCompleted / wTotal) * 100}%`, backgroundColor: "#22c55e" }} />}
              {wFailed > 0 && <div style={{ width: `${(wFailed / wTotal) * 100}%`, backgroundColor: "#f87171" }} />}
              {wTimeout > 0 && <div style={{ width: `${(wTimeout / wTotal) * 100}%`, backgroundColor: "#f59e0b" }} />}
              {wCancelled > 0 && <div style={{ width: `${(wCancelled / wTotal) * 100}%`, backgroundColor: "#9ca3af" }} />}
              {wRejected > 0 && <div style={{ width: `${(wRejected / wTotal) * 100}%`, backgroundColor: "#a78bfa" }} />}
            </div>
          </div>
        )}

        {/* Perspective counts */}
        <div className="border-t border-divider px-4 py-2.5">
          <div className="text-faint text-[10px] uppercase tracking-wider font-medium mb-1.5">All Tasks</div>
          <div className="grid grid-cols-3 gap-y-1.5 gap-x-3 text-xs">
            {[
              { label: "Inbox", val: taskStats?.INBOX },
              { label: "Queue", val: taskStats?.QUEUE },
              { label: "Active", val: taskStats?.ACTIVE },
              { label: "Review", val: taskStats?.REVIEW },
              { label: "Done", val: taskStats?.DONE },
            ].filter(r => r.val != null).map(r => (
              <div key={r.label} className="flex items-center justify-between">
                <span className="text-dim">{r.label}</span>
                <span className="text-heading font-medium tabular-nums">{r.val}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

const SunIcon = (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
  </svg>
);

const MoonIcon = (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
  </svg>
);

export default function PageHeader({ title, theme, onToggleTheme, actions, selectAction, showTaskRing, children }) {
  const navigate = useNavigate();
  const health = useHealthStatus();
  const { taskStats } = useMonitor();
  const [restarting, setRestarting] = useState(false);
  const [showStatsPopover, setShowStatsPopover] = useState(false);
  const ringContainerRef = useRef(null);
  const pollRef = useRef(null);
  const abortRef = useRef(null);
  const closePopover = useCallback(() => setShowStatsPopover(false), []);

  // Cleanup restart polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (abortRef.current) abortRef.current.abort();
    };
  }, []);

  const isHealthy = health && health.status === "ok" && health.db === "ok" && health.claude_cli === "ok";
  const chipCls = health === null
    ? "bg-gray-500/15 text-gray-400"
    : isHealthy
      ? "bg-green-500/15 text-green-500"
      : "bg-red-500/15 text-red-400";
  const dotColor = health === null ? "bg-gray-400" : isHealthy ? "bg-green-500" : "bg-red-500";
  const chipLabel = health === null ? "..." : isHealthy ? "OK" : "Error";

  // Weekly task stats — Apple Watch ring (only shown when parent passes showTaskRing)
  const wTotal = taskStats?.weekly_total ?? 0;
  const wPct = taskStats?.weekly_success_pct ?? 0;
  const ringR = 10, ringStroke = 2.5, ringC = 2 * Math.PI * ringR;
  const ringOffset = ringC * (1 - wPct / 100);
  const ringColor = wTotal === 0 ? "#9ca3af" : wPct >= 80 ? "#22c55e" : wPct >= 50 ? "#eab308" : "#f87171";

  return (
    <div className="shrink-0 bg-page border-b border-divider z-10">
      <div className="flex items-center gap-3 px-4 pb-2" style={{ paddingTop: "max(1rem, env(safe-area-inset-top, 1rem))" }}>
        <h1 className="text-xl font-bold text-heading flex-1 shrink-0">{title}</h1>
        {actions}
        {showTaskRing && taskStats && wTotal > 0 && (
          <div className="relative" ref={ringContainerRef}>
            <button
              type="button"
              onClick={() => setShowStatsPopover(v => !v)}
              title={`This week: ${wTotal} tasks, ${wPct}% success`}
              className="shrink-0 flex items-center justify-center w-8 h-8 hover:opacity-80 transition-opacity"
            >
              <svg width="26" height="26" viewBox="0 0 26 26">
                <circle cx="13" cy="13" r={ringR} fill="transparent" stroke={ringColor} strokeWidth={ringStroke} opacity={0.18} />
                <circle cx="13" cy="13" r={ringR} fill="transparent" stroke={ringColor} strokeWidth={ringStroke}
                  strokeLinecap="round" strokeDasharray={ringC} strokeDashoffset={ringOffset}
                  transform="rotate(-90 13 13)" style={{ transition: "stroke-dashoffset 0.6s ease" }} />
                <text x="13" y="13" textAnchor="middle" dominantBaseline="central"
                  fill={ringColor} style={{ fontSize: "8px", fontWeight: 700 }}>
                  {wPct}
                </text>
              </svg>
            </button>
            {showStatsPopover && <TaskStatsPopover taskStats={taskStats} onClose={closePopover} containerRef={ringContainerRef} />}
          </div>
        )}
        <button
          type="button"
          onClick={() => navigate("/monitor")}
          title={health === null ? "Checking system health..." : isHealthy ? "System healthy" : "System issue detected"}
          className={`shrink-0 inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium transition-colors hover:opacity-80 ${chipCls}`}
        >
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${dotColor} ${!isHealthy && health !== null ? "animate-pulse" : ""}`} />
          {chipLabel}
        </button>
        <button
          type="button"
          title="Restart AgentHive"
          disabled={restarting}
          onClick={async () => {
            if (!confirm("Restart AgentHive server?")) return;
            setRestarting(true);
            // Clean up any previous polling
            if (pollRef.current) clearInterval(pollRef.current);
            if (abortRef.current) abortRef.current.abort();
            const controller = new AbortController();
            abortRef.current = controller;
            try {
              await restartServer();
              // Wait for old server to die before polling.
              // _delayed_restart sleeps 0.5s then kills processes,
              // so the old server can respond for ~1-2s after we get the response.
              let attempts = 0;
              let sawDown = false;
              let consecutiveOk = 0;
              pollRef.current = setInterval(async () => {
                if (controller.signal.aborted) {
                  clearInterval(pollRef.current);
                  return;
                }
                attempts++;
                if (attempts > 60) {
                  clearInterval(pollRef.current);
                  pollRef.current = null;
                  setRestarting(false);
                  alert("Server did not restart after 60s. Check logs.");
                  return;
                }
                try {
                  const h = await fetchHealth();
                  if (controller.signal.aborted) return;
                  if (!sawDown) {
                    // Still hitting old server — ignore until we see it go down
                    return;
                  }
                  // Server is back — require 2 consecutive OKs to be sure
                  consecutiveOk++;
                  if (consecutiveOk >= 2 && h?.status === "ok") {
                    clearInterval(pollRef.current);
                    pollRef.current = null;
                    window.location.reload();
                  }
                } catch (err) {
                  if (controller.signal.aborted) return;
                  // Network errors (TypeError) or 5xx indicate server is down
                  if (err instanceof TypeError || (err.message && /^HTTP 5\d\d/.test(err.message))) {
                    sawDown = true;
                    consecutiveOk = 0;
                  } else {
                    // Non-network error (e.g. 4xx, CORS) — still mark as down during restart
                    // but log for visibility
                    console.warn("Restart poll unexpected error:", err);
                    sawDown = true;
                    consecutiveOk = 0;
                  }
                }
              }, 1000);
            } catch (e) {
              setRestarting(false);
              alert(e.message || "Restart failed");
            }
          }}
          className={`shrink-0 w-8 h-8 flex items-center justify-center rounded-lg transition-colors ${restarting ? "text-amber-400 animate-pulse" : "text-dim hover:text-heading hover:bg-input"}`}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M5.636 5.636a9 9 0 1012.728 0M12 3v9" />
          </svg>
        </button>
        {selectAction}
        {onToggleTheme && (
          <button
            type="button"
            onClick={onToggleTheme}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-dim hover:text-heading hover:bg-input transition-colors"
          >
            {theme === "dark" ? SunIcon : MoonIcon}
          </button>
        )}
      </div>
      {children}
    </div>
  );
}
