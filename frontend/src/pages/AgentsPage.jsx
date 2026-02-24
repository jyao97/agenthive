import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { fetchAgents } from "../lib/api";
import { relativeTime } from "../lib/formatters";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS, POLL_INTERVAL } from "../lib/constants";
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

function AgentRow({ agent, onClick }) {
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

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left rounded-xl bg-surface shadow-card p-4 flex items-center gap-3 transition-colors active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 hover:ring-1 hover:ring-ring-hover"
    >
      <div className="relative shrink-0" onClick={handleCopyId} title={`Copy ID: ${agent.id}`}>
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
          <span className="text-xs text-dim ml-auto">{agent.project}</span>
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

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Agents" theme={theme} onToggleTheme={onToggleTheme}>
        <div className="flex gap-1 px-4 pb-3">
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
      </PageHeader>

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      {/* Agent list */}
      <div className="pb-20 px-4 py-3 space-y-2">
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
          />
        ))}

        <div className="h-4" />
      </div>
      </div>
    </div>
  );
}
