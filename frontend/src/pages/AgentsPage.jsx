import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { fetchAgents, stopAgent } from "../lib/api";
import { relativeTime } from "../lib/formatters";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS, POLL_INTERVAL, modelDisplayName } from "../lib/constants";
import BotIcon from "../components/BotIcon";
import PageHeader from "../components/PageHeader";

const FILTER_TABS = [
  { key: "ALL", label: "All" },
  { key: "ACTIVE", label: "Active" },
  { key: "STOPPED", label: "Stopped" },
];

function agentBotState(status) {
  if (status === "EXECUTING" || status === "PLANNING") return "running";
  if (status === "ERROR") return "error";
  if (status === "IDLE" || status === "PLAN_REVIEW") return "completed";
  if (status === "STOPPED") return "idle";
  return "idle";
}

function AgentRow({ agent, onClick, selecting, selected, onToggle }) {
  const state = agentBotState(agent.status);
  const statusDotColor = AGENT_STATUS_COLORS[agent.status] || "bg-gray-500";
  const statusTextColor = AGENT_STATUS_TEXT_COLORS[agent.status] || "text-dim";
  const [copied, setCopied] = useState(false);

  const handleCopyId = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(agent.id).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const handleClick = () => {
    if (selecting) {
      onToggle(agent.id);
    } else {
      onClick();
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`w-full text-left rounded-xl bg-surface shadow-card p-4 flex items-center gap-3 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover ${
        selecting && selected ? "ring-1 ring-cyan-500" : ""
      }`}
    >
      {/* Selection checkbox */}
      {selecting && (
        <div className="shrink-0 flex items-center justify-center w-6 h-6">
          <div
            className={`w-[22px] h-[22px] rounded-full border-2 flex items-center justify-center transition-colors ${
              selected
                ? "bg-cyan-500 border-cyan-500"
                : "border-edge bg-transparent"
            }`}
          >
            {selected && (
              <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
        </div>
      )}

      <div className="relative shrink-0" onClick={selecting ? undefined : handleCopyId} title={selecting ? undefined : `Copy ID: ${agent.id}`}>
        <BotIcon state={state} className="w-10 h-10 cursor-pointer hover:opacity-70 transition-opacity" />
        {copied && (
          <span className="absolute -bottom-5 left-1/2 -translate-x-1/2 text-[10px] text-cyan-400 font-medium whitespace-nowrap">
            Copied!
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-heading truncate flex-1">
            {agent.name}
          </h3>
          {agent.last_message_at && (
            <span className="text-xs text-dim shrink-0">
              {relativeTime(agent.last_message_at)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 mt-1">
          <p className="text-xs text-label truncate flex-1">
            {agent.last_message_preview || "No messages yet"}
          </p>
          {agent.unread_count > 0 && (
            <span className="shrink-0 inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-cyan-500 text-white text-xs font-bold">
              {agent.unread_count}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 mt-1.5">
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${statusDotColor}`} />
          <span className={`text-xs lowercase ${statusTextColor}`}>{agent.status.toLowerCase().replace("_", " ")}</span>
          {agent.model && (
            <span className="text-[10px] text-faint font-medium px-1.5 py-0.5 rounded bg-elevated ml-auto">
              {modelDisplayName(agent.model)}
            </span>
          )}
          <span className={`text-xs text-dim ${agent.model ? "" : "ml-auto"}`}>{agent.project}</span>
        </div>
      </div>
    </button>
  );
}

export default function AgentsPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("ALL");
  const pollRef = useRef(null);

  // Multi-select state
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [bulkStopping, setBulkStopping] = useState(false);
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);

  const showToast = useCallback((message, type = "success") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const load = useCallback(async () => {
    try {
      const data = await fetchAgents();
      setAgents(Array.isArray(data) ? data : []);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    pollRef.current = setInterval(load, POLL_INTERVAL);
    return () => clearInterval(pollRef.current);
  }, [load]);

  const filtered =
    filter === "ALL"
      ? agents
      : filter === "ACTIVE"
        ? agents.filter((a) => a.status !== "STOPPED")
        : agents.filter((a) => a.status === "STOPPED");

  const enterSelectMode = () => {
    setSelecting(true);
    setSelected(new Set());
  };

  const exitSelectMode = () => {
    setSelecting(false);
    setSelected(new Set());
  };

  const toggleOne = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    setSelected(new Set(filtered.map((a) => a.id)));
  };

  const deselectAll = () => {
    setSelected(new Set());
  };

  const allSelected = filtered.length > 0 && selected.size === filtered.length;

  // Count how many selected agents are stoppable (not already stopped)
  const stoppableSelected = filtered.filter(
    (a) => selected.has(a.id) && a.status !== "STOPPED"
  );

  const handleBulkStop = async () => {
    if (stoppableSelected.length === 0) return;
    setBulkStopping(true);
    let stopped = 0;
    let failed = 0;
    for (const agent of stoppableSelected) {
      try {
        await stopAgent(agent.id);
        stopped++;
      } catch {
        failed++;
      }
    }
    setBulkStopping(false);
    if (failed > 0) {
      showToast(`Stopped ${stopped}, failed ${failed}`, "error");
    } else {
      showToast(`Stopped ${stopped} agent${stopped !== 1 ? "s" : ""}`);
    }
    setSelected(new Set());
    setSelecting(false);
    load();
  };

  return (
    <div className="h-full flex flex-col">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      <PageHeader title="Agents" theme={theme} onToggleTheme={onToggleTheme}>
        {/* Edit / Done button row */}
        {!selecting ? (
          <div className="flex items-center justify-between px-4 pb-2">
            <div className="flex gap-1">
              {FILTER_TABS.map((tab) => {
                const isActive = filter === tab.key;
                const count =
                  tab.key === "ALL"
                    ? agents.length
                    : tab.key === "ACTIVE"
                      ? agents.filter((a) => a.status !== "STOPPED").length
                      : agents.filter((a) => a.status === "STOPPED").length;
                return (
                  <button
                    key={tab.key}
                    type="button"
                    onClick={() => setFilter(tab.key)}
                    className={`min-h-[36px] px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-cyan-600 text-white"
                        : "bg-surface text-label hover:bg-input hover:text-body"
                    }`}
                  >
                    {tab.label}
                    <span className={`ml-1.5 text-xs ${isActive ? "text-cyan-200" : "text-faint"}`}>
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>
            {agents.length > 0 && (
              <button
                type="button"
                onClick={enterSelectMode}
                className="text-sm font-medium text-cyan-400 hover:text-cyan-300 transition-colors px-2 py-1"
              >
                Edit
              </button>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-between px-4 pb-2">
            <button
              type="button"
              onClick={allSelected ? deselectAll : selectAll}
              className="text-sm font-medium text-cyan-400 hover:text-cyan-300 transition-colors px-2 py-1"
            >
              {allSelected ? "Deselect All" : "Select All"}
            </button>
            <span className="text-sm text-label">
              {selected.size > 0 ? `${selected.size} selected` : "Select items"}
            </span>
            <button
              type="button"
              onClick={exitSelectMode}
              className="text-sm font-semibold text-cyan-400 hover:text-cyan-300 transition-colors px-2 py-1"
            >
              Done
            </button>
          </div>
        )}
      </PageHeader>

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      {/* Agent list */}
      <div className={`${selecting ? "pb-28" : "pb-20"} px-4 py-3 space-y-2`}>
        {loading && agents.length === 0 && (
          <div className="flex justify-center py-12">
            <span className="text-dim text-sm animate-pulse">Loading agents...</span>
          </div>
        )}

        {error && (
          <div className="bg-red-950/40 border border-red-800 rounded-xl p-4">
            <p className="text-red-400 text-sm">Failed to fetch agents: {error}</p>
            <button type="button" onClick={load} className="mt-2 text-xs text-red-300 underline hover:text-red-200">
              Retry
            </button>
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-faint">
            <svg className="w-12 h-12 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            <p className="text-sm">No agents yet</p>
            <p className="text-xs mt-1 text-ghost">Create one from the New tab</p>
          </div>
        )}

        {filtered.map((agent) => (
          <AgentRow
            key={agent.id}
            agent={agent}
            onClick={() => navigate(`/agents/${agent.id}`)}
            selecting={selecting}
            selected={selected.has(agent.id)}
            onToggle={toggleOne}
          />
        ))}

        <div className="h-4" />
      </div>
      </div>

      {/* Bottom toolbar in selection mode */}
      {selecting && selected.size > 0 && (
        <div className="fixed bottom-[calc(4rem+env(safe-area-inset-bottom,0px))] left-0 right-0 z-20 px-4 pb-2">
          <div className="max-w-xl mx-auto bg-surface border border-divider rounded-xl shadow-lg p-3 flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={handleBulkStop}
              disabled={bulkStopping || stoppableSelected.length === 0}
              className="flex items-center gap-2 px-5 min-h-[40px] rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
              {bulkStopping
                ? "Stopping..."
                : stoppableSelected.length === 0
                  ? "Stop"
                  : `Stop ${stoppableSelected.length} Agent${stoppableSelected.length !== 1 ? "s" : ""}`
              }
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
