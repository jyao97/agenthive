import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  fetchAgent,
  fetchMessages,
  sendMessage,
  stopAgent,
  resumeAgent,
  markAgentRead,
  approveAgentPlan,
  rejectAgentPlan,
} from "../lib/api";
import { relativeTime, renderMarkdown, extractFileAttachments } from "../lib/formatters";
import FileAttachments from "../components/FilePreview";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS } from "../lib/constants";
import VoiceRecorder from "../components/VoiceRecorder";
import useVoiceRecorder from "../hooks/useVoiceRecorder";
import useWebSocket from "../hooks/useWebSocket";
import useHealthStatus from "../hooks/useHealthStatus";

// --- Chat Bubble ---

function ChatBubble({ message, project }) {
  if (message.role === "SYSTEM") {
    return (
      <div className="flex justify-center my-2">
        <span className="inline-block px-3 py-1 rounded-full bg-elevated text-xs text-dim">
          {message.content}
        </span>
      </div>
    );
  }

  const isUser = message.role === "USER";
  const isAgent = message.role === "AGENT";

  const attachments = useMemo(
    () => (isAgent ? extractFileAttachments(message.content, project) : []),
    [isAgent, message.content, project],
  );

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} my-2`}>
      <div className="max-w-[85%]">
        <div
          className={`rounded-2xl px-4 py-2.5 ${
            isUser
              ? "bg-cyan-600 text-white rounded-br-md"
              : "bg-surface shadow-card text-body rounded-bl-md"
          }`}
        >
          {isUser ? (
            <p className="text-sm whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="text-sm">
              {renderMarkdown(message.content, project)}
            </div>
          )}
          <div className={`text-xs mt-1 ${isUser ? "text-cyan-200" : "text-dim"}`}>
            {relativeTime(message.created_at)}
            {message.status === "FAILED" && (
              <span className="ml-2 text-red-400">Failed</span>
            )}
            {message.status === "TIMEOUT" && (
              <span className="ml-2 text-orange-400">Timed out</span>
            )}
          </div>
        </div>
        {attachments.length > 0 && <FileAttachments attachments={attachments} />}
      </div>
    </div>
  );
}

// --- Typing Indicator ---

function TypingIndicator() {
  return (
    <div className="flex justify-start my-2">
      <div className="bg-surface shadow-card rounded-2xl rounded-bl-md px-4 py-3 flex items-center gap-1.5">
        <span className="w-2 h-2 rounded-full bg-dim animate-bounce" style={{ animationDelay: "0ms" }} />
        <span className="w-2 h-2 rounded-full bg-dim animate-bounce" style={{ animationDelay: "150ms" }} />
        <span className="w-2 h-2 rounded-full bg-dim animate-bounce" style={{ animationDelay: "300ms" }} />
      </div>
    </div>
  );
}

// --- Plan Review Bar ---

function PlanReviewBar({ onApprove, onReject }) {
  const [rejecting, setRejecting] = useState(false);
  const [notes, setNotes] = useState("");

  if (rejecting) {
    return (
      <div className="bg-surface border border-divider rounded-xl p-3 my-2 space-y-2">
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Revision feedback..."
          rows={2}
          className="w-full rounded-lg bg-input border border-edge px-3 py-2 text-sm text-heading placeholder-hint resize-none focus:border-cyan-500 focus:outline-none"
        />
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => { onReject(notes); setRejecting(false); setNotes(""); }}
            disabled={!notes.trim()}
            className="flex-1 min-h-[36px] rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
          >
            Submit Rejection
          </button>
          <button
            type="button"
            onClick={() => setRejecting(false)}
            className="px-3 min-h-[36px] rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-2 my-2 justify-center">
      <button
        type="button"
        onClick={onApprove}
        className="px-5 min-h-[36px] rounded-lg bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition-colors"
      >
        Approve Plan
      </button>
      <button
        type="button"
        onClick={() => setRejecting(true)}
        className="px-5 min-h-[36px] rounded-lg bg-red-600/20 text-red-400 text-sm font-medium hover:bg-red-600/30 transition-colors"
      >
        Reject
      </button>
    </div>
  );
}

// --- Chat Input ---

function ChatInput({ onSend, disabled, disabledReason }) {
  const [text, setText] = useState("");
  const textareaRef = useRef(null);

  const voice = useVoiceRecorder({
    onTranscript: (t) => setText((prev) => (prev ? prev + " " + t : t)),
    onError: () => {},
  });

  // Auto-grow textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  }, [text]);

  const handleSend = () => {
    if (!text.trim() || disabled) return;
    onSend(text.trim());
    setText("");
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-t border-divider bg-surface px-3 py-2 safe-area-pb">
      <div className="flex items-end gap-2 max-w-2xl mx-auto">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? disabledReason : "Type a message..."}
          disabled={disabled}
          rows={1}
          className="flex-1 min-h-[40px] max-h-[160px] rounded-xl bg-input border border-edge px-3 py-2.5 text-sm text-heading placeholder-hint resize-none focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 transition-colors disabled:opacity-50"
        />
        <VoiceRecorder
          recording={voice.recording}
          voiceLoading={voice.voiceLoading}
          analyserNode={voice.analyserNode}
          micError={voice.micError}
          onToggle={voice.toggleRecording}
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={disabled || !text.trim()}
          className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
            disabled || !text.trim()
              ? "bg-elevated text-dim cursor-not-allowed"
              : "bg-cyan-500 hover:bg-cyan-400 text-white"
          }`}
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
          </svg>
        </button>
      </div>
    </div>
  );
}

