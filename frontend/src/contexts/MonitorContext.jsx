import { createContext, useContext, useState, useEffect, useCallback } from "react";
import {
  fetchHealth as apiFetchHealth,
  fetchAgents as apiFetchAgents,
  fetchProcesses as apiFetchProcesses,
  fetchSystemStats,
  fetchStorageStats,
  fetchTokenUsage,
} from "../lib/api";
import usePageVisible from "../hooks/usePageVisible";

const MonitorContext = createContext(null);

const BG_INTERVAL = 5 * 60 * 1000; // 5-minute background refresh

export function MonitorProvider({ children }) {
  const visible = usePageVisible();
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(false);
  const [agents, setAgents] = useState([]);
  const [agentCounts, setAgentCounts] = useState({});
  const [processes, setProcesses] = useState([]);
  const [sysStats, setSysStats] = useState(null);
  const [tokenUsage, setTokenUsage] = useState(null);
  const [storageStats, setStorageStats] = useState(null);
  const [monitorActive, setMonitorActive] = useState(false);

  const fetchHealth = useCallback(async () => {
    try {
      const data = await apiFetchHealth();
      setHealth(data);
      setHealthError(false);
    } catch (err) {
      console.error("MonitorContext: failed to fetch health:", err);
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
    } catch (err) {
      console.error("MonitorContext: failed to fetch agents:", err);
    }
  }, []);

  const fetchProcesses = useCallback(async () => {
    try {
      setProcesses(await apiFetchProcesses());
    } catch (err) {
      console.error("MonitorContext: failed to fetch processes:", err);
    }
  }, []);

  const fetchSysStats = useCallback(async () => {
    try {
      setSysStats(await fetchSystemStats());
    } catch (err) {
      console.error("MonitorContext: failed to fetch system stats:", err);
    }
  }, []);

  const fetchUsage = useCallback(async () => {
    try {
      setTokenUsage(await fetchTokenUsage());
    } catch (err) {
      console.error("MonitorContext: failed to fetch token usage:", err);
    }
  }, []);

  const fetchStorage = useCallback(async () => {
    try {
      setStorageStats(await fetchStorageStats());
    } catch (err) {
      console.error("MonitorContext: failed to fetch storage stats:", err);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([
      fetchHealth(), fetchAgents(), fetchProcesses(),
      fetchSysStats(), fetchUsage(), fetchStorage(),
    ]);
  }, [fetchHealth, fetchAgents, fetchProcesses, fetchSysStats, fetchUsage, fetchStorage]);

  // Initial fetch on mount
  useEffect(() => { refreshAll(); }, [refreshAll]);

  // Background 5-minute poll (always running when tab is visible)
  useEffect(() => {
    if (!visible) return;
    const id = setInterval(refreshAll, BG_INTERVAL);
    return () => clearInterval(id);
  }, [visible, refreshAll]);

  // Fast polling when MonitorPage is active and tab is visible
  useEffect(() => {
    if (!visible || !monitorActive) return;
    // Agents, processes, sysStats: every 5s
    const fastId = setInterval(() => {
      fetchAgents(); fetchProcesses(); fetchSysStats();
    }, 5000);
    // Health: every 10s
    const healthId = setInterval(fetchHealth, 10000);
    // Token usage, storage: every 30s
    const slowId = setInterval(() => {
      fetchUsage(); fetchStorage();
    }, 30000);
    return () => {
      clearInterval(fastId);
      clearInterval(healthId);
      clearInterval(slowId);
    };
  }, [visible, monitorActive, fetchAgents, fetchProcesses, fetchSysStats, fetchHealth, fetchUsage, fetchStorage]);

  const activate = useCallback(() => setMonitorActive(true), []);
  const deactivate = useCallback(() => setMonitorActive(false), []);

  return (
    <MonitorContext.Provider value={{
      health, healthError, agents, agentCounts, processes, sysStats, tokenUsage, storageStats,
      refresh: refreshAll, activate, deactivate,
    }}>
      {children}
    </MonitorContext.Provider>
  );
}

export function useMonitor() {
  const ctx = useContext(MonitorContext);
  if (!ctx) throw new Error("useMonitor must be used within MonitorProvider");
  return ctx;
}
