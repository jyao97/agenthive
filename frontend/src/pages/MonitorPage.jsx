import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS } from "../lib/constants";
import {
  fetchHealth as apiFetchHealth,
  fetchAgents as apiFetchAgents,
  fetchProcesses as apiFetchProcesses,
  fetchSystemStats,
} from "../lib/api";

const HEALTH_COLORS = {
  ok: "bg-green-500",
  error: "bg-red-500",
  degraded: "bg-yellow-500",
  unavailable: "bg-red-500",
  unknown: "bg-gray-500",
};

const AGENT_STATUS_ORDER = ["EXECUTING", "PLANNING", "PLAN_REVIEW", "IDLE", "STARTING", "ERROR", "STOPPED"];

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

export default function MonitorPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(false);
  const [agents, setAgents] = useState([]);
  const [agentCounts, setAgentCounts] = useState({});
  const [processes, setProcesses] = useState([]);
  const [sysStats, setSysStats] = useState(null);

  const fetchHealth = useCallback(async () => {
    try {
      const data = await apiFetchHealth();
      setHealth(data);
      setHealthError(false);
    } catch {
      setHealthError(true);
    }
  }, []);

  const fetchAgents = useCallback(async () => {
    try {
      const data = await apiFetchAgents("limit=200");
      setAgents(data);
      const counts = {};
      for (const a of data) counts[a.status] = (counts[a.status] || 0) + 1;
      setAgentCounts(counts);
    } catch { /* retry next poll */ }
  }, []);

  const fetchProcesses = useCallback(async () => {
    try {
      setProcesses(await apiFetchProcesses());
    } catch { /* retry next poll */ }
  }, []);

  const fetchSysStats = useCallback(async () => {
    try {
      setSysStats(await fetchSystemStats());
    } catch { /* retry next poll */ }
  }, []);

  useEffect(() => {
    fetchHealth();
    fetchAgents();
    fetchProcesses();
    fetchSysStats();
  }, [fetchHealth, fetchAgents, fetchProcesses, fetchSysStats]);

  useEffect(() => {
    const interval = setInterval(() => { fetchAgents(); fetchProcesses(); fetchSysStats(); }, 3000);
    return () => clearInterval(interval);
  }, [fetchAgents, fetchProcesses, fetchSysStats]);

  useEffect(() => {
    const interval = setInterval(fetchHealth, 10000);
    return () => clearInterval(interval);
  }, [fetchHealth]);

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Monitor" theme={theme} onToggleTheme={onToggleTheme}>
        <div className="px-4 pb-2">
          <span className="text-xs text-faint">Auto-refreshing every 3s</span>
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