// --- Main Page ---

export default function AgentChatPage({ theme, onToggleTheme }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [agent, setAgent] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [resuming, setResuming] = useState(false);
  const messagesEndRef = useRef(null);
  const toastTimer = useRef(null);
  const health = useHealthStatus();

  const showToast = useCallback((message, type = "success") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  // Load agent + messages
  const loadData = useCallback(async () => {
    try {
      const [agentData, msgData] = await Promise.all([
        fetchAgent(id),
        fetchMessages(id),
      ]);
      setAgent(agentData);
      setMessages(msgData);
    } catch {
      // silently retry
    } finally {
      setLoading(false);
    }
  }, [id]);

  // Initial load
  useEffect(() => {
    loadData();
  }, [loadData]);

  // Polling — faster when executing
  useEffect(() => {
    const isExecuting = agent?.status === "EXECUTING" || agent?.status === "PLANNING";
    const interval = isExecuting ? 3000 : 10000;
    const timer = setInterval(loadData, interval);
    return () => clearInterval(timer);
  }, [loadData, agent?.status]);

  // Mark as read on mount and when new messages arrive
  useEffect(() => {
    if (agent && agent.unread_count > 0) {
      markAgentRead(id).catch(() => {});
    }
  }, [id, messages.length, agent?.unread_count]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  // WebSocket: re-fetch on new_message events for this agent
  const { lastEvent } = useWebSocket();
  useEffect(() => {
    if (!lastEvent) return;
    if (
      (lastEvent.type === "new_message" && lastEvent.data?.agent_id === id) ||
      (lastEvent.type === "agent_update" && lastEvent.data?.agent_id === id)
    ) {
      loadData();
    }
  }, [lastEvent, id, loadData]);

  // Cleanup
  useEffect(() => {
    return () => { if (toastTimer.current) clearTimeout(toastTimer.current); };
  }, []);

  // Send message
  const handleSend = async (content) => {
    try {
      await sendMessage(id, content);
      loadData();
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    }
  };

  // Approve plan
  const handleApprove = async () => {
    try {
      await approveAgentPlan(id);
      showToast("Plan approved!");
      loadData();
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    }
  };

  // Reject plan
  const handleReject = async (notes) => {
    try {
      await rejectAgentPlan(id, notes);
      showToast("Plan rejected — re-queued");
      loadData();
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    }
  };

  // Stop agent
  const handleStop = async () => {
    setStopping(true);
    try {
      await stopAgent(id);
      showToast("Agent stopped");
      loadData();
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setStopping(false);
      setShowStopConfirm(false);
    }
  };

  // Resume agent
  const handleResume = async () => {
    setResuming(true);
    try {
      await resumeAgent(id);
      showToast("Agent resumed");
      loadData();
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setResuming(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <span className="text-dim text-sm animate-pulse">Loading...</span>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-faint">
        <p>Agent not found</p>
        <button type="button" onClick={() => navigate("/agents")} className="mt-2 text-sm text-cyan-400 underline">
          Back to Agents
        </button>
      </div>
    );
  }

  const isHealthy = health && health.status === "ok" && health.db === "ok" && health.docker === "ok";
  const healthChipCls = health === null
    ? "bg-gray-500/15 text-gray-400"
    : isHealthy
      ? "bg-green-500/15 text-green-500"
      : "bg-red-500/15 text-red-400";
  const healthDotColor = health === null ? "bg-gray-400" : isHealthy ? "bg-green-500" : "bg-red-500";
  const healthLabel = health === null ? "..." : isHealthy ? "OK" : "Error";

  const statusDot = AGENT_STATUS_COLORS[agent.status] || "bg-gray-500";
  const statusText = AGENT_STATUS_TEXT_COLORS[agent.status] || "text-dim";
  const isExecuting = agent.status === "EXECUTING" || agent.status === "PLANNING";
  const isStopped = agent.status === "STOPPED";
  const isError = agent.status === "ERROR";
  const isPlanReview = agent.status === "PLAN_REVIEW";

  let disabledReason = "";
  if (isStopped) disabledReason = "Agent is stopped — click Resume to restart";
  else if (isError) disabledReason = "Agent errored — click Resume to restart";
  else if (isExecuting) disabledReason = "Agent is working...";

  return (
    <div className="flex flex-col h-full">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      {/* Fixed Header */}
      <div className="fixed top-0 left-0 right-0 z-10 bg-surface border-b border-divider px-4 py-3">
        <div className="flex items-center gap-2 max-w-2xl mx-auto">
          <button
            type="button"
            onClick={() => navigate("/agents")}
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
          >
            <svg className="w-5 h-5 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
          </button>

          <div className="min-w-0 flex-1">
            <h1 className="text-sm font-semibold text-heading truncate">{agent.name}</h1>
            <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${statusDot}`} />
              <span className={`text-xs ${statusText}`}>{agent.status.toLowerCase().replace("_", " ")}</span>
              <span className="text-xs text-dim">{agent.project}</span>
              {agent.branch && (
                <span className="text-xs text-violet-400 font-mono truncate max-w-[120px]">
                  {agent.branch}
                </span>
              )}
            </div>
          </div>

          {isStopped || isError ? (
            <button
              type="button"
              onClick={handleResume}
              disabled={resuming}
              className="shrink-0 px-3 h-8 flex items-center gap-1.5 rounded-lg text-xs font-medium bg-cyan-600 hover:bg-cyan-500 text-white transition-colors disabled:opacity-50"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 3l14 9-14 9V3z" />
              </svg>
              {resuming ? "Resuming..." : "Resume"}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setShowStopConfirm(true)}
              className="shrink-0 px-3 h-8 flex items-center gap-1.5 rounded-lg text-xs font-medium text-red-400 hover:bg-red-600/20 transition-colors"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
              Stop
            </button>
          )}

          {/* Monitor chip */}
          <button
            type="button"
            onClick={() => navigate("/monitor")}
            title={health === null ? "Checking..." : isHealthy ? "System healthy" : "System issue"}
            className={`shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium transition-colors hover:opacity-80 ${healthChipCls}`}
          >
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${healthDotColor} ${!isHealthy && health !== null ? "animate-pulse" : ""}`} />
            {healthLabel}
          </button>

          {/* Theme toggle */}
          <button
            type="button"
            onClick={onToggleTheme}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-dim hover:text-heading hover:bg-input transition-colors"
          >
            {theme === "dark" ? (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
              </svg>
            ) : (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Messages — offset for fixed header */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden px-4 py-3 pt-[70px] max-w-2xl mx-auto w-full">
        {messages.map((msg) => (
          <ChatBubble key={msg.id} message={msg} project={agent.project} />
        ))}

        {/* Plan review inline */}
        {isPlanReview && (
          <PlanReviewBar onApprove={handleApprove} onReject={handleReject} />
        )}

        {/* Typing indicator */}
        {isExecuting && <TypingIndicator />}

        <div ref={messagesEndRef} />
      </div>

      {/* Input bar */}
      <ChatInput
        onSend={handleSend}
        disabled={isStopped || isError || isExecuting}
        disabledReason={disabledReason}
      />

      {/* Stop confirmation modal */}
      {showStopConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-surface rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-card">
            <h3 className="text-lg font-bold text-heading">Stop Agent?</h3>
            <p className="text-sm text-label">
              This will stop and remove the agent's container. You won't be able to send more messages.
            </p>
            <div className="flex gap-3">
              <button
                type="button"
                disabled={stopping}
                onClick={handleStop}
                className="flex-1 min-h-[44px] rounded-lg bg-red-600 hover:bg-red-500 text-white font-semibold text-sm transition-colors disabled:opacity-50"
              >
                {stopping ? "Stopping..." : "Stop"}
              </button>
              <button
                type="button"
                onClick={() => setShowStopConfirm(false)}
                className="flex-1 min-h-[44px] rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
