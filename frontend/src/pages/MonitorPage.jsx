import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS } from "../lib/constants";
import { useMonitor } from "../contexts/MonitorContext";

const HEALTH_COLORS = {
  ok: "bg-green-500",
  error: "bg-red-500",
  degraded: "bg-yellow-500",
  unavailable: "bg-red-500",
  unknown: "bg-gray-500",
};

const AGENT_STATUS_ORDER = ["EXECUTING", "SYNCING", "IDLE", "STARTING", "ERROR", "STOPPED"];

function formatResetTime(isoStr) {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  if (isNaN(d)) return "";
  const now = new Date();
  const diffMs = d - now;
  if (diffMs <= 0) return "now";
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 60) return `in ${diffMin}m`;
  const diffH = Math.floor(diffMin / 60);
  const remMin = diffMin % 60;
  if (diffH < 24) return `in ${diffH}h${remMin > 0 ? ` ${remMin}m` : ""}`;
  const diffD = Math.floor(diffH / 24);
  const remH = diffH % 24;
  return `in ${diffD}d ${remH}h`;
}

function UsageBar({ label, pct, detail }) {
  const barColor =
    pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-500" : "bg-cyan-500";
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-label">{label}</span>
        <span className="text-xs text-dim font-mono">{detail}</span>
      </div>
      <div className="h-2 rounded-full bg-elevated overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}

function HealthCard({ label, status }) {
  const color = HEALTH_COLORS[status] || HEALTH_COLORS.unknown;
  return (
    <div className="rounded-xl bg-surface shadow-card p-4 flex items-center gap-3 min-w-0">
      <span className={`inline-block w-2.5 h-2.5 rounded-full ${color}`} />
      <div className="min-w-0">
        <p className="text-xs text-dim uppercase tracking-wider">{label}</p>
        <p className="text-sm font-medium text-heading truncate">{status}</p>
      </div>
    </div>
  );
}

const STORAGE_COLORS = {
  cyan: { ring: "stroke-cyan-500", dot: "bg-cyan-500", bar: "bg-cyan-500" },
  violet: { ring: "stroke-violet-500", dot: "bg-violet-500", bar: "bg-violet-500" },
  amber: { ring: "stroke-amber-500", dot: "bg-amber-500", bar: "bg-amber-500" },
  emerald: { ring: "stroke-emerald-500", dot: "bg-emerald-500", bar: "bg-emerald-500" },
  orange: { ring: "stroke-orange-500", dot: "bg-orange-500", bar: "bg-orange-500" },
  rose: { ring: "stroke-rose-500", dot: "bg-rose-500", bar: "bg-rose-500" },
  gray: { ring: "stroke-gray-400", dot: "bg-gray-400", bar: "bg-gray-400" },
};

// Hex values for SVG stroke (Tailwind classes don't work on SVG stroke directly)
const STORAGE_HEX = {
  cyan: "#06b6d4", violet: "#8b5cf6", amber: "#f59e0b",
  emerald: "#10b981", orange: "#f97316", rose: "#f43f5e", gray: "#9ca3af",
};

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = bytes / Math.pow(1024, i);
  return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[i]}`;
}

function StorageChart({ data }) {
  if (!data) return null;
  const { categories, total_bytes } = data;
  const visible = categories.filter((c) => c.size_bytes > 0);
  if (visible.length === 0) return null;

  const radius = 52;
  const stroke = 14;
  const size = 140;
  const circumference = 2 * Math.PI * radius;

  // Build segments
  let offset = 0;
  const segments = visible.map((cat) => {
    const pct = total_bytes > 0 ? cat.size_bytes / total_bytes : 0;
    const dash = pct * circumference;
    const gap = circumference - dash;
    const seg = { ...cat, pct, dash, gap, offset };
    offset += dash;
    return seg;
  });

  return (
    <section>
      <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">Storage</h2>
      <div className="rounded-xl bg-surface shadow-card p-4 flex items-center gap-4">
        {/* Donut ring — left */}
        <div className="relative shrink-0" style={{ width: size, height: size }}>
          <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
            <circle
              cx={size / 2} cy={size / 2} r={radius}
              fill="none" strokeWidth={stroke}
              className="stroke-elevated"
            />
            {segments.map((seg) => (
              <circle
                key={seg.name}
                cx={size / 2} cy={size / 2} r={radius}
                fill="none" strokeWidth={stroke}
                stroke={STORAGE_HEX[seg.color] || STORAGE_HEX.gray}
                strokeDasharray={`${seg.dash} ${seg.gap}`}
                strokeDashoffset={-seg.offset}
                strokeLinecap="butt"
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
              />
            ))}
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-sm font-bold text-heading">{formatBytes(total_bytes)}</span>
            <span className="text-[10px] text-dim">total</span>
          </div>
        </div>
        {/* Legend — right */}
        <div className="flex-1 min-w-0 space-y-1">
          {visible.map((cat) => {
            const colors = STORAGE_COLORS[cat.color] || STORAGE_COLORS.gray;
            return (
              <div key={cat.name} className="flex items-center gap-2 text-xs">
                <span className={`w-2 h-2 rounded-full shrink-0 ${colors.dot}`} />
                <span className="text-label truncate flex-1">{cat.name}</span>
                <span className="text-dim font-mono shrink-0">{formatBytes(cat.size_bytes)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

export default function MonitorPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const {
    health, healthError, agents, agentCounts, processes, sysStats, tokenUsage, storageStats,
    refresh, activate, deactivate,
  } = useMonitor();
  const [refreshing, setRefreshing] = useState(false);

  // Activate fast polling while this page is mounted; show cached data
  // immediately, then do a fresh fetch.
  useEffect(() => {
    activate();
    refresh();
    return () => deactivate();
  }, [activate, deactivate, refresh]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await refresh();
    // Minimum 400ms spinner display to prevent jarring sub-frame flicker
    setTimeout(() => setRefreshing(false), 400);
  }, [refresh]);

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Monitor" theme={theme} onToggleTheme={onToggleTheme}>
        <div className="px-4 pb-2 flex items-center justify-between">
          <span className="text-xs text-faint">Auto-refreshing every 5s</span>
          <button
            type="button"
            onClick={handleRefresh}
            title="Refresh"
            className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
          >
            <svg className={`w-4 h-4 text-label ${refreshing ? "animate-spin" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
        </div>
      </PageHeader>

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <div className="pb-20 p-4 space-y-5 max-w-2xl mx-auto w-full">
        {/* System Health */}
        <section>
          <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">System Health</h2>
          {healthError && !health ? (
            <div className="rounded-xl bg-surface shadow-card p-4">
              <p className="text-sm text-red-400">Failed to reach health endpoint.</p>
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-3">
              <HealthCard label="Overall" status={health?.status || "unknown"} />
              <HealthCard label="Database" status={health?.db || "unknown"} />
              <HealthCard label="Claude CLI" status={health?.claude_cli || "unknown"} />
            </div>
          )}
        </section>

        {/* System Resources */}
        {sysStats && (
          <section>
            <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">Resources</h2>
            <div className="space-y-3">
              {/* CPU / Memory / Disk bars */}
              <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
                {sysStats.cpu && (
                  <UsageBar
                    label={`CPU (${sysStats.cpu.cores} cores)`}
                    pct={sysStats.cpu.usage_pct}
                    detail={`Load ${sysStats.cpu.load_1m}`}
                  />
                )}
                {sysStats.memory && (
                  <UsageBar
                    label="Memory"
                    pct={sysStats.memory.usage_pct}
                    detail={`${sysStats.memory.used_gb} / ${sysStats.memory.total_gb} GB`}
                  />
                )}
                {sysStats.disk && (
                  <UsageBar
                    label="Disk"
                    pct={sysStats.disk.usage_pct}
                    detail={`${sysStats.disk.used_gb} / ${sysStats.disk.total_gb} GB`}
                  />
                )}
                {sysStats.agenthive && (
                  <UsageBar
                    label="AgentHive"
                    pct={sysStats.memory ? Math.min(Math.round(sysStats.agenthive.mem_mb / (sysStats.memory.total_gb * 1024) * 100), 100) : 0}
                    detail={`${sysStats.agenthive.mem_mb} MB / ${sysStats.agenthive.cpu_pct}% CPU`}
                  />
                )}
              </div>

              {/* GPUs */}
              {sysStats.gpus && sysStats.gpus.length > 0 && (
                <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
                  {sysStats.gpus.map((gpu) => (
                    <div key={gpu.index}>
                      <p className="text-xs text-label font-medium mb-2">
                        GPU {gpu.index}: {gpu.name}
                        <span className="text-dim ml-2">{gpu.temp_c}°C</span>
                      </p>
                      <div className="space-y-2">
                        <UsageBar label="Compute" pct={gpu.gpu_pct} detail={`${gpu.gpu_pct}%`} />
                        <UsageBar
                          label="VRAM"
                          pct={gpu.mem_pct}
                          detail={`${gpu.mem_used_mb} / ${gpu.mem_total_mb} MB`}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        )}

        {/* Token Usage */}
        {tokenUsage && (
          <section>
            <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">Token Usage</h2>
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
              {tokenUsage.session && (
                <UsageBar
                  label="Session (5h)"
                  pct={tokenUsage.session.utilization ?? 0}
                  detail={`${tokenUsage.session.utilization ?? 0}% — resets ${formatResetTime(tokenUsage.session.resets_at)}`}
                />
              )}
              {tokenUsage.weekly && (
                <UsageBar
                  label="Weekly (7d)"
                  pct={tokenUsage.weekly.utilization ?? 0}
                  detail={`${tokenUsage.weekly.utilization ?? 0}% — resets ${formatResetTime(tokenUsage.weekly.resets_at)}`}
                />
              )}
            </div>
          </section>
        )}

        {/* Storage */}
        <StorageChart data={storageStats} />

        {/* Summary Stats */}
        <section className="grid grid-cols-2 gap-3">
          <div className="rounded-xl bg-surface shadow-card p-4">
            <p className="text-xs text-dim uppercase tracking-wider">Claude Processes</p>
            <div className="mt-1 flex items-baseline gap-1">
              <span className={`text-2xl font-bold ${processes.length > 0 ? "text-cyan-400" : "text-dim"}`}>{processes.length}</span>
              <span className="text-sm text-dim">running</span>
            </div>
          </div>
          <div className="rounded-xl bg-surface shadow-card p-4">
            <p className="text-xs text-dim uppercase tracking-wider">Agents</p>
            <div className="mt-1 flex items-baseline gap-1">
              <span className="text-2xl font-bold text-heading">{agents.length}</span>
              <span className="text-sm text-dim">total</span>
            </div>
          </div>
        </section>

        {/* Agent Status Breakdown */}
        <section>
          <div className="rounded-xl bg-surface shadow-card p-4">
            <p className="text-xs text-dim uppercase tracking-wider mb-2">Agents by Status</p>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {AGENT_STATUS_ORDER.map((st) =>
                agentCounts[st] ? (
                  <span key={st} className="text-xs whitespace-nowrap">
                    <span className={AGENT_STATUS_TEXT_COLORS[st] || "text-label"}>{agentCounts[st]}</span>{" "}
                    <span className="text-faint">{st.toLowerCase().replace("_", " ")}</span>
                  </span>
                ) : null
              )}
              {agents.length === 0 && <span className="text-xs text-faint">No agents</span>}
            </div>
          </div>
        </section>

        {/* Running Processes */}
        {processes.length > 0 && (
          <section>
            <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">
              Running Processes ({processes.length})
            </h2>
            <div className="space-y-2">
              {processes.map((proc) => {
                const agent = agents.find((a) => a.id === proc.agent_id);
                const elapsed = proc.elapsed_seconds;
                const elapsedStr = elapsed != null
                  ? elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`
                  : "--";
                return (
                  <button
                    key={proc.agent_id}
                    type="button"
                    onClick={() => navigate(`/agents/${proc.agent_id}`)}
                    className="w-full text-left rounded-xl bg-surface shadow-card p-3 border-l-2 border-cyan-500/40 flex items-center gap-3 transition-colors active:bg-input hover:ring-1 hover:ring-ring-hover"
                  >
                    <div className="shrink-0">
                      <span className="inline-block w-2 h-2 rounded-full bg-cyan-500 animate-pulse" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-heading truncate">
                        {agent ? agent.name : proc.agent_id}
                      </p>
                      <p className="text-xs text-dim mt-0.5">
                        {proc.type === "planner" ? "Planning..." : "Executing..."}{" "}
                        {agent && <span className="text-label">{agent.project}</span>}
                      </p>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-sm font-mono text-cyan-400">{elapsedStr}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
        )}

        {/* Active Agents */}
        <section>
          <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">
            Active Agents ({agents.filter((a) => a.status !== "STOPPED").length})
          </h2>
          {agents.filter((a) => a.status !== "STOPPED").length === 0 ? (
            <div className="rounded-xl bg-surface shadow-card p-6 text-center">
              <p className="text-faint text-sm">No active agents</p>
            </div>
          ) : (
            <div className="space-y-2">
              {agents
                .filter((a) => a.status !== "STOPPED")
                .map((agent) => {
                  const dot = AGENT_STATUS_COLORS[agent.status] || "bg-gray-500";
                  const textCls = AGENT_STATUS_TEXT_COLORS[agent.status] || "text-dim";
                  return (
                    <button
                      key={agent.id}
                      type="button"
                      onClick={() => navigate(`/agents/${agent.id}`)}
                      className="w-full text-left rounded-xl bg-surface shadow-card p-3 flex items-center gap-3 transition-colors active:bg-input hover:ring-1 hover:ring-ring-hover"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-heading truncate">{agent.name}</p>
                        <div className="flex items-center gap-1.5 mt-0.5">
                          <span className={`inline-block w-1.5 h-1.5 rounded-full ${dot}`} />
                          <span className={`text-xs ${textCls}`}>{agent.status.toLowerCase().replace("_", " ")}</span>
                          <span className="text-xs text-dim ml-1">{agent.project}</span>
                        </div>
                      </div>
                      <svg className="w-4 h-4 text-faint shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                  );
                })}
            </div>
          )}
        </section>
      </div>
      </div>
    </div>
  );
}
