import React, { useState, useEffect, useLayoutEffect, useCallback, useRef, useMemo, Component } from "react";
import { Bell, BellOff, Sparkles, X as XIcon } from "lucide-react";
import SkillPickerPanel, { bumpSkillUsage } from "../components/SkillPickerPanel";
import { useParams, useNavigate } from "react-router-dom";
import {
  fetchAgent,
  fetchDisplay,
  sendMessage,
  stopAgent,
  resumeAgent,
  renameAgent,
  markAgentRead,
  fetchProjectSessions,
  starSession,
  unstarSession,
  cancelMessage,
  updateMessage,
  updateAgent,
  answerAgent,
  escapeAgent,
  cyclePermissionMode,
  uploadFile,
  fetchProjectFile,
  respondPermission,
  fetchAgentSuggestions,
  fetchProcessedSuggestions,
  applyAgentSuggestions,
  discardAgentSuggestions,
  regenerateAgentInsights,
  fetchTaskV2,
  wakeSync,
} from "../lib/api";
import ProjectFileModal from "../components/ProjectFileModal";
import FloatingTaskCard from "../components/FloatingTaskCard";
import ProjectBrowserModal from "../components/ProjectBrowserModal";
import { relativeTime, renderMarkdown, extractFileAttachments, stripAttachmentTags } from "../lib/formatters";
import { serverNow } from "../lib/serverTime";
import { uploadUrl } from "../lib/urls";

const PERM_MODES = [
  { key: "normal", label: "Normal", active: "bg-zinc-600 text-white", idle: "text-dim hover:text-body hover:bg-input" },
  { key: "auto", label: "Auto", active: "bg-emerald-600 text-white", idle: "text-dim hover:text-body hover:bg-input" },
  { key: "plan", label: "Plan", active: "bg-amber-600 text-white", idle: "text-dim hover:text-body hover:bg-input" },
];

function PermModeSegmented({ mode, onSelect, disabled, compact }) {
  const size = compact ? "h-6 text-[10px] px-1.5" : "h-7 text-xs px-2";
  return (
    <div className={`inline-flex items-center rounded-lg bg-input/60 border border-divider/40 p-0.5 ${disabled ? "opacity-60" : ""}`}>
      {PERM_MODES.map((m) => {
        const active = mode === m.key;
        return (
          <button
            key={m.key}
            type="button"
            disabled={disabled || active}
            onClick={() => onSelect(m.key)}
            title={`Switch to ${m.label}`}
            className={`${size} flex items-center rounded-md font-medium transition-colors ${active ? m.active : m.idle}`}
          >
            {m.label}
          </button>
        );
      })}
    </div>
  );
}

// Mini error boundary that wraps individual markdown renders so a single
// broken message doesn't crash the entire chat page.
class SafeMarkdown extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(error, info) {
    console.error("SafeMarkdown caught:", error, info);
  }
  render() {
    if (this.state.hasError) {
      return <pre className="text-sm text-body whitespace-pre-wrap">{this.props.fallback || "Error rendering message"}</pre>;
    }
    return this.props.children;
  }
}
import FileAttachments from "../components/FilePreview";
import ImageLightbox from "../components/ImageLightbox";
import {
  AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS, modelDisplayName,
  POLL_ACTIVE_INTERVAL, POLL_IDLE_INTERVAL, STREAM_TIMEOUT,
  COPY_TOAST_DURATION, ERROR_TOAST_DURATION, TOAST_DURATION,
  ESCAPE_COOLDOWN, LONG_PRESS_DELAY, DOUBLE_TAP_WINDOW,
  SCROLL_SAVE_DEBOUNCE, isSystemHealthy,
} from "../lib/constants";
import { DATE_SHORT, TIME_SHORT } from "../lib/formatters";
import VoiceRecorder from "../components/VoiceRecorder";
import useDraft from "../hooks/useDraft";
import useVoiceRecorder from "../hooks/useVoiceRecorder";
import useWebSocket, { useWsEvent, isAgentMuted, setAgentMuted, clearAgentNotified, registerViewing, unregisterViewing } from "../hooks/useWebSocket";
import useHealthStatus from "../hooks/useHealthStatus";
import usePageVisible from "../hooks/usePageVisible";
import { useToast } from "../contexts/ToastContext";

const ACTIVE_AGENT_STATUSES = new Set(["EXECUTING", "IDLE"]);
// --- Chat Bubble ---

function SystemBubble({ message }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = message.content.length > 80;
  const label = isLong
    ? message.content.slice(0, 60).replace(/\n/g, " ") + "..."
    : message.content;

  return (
    <div className="flex justify-center my-2">
      <button
        type="button"
        onClick={() => isLong && setExpanded((v) => !v)}
        className={`inline-block max-w-[80%] px-3 py-1 rounded-lg bg-elevated text-xs text-dim text-left ${isLong ? "cursor-pointer hover:bg-hover transition-colors" : "cursor-default"}`}
      >
        <div className="flex items-center gap-1.5">
          <span className="shrink-0 opacity-60">sys</span>
          <span className="truncate">{label}</span>
          <span className="shrink-0 opacity-40 ml-1">{relativeTime(message.completed_at || message.created_at)}</span>
          {isLong && (
            <svg className={`w-3 h-3 shrink-0 opacity-50 transition-transform ${expanded ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" d="m19 9-7 7-7-7" />
            </svg>
          )}
        </div>
        {expanded && (
          <div className="mt-2 pt-2 border-t border-divider text-xs text-dim whitespace-pre-wrap break-words max-h-60 overflow-y-auto">
            {message.content}
          </div>
        )}
      </button>
    </div>
  );
}

// --- Stop note card (Redo / Drop) ---
function StopNoteCard({ message }) {
  const meta = message.metadata || {};
  const isDrop = meta.stop_action ? meta.stop_action === "dropped" : message.content.startsWith("Task dropped");
  const label = isDrop ? "Dropped" : "Redo";
  const note = meta.stop_action ? message.content : message.content.replace(/^(Task dropped|Redo)\s*—\s*/, "");
  const color = isDrop ? "gray" : "amber";
  return (
    <div className={`mx-4 my-3 rounded-xl border px-4 py-3 ${
      isDrop
        ? "bg-gray-500/8 border-gray-500/20"
        : "bg-amber-500/8 border-amber-500/20"
    }`}>
      <div className={`text-[11px] font-semibold mb-1 ${
        isDrop ? "text-gray-400" : "text-amber-500"
      }`}>{label}</div>
      {note && <div className="text-sm text-body whitespace-pre-wrap">{note}</div>}
      <div className="text-[10px] text-dim mt-1">{relativeTime(message.completed_at || message.created_at)}</div>
    </div>
  );
}

// --- Sub-agent task notification bubble (collapsible) ---

function SubAgentBubble({ message, project }) {
  const [expanded, setExpanded] = useState(false);
  const tn = message.metadata?.task_notification;
  const content = message.content || "";
  const status = tn?.status || content.match(/<status>(.*?)<\/status>/s)?.[1] || "";
  const summary = tn?.summary || content.match(/<summary>([\s\S]*?)<\/summary>/)?.[1] || "";
  const result = tn?.result ?? content.match(/<result>([\s\S]*?)<\/result>/)?.[1]?.trim();

  const statusColor = status === "completed"
    ? "text-emerald-400"
    : status === "failed" ? "text-red-400" : "text-amber-400";
  const borderColor = status === "completed"
    ? "border-emerald-500/30"
    : status === "failed" ? "border-red-500/30" : "border-amber-500/30";

  return (
    <div className="flex justify-start my-2">
      <div className="max-w-[min(85%,30rem)]">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className={`w-full text-left rounded-2xl px-4 py-2.5 border ${borderColor} bg-purple-900/20 cursor-pointer hover:bg-purple-900/30 transition-colors rounded-bl-md`}
        >
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-purple-400">Sub-agent</span>
            <span className={`text-xs font-medium ${statusColor}`}>{status}</span>
            <svg className={`w-3 h-3 shrink-0 text-dim transition-transform ml-auto ${expanded ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" d="m19 9-7 7-7-7" />
            </svg>
          </div>
          {summary && <p className="text-sm text-body mt-1">{summary}</p>}
        </button>
        {expanded && result && (
          <div className={`mt-0 border ${borderColor} border-t-0 rounded-b-2xl bg-purple-900/10 px-4 py-3 max-h-[400px] overflow-y-auto`}>
            <div className="text-sm">
              <SafeMarkdown fallback={result}>
                {renderMarkdown(result, project)}
              </SafeMarkdown>
            </div>
          </div>
        )}
        <div className="text-xs text-dim mt-1 px-1">
          {relativeTime(message.completed_at || message.created_at)}
          {message.source && (
            <span className={`ml-1.5 px-1 py-0.5 rounded text-[10px] font-medium leading-none ${
              message.source === "web"
                ? "bg-cyan-500/20 text-cyan-300"
                : "bg-emerald-500/20 text-emerald-300"
            }`}>
              {message.source}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// --- RAG Insights bubble (collapsible, shown on user messages) ---

function InsightsBubble({ insights }) {
  const [expanded, setExpanded] = useState(false);
  if (!insights || insights.length === 0) return null;
  return (
    <div className="rounded-lg bg-elevated overflow-hidden max-w-[280px] mt-1.5 ml-auto">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-hover transition-colors text-left"
      >
        <svg className="w-4 h-4 text-cyan-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
        <span className="text-xs text-label flex-1 min-w-0">Past Insights ({insights.length})</span>
        <svg className={`w-3 h-3 text-dim shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" d="m19 9-7 7-7-7" />
        </svg>
      </button>
      {expanded && (
        <div className="border-t border-divider max-h-60 overflow-y-auto">
          {insights.map((insight, i) => {
            const dateMatch = insight.match(/^\[(\d{4}-\d{2}-\d{2})\]\s*/);
            const date = dateMatch ? dateMatch[1] : null;
            const text = dateMatch ? insight.slice(dateMatch[0].length) : insight;
            return (
              <div key={i} className="flex items-start gap-2 px-3 py-1.5 text-left">
                {date && <span className="shrink-0 text-[10px] text-dim font-mono mt-0.5">{date}</span>}
                <span className="text-xs text-label">{text}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- Interactive: AskUserQuestion ---

function QuestionBubble({ item, agentId, onAnswered }) {
  // Per-question state: { [questionIndex]: optionIndex }
  const [chosenIndices, setChosenIndices] = useState({});
  // Track which question is currently submitting (null = none)
  const [submittingQi, setSubmittingQi] = useState(null);
  const [submitError, setSubmitError] = useState(null);

  const questions = item.questions || [];
  // Detect dismissed/escaped answers (not a real selection)
  const isDismissed = item.answer != null && typeof item.answer === "string" &&
    (item.answer.startsWith("The user doesn't want to proceed") ||
     item.answer.startsWith("User declined") ||
     item.answer.startsWith("Tool use rejected"));

  const handleSubmit = async (qi, idx) => {
    setChosenIndices(prev => ({ ...prev, [qi]: idx }));
    setSubmittingQi(qi);
    setSubmitError(null);
    try {
      await answerAgent(agentId, {
        tool_use_id: item.tool_use_id,
        type: "ask_user_question",
        selected_index: idx,
        question_index: qi,
      });
      onAnswered?.();
    } catch (e) {
      console.error("Failed to answer:", e);
      setSubmitError("Failed to send answer: " + (e.message || "Unknown error"));
      setChosenIndices(prev => { const next = { ...prev }; delete next[qi]; return next; });
    } finally {
      setSubmittingQi(null);
    }
  };

  // Resolve which option index was selected for a specific question
  const resolveIdx = (q, qi) => {
    // Dismissed = no valid selection, regardless of stored indices
    if (isDismissed) return null;
    // 1. Best: per-question index from backend (new field)
    if (item.selected_indices && item.selected_indices[String(qi)] != null)
      return item.selected_indices[String(qi)];
    // 2. Parse the answer string: use positional matching (qi-th ="label"
    //    pair corresponds to qi-th question) to avoid cross-question leakage
    if (item.answer != null && typeof item.answer === "string") {
      const allMatches = [...item.answer.matchAll(/="([^"]+)"/g)];
      // For single-question: try all matches against this question's options
      if (questions.length <= 1) {
        for (const m of allMatches) {
          const idx = (q.options || []).findIndex((o) => o.label === m[1]);
          if (idx !== -1) return idx;
        }
      } else {
        // Multi-question: match labels to questions in order (consumed set)
        const used = new Set();
        for (let qj = 0; qj <= qi; qj++) {
          const opts = (questions[qj]?.options || []);
          for (let mi = 0; mi < allMatches.length; mi++) {
            if (used.has(mi)) continue;
            const matchedOi = opts.findIndex((o) => o.label === allMatches[mi][1]);
            if (matchedOi !== -1) {
              used.add(mi);
              if (qj === qi) return matchedOi;
              break; // move to next question
            }
          }
        }
      }
    }
    // 3. Local optimistic choice for this question
    if (chosenIndices[qi] != null) return chosenIndices[qi];
    // 4. Backward compat: selected_index only applies to first question
    if (qi === 0 && item.selected_index != null) return item.selected_index;
    return null;
  };

  return (
    <div className="mt-3 space-y-3">
      {questions.map((q, qi) => {
        const answeredIdx = resolveIdx(q, qi);
        const isAnswered = answeredIdx != null || isDismissed || item.auto_approved;
        // Sequential lock: question qi is locked if any prior question is unanswered
        const isLocked = qi > 0 && !isDismissed && resolveIdx(questions[qi - 1], qi - 1) == null;

        // Badge text & style
        let badgeText = null;
        let badgeClass = "";
        if (isDismissed) {
          badgeText = "Dismissed";
          badgeClass = "bg-red-500/20 text-red-300";
        } else if (item.auto_approved) {
          badgeText = "Auto-approved";
          badgeClass = "bg-amber-500/20 text-amber-300";
        } else if (answeredIdx != null) {
          badgeText = "Choice Sent";
          badgeClass = "bg-cyan-500/20 text-cyan-300";
        }

        return (
          <div key={qi} className={`rounded-xl bg-indigo-500/10 border border-indigo-500/20 p-3 ${isLocked ? "opacity-50" : ""}`}>
            <div className="flex items-center gap-2 mb-1.5">
              {q.header && (
                <span className="inline-block px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 text-[10px] font-semibold uppercase tracking-wider">
                  {q.header}
                </span>
              )}
              {badgeText && (
                <span className={`ml-auto px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider ${badgeClass}`}>
                  {badgeText}
                </span>
              )}
            </div>
            <p className="text-sm text-heading font-medium mb-2">{q.question}</p>
            {isLocked && (
              <p className="text-xs text-dim mb-2 italic">Answer above first</p>
            )}
            <div className="space-y-1.5 max-h-80 overflow-y-auto">
              {(q.options || []).map((opt, oi) => {
                const isChosen = answeredIdx === oi;
                const dimmed = isAnswered && !isChosen;
                const disabled = isAnswered || isLocked || submittingQi === qi;

                return (
                  <button
                    key={oi}
                    type="button"
                    disabled={disabled}
                    onClick={() => {
                      if (!isAnswered && !isLocked) handleSubmit(qi, oi);
                    }}
                    className={`w-full text-left rounded-lg px-3 py-2 text-sm transition-all border ${
                      isChosen
                        ? "bg-cyan-500/20 border-cyan-500/40 text-heading"
                        : dimmed
                          ? "bg-surface/30 border-divider/30 text-dim/50"
                          : isLocked
                            ? "bg-surface/30 border-divider/30 text-dim/50 cursor-not-allowed"
                            : "bg-surface/50 border-divider hover:bg-hover hover:border-heading/20 text-body"
                    } ${isAnswered || isLocked ? "cursor-default" : "cursor-pointer"}`}
                  >
                    <div className="flex items-start gap-2">
                      <span className={`mt-0.5 w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center ${
                        isChosen ? "border-cyan-400 bg-cyan-400" : "border-dim/40"
                      }`}>
                        {isChosen && (
                          <svg className="w-2.5 h-2.5 text-white" fill="currentColor" viewBox="0 0 20 20">
                            <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                          </svg>
                        )}
                      </span>
                      <div>
                        <span className="font-medium">{opt.label}</span>
                        {opt.description && (
                          <p className={`text-xs mt-0.5 ${dimmed ? "text-dim/30" : "text-dim"}`}>{opt.description}</p>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
            {submittingQi === qi && (
              <p className="text-xs text-dim mt-2 flex items-center gap-1.5">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
                Sending answer...
              </p>
            )}
          </div>
        );
      })}
      {submitError && (
        <p className="text-xs text-red-400 mt-2 px-1">{submitError}</p>
      )}
    </div>
  );
}

// --- Interactive: Native Permission Prompt ---

const PERM_PROMPT_OPTIONS = [
  { label: "Yes", description: "Allow this operation", color: "emerald" },
  { label: "Yes, and always allow", description: "Don't ask again for this scope", color: "amber" },
  { label: "No", description: "Block this operation", color: "red" },
];

function PermissionPromptBubble({ item, agentId, onAnswered }) {
  const [chosenIdx, setChosenIdx] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const q = (item.questions || [])[0] || {};
  const options = q.options || PERM_PROMPT_OPTIONS;
  const isDismissed = item.answer != null && typeof item.answer === "string" &&
    (item.answer.startsWith("The user doesn't want to proceed") ||
     item.answer.startsWith("User declined") ||
     item.answer.startsWith("Tool use rejected"));

  // Resolve answered index
  const resolveIdx = () => {
    if (isDismissed) return null;
    if (item.selected_index != null) return item.selected_index;
    if (item.answer != null && typeof item.answer === "string") {
      const idx = options.findIndex((o) => (o.label || o) === item.answer);
      if (idx !== -1) return idx;
    }
    if (chosenIdx != null) return chosenIdx;
    return null;
  };

  const answeredIdx = resolveIdx();
  const isAnswered = answeredIdx != null || isDismissed || item.auto_approved;

  const handleSelect = async (idx) => {
    setChosenIdx(idx);
    setSubmitting(true);
    setError(null);
    try {
      if (item.request_id) {
        // Hook-based permission: call respondPermission API
        const decisionMap = ["allow", "allow_always", "deny"];
        await respondPermission(agentId, item.request_id, {
          decision: decisionMap[idx] || "deny",
          tool_name: item.tool_name,
        });
      } else {
        // Native CC permission prompt: send tmux keys via answerAgent
        await answerAgent(agentId, {
          tool_use_id: item.tool_use_id,
          type: "permission_prompt",
          selected_index: idx,
        });
      }
      onAnswered?.();
    } catch (e) {
      setError("Failed: " + (e.message || "Unknown error"));
      setChosenIdx(null);
    } finally {
      setSubmitting(false);
    }
  };

  // Badge
  let badgeText = null;
  let badgeClass = "";
  if (isDismissed) {
    badgeText = "Dismissed";
    badgeClass = "bg-red-500/20 text-red-300";
  } else if (item.auto_approved) {
    badgeText = "Auto-approved";
    badgeClass = "bg-amber-500/20 text-amber-300";
  } else if (item.answer === "Timed out") {
    badgeText = "Timed out";
    badgeClass = "bg-red-500/20 text-red-300";
  } else if (answeredIdx != null) {
    const opt = options[answeredIdx];
    const optLabel = (opt?.label || opt || "").toLowerCase();
    if (optLabel.startsWith("no") || optLabel === "deny") {
      badgeText = "Denied"; badgeClass = "bg-red-500/20 text-red-300";
    } else {
      badgeText = "Allowed"; badgeClass = "bg-emerald-500/20 text-emerald-300";
    }
  }

  const colorMap = {
    emerald: { active: "bg-emerald-500/20 border-emerald-500/40 text-heading", dot: "border-emerald-400 bg-emerald-400" },
    amber:   { active: "bg-amber-500/20 border-amber-500/40 text-heading", dot: "border-amber-400 bg-amber-400" },
    red:     { active: "bg-red-500/20 border-red-500/40 text-heading", dot: "border-red-400 bg-red-400" },
  };

  return (
    <div className="mt-3 rounded-xl bg-amber-500/10 border border-amber-500/20 p-3">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="inline-block px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 text-[10px] font-semibold uppercase tracking-wider">
          Permission
        </span>
        {item.tool_name && (
          <span className="text-xs text-dim font-mono">{item.tool_name}</span>
        )}
        {badgeText && (
          <span className={`ml-auto px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider ${badgeClass}`}>
            {badgeText}
          </span>
        )}
      </div>
      <p className="text-sm text-heading font-medium mb-2">{q.question || "Permission required"}</p>
      <div className="space-y-1.5">
        {options.map((opt, oi) => {
          const label = opt.label || opt;
          const desc = opt.description || "";
          const clr = opt.color || (oi === 0 ? "emerald" : oi === options.length - 1 ? "red" : "amber");
          const isChosen = answeredIdx === oi;
          const dimmed = isAnswered && !isChosen;
          const disabled = isAnswered || submitting;
          const cs = colorMap[clr] || colorMap.amber;

          return (
            <button
              key={oi}
              type="button"
              disabled={disabled}
              onClick={() => { if (!isAnswered) handleSelect(oi); }}
              className={`w-full text-left rounded-lg px-3 py-2 text-sm transition-all border ${
                isChosen
                  ? cs.active
                  : dimmed
                    ? "bg-surface/30 border-divider/30 text-dim/50"
                    : "bg-surface/50 border-divider hover:bg-hover hover:border-heading/20 text-body"
              } ${disabled ? "cursor-default" : "cursor-pointer"}`}
            >
              <div className="flex items-start gap-2">
                <span className={`mt-0.5 w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center ${
                  isChosen ? cs.dot : "border-dim/40"
                }`}>
                  {isChosen && (
                    <svg className="w-2.5 h-2.5 text-white" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                    </svg>
                  )}
                </span>
                <div>
                  <span className="font-medium">{label}</span>
                  {desc && <p className={`text-xs mt-0.5 ${dimmed ? "text-dim/30" : "text-dim"}`}>{desc}</p>}
                </div>
              </div>
            </button>
          );
        })}
      </div>
      {submitting && (
        <p className="text-xs text-dim mt-2 flex items-center gap-1.5">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
          Sending response...
        </p>
      )}
      {error && <p className="text-xs text-red-400 mt-2 px-1">{error}</p>}
    </div>
  );
}

// --- Interactive: ExitPlanMode ---

// Must match the exact order Claude CLI shows in the ExitPlanMode TUI
// Note: "clear context" option was hidden by default in Claude Code v2.1.81
// (can be restored with showClearContextOnPlanAccept setting)
const PLAN_OPTIONS = [
  { label: "Yes, bypass permissions", description: "Approve and skip permission prompts", color: "emerald" },
  { label: "Yes, manual approval", description: "Approve but require manual approval for each edit", color: "amber" },
  { label: "Give feedback", description: "Type feedback for Claude to revise the plan", color: "indigo" },
];

function _detectPlanIdx(answer) {
  if (answer == null || typeof answer !== "string") return null;
  // Dismissed / escaped answers
  if (answer.startsWith("The user doesn't want to proceed") || answer.startsWith("User declined") || answer.startsWith("Tool use rejected")) return null;
  const a = answer.toLowerCase().trim();
  // Exact label match first (avoids keyword collision like "bypass manual")
  const exactLabels = PLAN_OPTIONS.map((o) => o.label.toLowerCase());
  const exactIdx = exactLabels.indexOf(a);
  if (exactIdx !== -1) return exactIdx;
  // Keyword fallback for answers from Claude's tool_result (may differ in wording)
  if (/bypass/.test(a) && !/manual/.test(a)) return 0;
  if (/manual/.test(a)) return 1;
  if (/feedback|type here|tell claude/.test(a)) return 2;
  if (/^yes\b/.test(a) || a === "approve" || a === "approved") return 1; // safe default: manual approval
  if (/^no\b/.test(a) || a === "reject") return 2; // feedback mode, not destructive
  return null; // unrecognized input — do not select any option
}

function _isPlanDismissed(item) {
  if (!item.answer || typeof item.answer !== "string") return false;
  return item.answer.startsWith("The user doesn't want to proceed") || item.answer.startsWith("User declined") || item.answer.startsWith("Tool use rejected");
}

function ProgressSuggestionsCard({ agentId, onDone }) {
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(new Set());
  const [edits, setEdits] = useState({});
  const [editingId, setEditingId] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null); // "applied" | "discarded"

  useEffect(() => {
    fetchAgentSuggestions(agentId)
      .then((data) => {
        setSuggestions(data);
        setSelected(new Set(data.map((s) => s.id)));
      })
      .catch((err) => console.error("Failed to fetch suggestions:", err))
      .finally(() => setLoading(false));
  }, [agentId]);

  const handleApply = async () => {
    setSubmitting(true);
    try {
      const accepted = [...selected].map((id) => ({
        id,
        ...(edits[id] ? { edited_content: edits[id] } : {}),
      }));
      const rejected_ids = suggestions
        .filter((s) => !selected.has(s.id))
        .map((s) => s.id);
      await applyAgentSuggestions(agentId, { accepted, rejected_ids });
      setResult("applied");
      onDone?.();
    } catch (err) {
      console.error("Failed to apply suggestions:", err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDiscard = async () => {
    setSubmitting(true);
    try {
      await discardAgentSuggestions(agentId);
      setResult("discarded");
      onDone?.();
    } catch (err) {
      console.error("Failed to discard suggestions:", err);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return null;
  if (!suggestions.length && !result) return null;

  if (result) {
    return (
      <div className="mt-4 rounded-xl bg-amber-500/10 border border-amber-500/20 p-4">
        <p className="text-sm text-amber-300 font-medium">
          {result === "applied" ? "Insights applied to PROGRESS.md" : "Suggestions dismissed"}
        </p>
      </div>
    );
  }

  return (
    <div className="mt-4 rounded-xl bg-amber-500/10 border border-amber-500/20 p-4">
      <div className="flex items-center gap-2 mb-3">
        <svg className="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
        <span className="text-sm font-medium text-amber-300">Progress Insights</span>
        <span className="text-xs text-dim ml-auto">{selected.size}/{suggestions.length} selected</span>
      </div>
      <div className="space-y-2 mb-3">
        {suggestions.map((s) => (
          <div key={s.id} className="flex items-start gap-2 group">
            <input
              type="checkbox"
              checked={selected.has(s.id)}
              onChange={() => {
                setSelected((prev) => {
                  const next = new Set(prev);
                  if (next.has(s.id)) next.delete(s.id);
                  else next.add(s.id);
                  return next;
                });
              }}
              className="mt-1 w-4 h-4 rounded border-divider bg-input text-amber-500 focus:ring-amber-500/50 shrink-0"
            />
            <div className="flex-1 min-w-0">
              {editingId === s.id ? (
                <textarea
                  autoFocus
                  rows={3}
                  value={edits[s.id] ?? s.content}
                  onChange={(e) => setEdits((prev) => ({ ...prev, [s.id]: e.target.value }))}
                  onBlur={() => setEditingId(null)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); setEditingId(null); } }}
                  className="w-full bg-input border border-divider rounded px-2 py-1 text-sm text-body focus:outline-none focus:ring-1 focus:ring-amber-500/50 resize-y"
                />
              ) : (
                <div className="flex items-start gap-1">
                  <p
                    className="text-sm text-body leading-relaxed flex-1 min-w-0 cursor-pointer select-none"
                    onDoubleClick={() => setEditingId(s.id)}
                  >
                    {edits[s.id] ?? s.content}
                  </p>
                  <button
                    type="button"
                    onClick={() => setEditingId(s.id)}
                    className="text-dim hover:text-amber-400 transition-colors shrink-0 mt-0.5 p-0.5"
                    title="Edit"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                    </svg>
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={submitting || selected.size === 0}
          onClick={handleApply}
          className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-semibold transition-colors disabled:opacity-50"
        >
          {submitting ? "Applying..." : `Apply Selected (${selected.size})`}
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={handleDiscard}
          className="px-3 py-1.5 rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
        >
          Discard All
        </button>
      </div>
    </div>
  );
}

function InsightStatusCard({ status, agentId, onRetry }) {
  const [submitting, setSubmitting] = useState(false);

  const handleRetry = async () => {
    setSubmitting(true);
    try {
      await regenerateAgentInsights(agentId);
      onRetry?.();
    } catch (err) {
      console.error("Failed to regenerate insights:", err);
    } finally {
      setSubmitting(false);
    }
  };

  if (status === "generating") {
    return (
      <div className="mt-4 rounded-xl bg-blue-500/10 border border-blue-500/20 p-3 flex items-center gap-2">
        <svg className="w-4 h-4 text-blue-400/60 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <span className="text-sm text-blue-300/70">Generating insights...</span>
      </div>
    );
  }

  return (
    <div className="mt-4 rounded-xl bg-red-500/10 border border-red-500/20 p-3 flex items-center justify-between">
      <div className="flex items-center gap-2">
        <svg className="w-4 h-4 text-red-400/60" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z" />
        </svg>
        <span className="text-sm text-red-300/70">Insight generation failed</span>
      </div>
      <button
        type="button"
        onClick={handleRetry}
        disabled={submitting}
        className="text-xs px-2.5 py-1 rounded-lg bg-red-500/20 text-red-300/80 hover:bg-red-500/30 transition-colors disabled:opacity-50"
      >
        {submitting ? "Retrying..." : "Retry"}
      </button>
    </div>
  );
}

function InsightsHistoryCard({ agentId }) {
  const [items, setItems] = useState([]);
  const [expanded, setExpanded] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetchProcessedSuggestions(agentId)
      .then((data) => { setItems(data); setLoaded(true); })
      .catch(() => setLoaded(true));
  }, [agentId]);

  if (!loaded || items.length === 0) return null;

  const accepted = items.filter((s) => s.status === "accepted");
  const rejected = items.filter((s) => s.status === "rejected");
  const allDiscarded = accepted.length === 0;

  return (
    <div className="mt-4 rounded-xl bg-amber-500/10 border border-amber-500/20 p-3">
      <button
        type="button"
        className="flex items-center gap-2 w-full text-left"
        onClick={() => setExpanded((v) => !v)}
      >
        <svg className="w-4 h-4 text-amber-400/60" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
        <span className="text-sm text-amber-300/70 font-medium">
          {allDiscarded
            ? `Insights discarded (${rejected.length})`
            : `${accepted.length} insight${accepted.length !== 1 ? "s" : ""} applied`}
          {!allDiscarded && rejected.length > 0 && (
            <span className="text-dim font-normal ml-1">
              ({rejected.length} discarded)
            </span>
          )}
        </span>
        <svg
          className={`w-3.5 h-3.5 text-dim ml-auto transition-transform ${expanded ? "rotate-180" : ""}`}
          fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {expanded && (
        <div className="mt-2 space-y-1 pl-6">
          {accepted.map((s) => (
            <p key={s.id} className="text-sm text-body/70 leading-relaxed flex items-start gap-1.5">
              <svg className="w-3.5 h-3.5 text-green-400/60 mt-0.5 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
              {s.edited_content || s.content}
            </p>
          ))}
          {rejected.map((s) => (
            <p key={s.id} className="text-sm text-dim/50 leading-relaxed line-through flex items-start gap-1.5">
              <svg className="w-3.5 h-3.5 text-red-400/40 mt-0.5 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
              {s.content}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function PlanBubble({ item, agentId, onAnswered }) {
  const [chosenIdx, setChosenIdx] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [planExpanded, setPlanExpanded] = useState(true);
  const [planError, setPlanError] = useState(null);

  const isDismissed = _isPlanDismissed(item);
  // Determine the effective selected index: stored index > answer parse > local choice
  // When dismissed, do NOT fall back to index detection — no option should highlight
  const serverIdx = isDismissed ? null : (item.selected_index ?? (item.answer != null ? _detectPlanIdx(item.answer) : null));
  const effectiveIdx = isDismissed ? null : (serverIdx ?? chosenIdx);
  const isAnswered = effectiveIdx != null || isDismissed || item.auto_approved;

  // Plan content comes directly from metadata (extracted from ExitPlanMode tool_input)
  const planContent = item.plan || null;

  const handleSelect = async (idx) => {
    setChosenIdx(idx);
    setSubmitting(true);
    setPlanError(null);
    try {
      await answerAgent(agentId, {
        tool_use_id: item.tool_use_id,
        type: "exit_plan_mode",
        selected_index: idx,
      });
      onAnswered?.();
    } catch (e) {
      console.error("Failed to answer plan:", e);
      setPlanError("Failed to send plan response: " + (e.message || "Unknown error"));
      setChosenIdx(null); // revert on failure
    } finally {
      setSubmitting(false);
    }
  };

  const colorMap = {
    emerald: { active: "bg-emerald-500/20 border-emerald-500/40 text-heading", dot: "border-emerald-400 bg-emerald-400" },
    amber: { active: "bg-amber-500/20 border-amber-500/40 text-heading", dot: "border-amber-400 bg-amber-400" },
    indigo: { active: "bg-indigo-500/20 border-indigo-500/40 text-heading", dot: "border-indigo-400 bg-indigo-400" },
  };

  // Badge
  let badgeText = null;
  let badgeClass = "";
  if (isDismissed) {
    badgeText = "Dismissed";
    badgeClass = "bg-red-500/20 text-red-300";
  } else if (item.auto_approved) {
    badgeText = "Auto-approved";
    badgeClass = "bg-amber-500/20 text-amber-300";
  } else if (effectiveIdx != null) {
    badgeText = "Choice Sent";
    badgeClass = effectiveIdx <= 1
      ? "bg-emerald-500/20 text-emerald-300"
      : effectiveIdx === 3
        ? "bg-indigo-500/20 text-indigo-300"
        : "bg-amber-500/20 text-amber-300";
  }

  return (
    <div className="mt-3 rounded-xl bg-amber-500/10 border border-amber-500/20 p-3">
      <div className="flex items-center gap-2 mb-2">
        <svg className="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        <span className="text-sm font-medium text-amber-300">Plan Approval</span>
        {planContent && (
          <button
            type="button"
            onClick={() => setPlanExpanded((v) => !v)}
            className="text-[10px] text-dim hover:text-body transition-colors"
          >
            {planExpanded ? "Hide plan" : "Show plan"}
          </button>
        )}
        {badgeText && (
          <span className={`ml-auto px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider ${badgeClass}`}>
            {badgeText}
          </span>
        )}
      </div>
      {/* Inline plan content */}
      {planContent && planExpanded && (
        <div className="mb-3 rounded-lg bg-surface/60 border border-divider/40 overflow-hidden">
          <div className="px-3 py-2 max-h-[300px] overflow-y-auto text-sm">
            <SafeMarkdown fallback={planContent}>
              <div className="prose-sm text-body [&_h1]:text-base [&_h1]:font-semibold [&_h1]:text-heading [&_h1]:mt-3 [&_h1]:mb-1.5 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:text-heading [&_h2]:mt-2.5 [&_h2]:mb-1 [&_h3]:text-xs [&_h3]:font-semibold [&_h3]:text-heading [&_h3]:mt-2 [&_h3]:mb-1 [&_p]:text-xs [&_p]:mb-1.5 [&_ul]:text-xs [&_ul]:ml-4 [&_ul]:mb-1.5 [&_ol]:text-xs [&_ol]:ml-4 [&_ol]:mb-1.5 [&_li]:mb-0.5 [&_code]:text-[11px] [&_code]:bg-elevated [&_code]:px-1 [&_code]:rounded [&_pre]:text-[11px] [&_pre]:bg-elevated [&_pre]:p-2 [&_pre]:rounded [&_pre]:overflow-x-auto [&_pre]:mb-2">
                {renderMarkdown(planContent)}
              </div>
            </SafeMarkdown>
          </div>
        </div>
      )}
      <div className="space-y-1.5">
        {PLAN_OPTIONS.map((opt, oi) => {
          const isChosen = effectiveIdx === oi;
          const dimmed = isAnswered && !isChosen;
          const colors = colorMap[opt.color] || colorMap.indigo;

          return (
            <button
              key={oi}
              type="button"
              disabled={isAnswered || submitting}
              onClick={() => !isAnswered && handleSelect(oi)}
              className={`w-full text-left rounded-lg px-3 py-2 text-sm transition-all border ${
                isChosen
                  ? colors.active
                  : dimmed
                    ? "bg-surface/30 border-divider/30 text-dim/50"
                    : "bg-surface/50 border-divider hover:bg-hover hover:border-heading/20 text-body"
              } ${isAnswered ? "cursor-default" : "cursor-pointer"}`}
            >
              <div className="flex items-start gap-2">
                <span className={`mt-0.5 w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center ${
                  isChosen ? colors.dot : "border-dim/40"
                }`}>
                  {isChosen && (
                    <svg className="w-2.5 h-2.5 text-white" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                    </svg>
                  )}
                </span>
                <div>
                  <span className="font-medium">{opt.label}</span>
                  <p className={`text-xs mt-0.5 ${dimmed ? "text-dim/30" : "text-dim"}`}>{opt.description}</p>
                </div>
              </div>
            </button>
          );
        })}
      </div>
      {submitting && (
        <p className="text-xs text-dim mt-2 flex items-center gap-1.5">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
          Sending response...
        </p>
      )}
      {planError && (
        <p className="text-xs text-red-400 mt-2 px-1">{planError}</p>
      )}
    </div>
  );
}

// --- Interactive items renderer ---

function InteractiveBubbles({ metadata, agentId, onAnswered, messageContent, project }) {
  if (!metadata?.interactive?.length) return null;
  return metadata.interactive.map((item) => {
    if (item.type === "ask_user_question") {
      return <QuestionBubble key={item.tool_use_id} item={item} agentId={agentId} onAnswered={onAnswered} />;
    }
    if (item.type === "exit_plan_mode") {
      return <PlanBubble key={item.tool_use_id} item={item} agentId={agentId} onAnswered={onAnswered} />;
    }
    if (item.type === "permission_prompt") {
      return <PermissionPromptBubble key={item.tool_use_id} item={item} agentId={agentId} onAnswered={onAnswered} />;
    }
    // Unknown interactive type — render a generic informational card
    return (
      <div key={item.tool_use_id} className="mt-3 rounded-xl bg-surface/60 border border-divider/40 p-3">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-medium text-dim">Interactive prompt</span>
          <span className="ml-auto px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider bg-dim/20 text-dim">
            {item.type}
          </span>
        </div>
        {item.answer && <p className="text-xs text-body">{item.answer}</p>}
      </div>
    );
  });
}

// Regex matching tool summary lines in message content: > `ToolName` ...
const TOOL_SUMMARY_RE = /^> `(\w+)`\s*(.*)/;

/**
 * Split agent message content into interleaved text + tool segments.
 * Uses `> ToolName` summary lines in the content as delimiters.
 * Returns [{type:"text",text}, {type:"tools",entries}, ...].
 */
function splitMessageSegments(content) {
  const lines = content.split("\n");
  const result = [];
  let text = [], tools = [];
  for (const line of lines) {
    const m = line.match(TOOL_SUMMARY_RE);
    if (m) {
      if (text.length) { const t = text.join("\n").trim(); if (t) result.push({ type: "text", text: t }); text = []; }
      tools.push({ name: m[1], summary: m[2] });
    } else {
      if (tools.length) { result.push({ type: "tools", entries: [...tools] }); tools = []; }
      text.push(line);
    }
  }
  if (tools.length) result.push({ type: "tools", entries: tools });
  const t = text.join("\n").trim();
  if (t) result.push({ type: "text", text: t });
  return result;
}

/** Lightweight agent text bubble for non-final segments (no timestamp / actions). */
function AgentTextSegment({ text, project }) {
  return (
    <div className="flex justify-start my-2">
      <div className="max-w-[min(85%,30rem)] min-w-0">
        <div className="rounded-2xl px-4 py-2.5 bg-surface shadow-card text-body rounded-bl-md overflow-hidden">
          <div className="text-sm break-words chat-bubble-content">
            <SafeMarkdown fallback={text}>
              {renderMarkdown(text, project)}
            </SafeMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}

function ChatBubble({ message, project, onCancelMessage, onUpdateMessage, onSendNow, agentId, onRefresh, queuePosition, queueTotal, contentOverride, toolEntries }) {
  if (message.kind === "tool_activity") {
    return null;
  }
  if (message.role === "SYSTEM") {
    if (message.metadata?.stop_action || message.content.startsWith("Task dropped") || message.content.startsWith("Redo")) {
      return <StopNoteCard message={message} />;
    }
    return <SystemBubble message={message} />;
  }

  // Sub-agent task notifications get their own collapsible bubble
  if (message.metadata?.task_notification || (message.content || "").trimStart().startsWith("<task-notification>")) {
    return <SubAgentBubble message={message} project={project} />;
  }

  const isUser = message.role === "USER";
  const isScheduled = isUser && message.scheduled_at && message.status === "PENDING";
  const isPending = isUser && (message.status === "PENDING" || message.status === "QUEUED") && !message.scheduled_at;
  const isQueued = isUser && message.status === "QUEUED";
  const isSlashCommand = isUser && (message.content || "").trimStart().startsWith("/");
  const isWebUndelivered = isUser && message.source === "web" && !message.delivered_at && !isPending && !isScheduled && !isQueued && !isSlashCommand;
  const UNDELIVERED_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes
  const isUndeliveredTimedOut = isWebUndelivered && (serverNow() - new Date(message.created_at).getTime()) > UNDELIVERED_TIMEOUT_MS;

  const [showActions, setShowActions] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(message.content);
  const [editSchedule, setEditSchedule] = useState("");
  const [copied, setCopied] = useState(false);
  const [inlineLightbox, setInlineLightbox] = useState(null); // { media, initialIndex }
  const longPressTimer = useRef(null);
  const lastTapRef = useRef(0);
  const touchStartYRef = useRef(0);
  const editTextareaRef = useRef(null);
  const markdownRef = useRef(null);

  // Handle click on inline markdown images to open lightbox
  const handleMarkdownClick = useCallback((e) => {
    const img = e.target.closest("img");
    if (!img) return;
    const container = markdownRef.current;
    if (!container) return;
    const allImgs = Array.from(container.querySelectorAll("img"));
    if (allImgs.length === 0) return;
    const index = allImgs.indexOf(img);
    const media = allImgs.map((el) => ({ type: "image", src: el.src, filename: el.alt || "" }));
    setInlineLightbox({ media, initialIndex: Math.max(0, index) });
  }, []);

  // Initialize editSchedule from message when entering edit mode
  useEffect(() => {
    if (editing && message.scheduled_at) {
      const d = new Date(message.scheduled_at);
      const local = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
      setEditSchedule(local);
    }
  }, [editing, message.scheduled_at]);

  // Auto-focus textarea when editing starts (useEffect runs after DOM commit)
  useEffect(() => {
    if (editing) {
      editTextareaRef.current?.focus();
    }
  }, [editing]);

  const canModify = isScheduled || isPending;

  const handleLongPressStart = (e) => {
    touchStartYRef.current = e.touches?.[0]?.clientY ?? 0;
    if (!canModify) return;
    longPressTimer.current = setTimeout(() => {
      setShowActions(true);
    }, LONG_PRESS_DELAY);
  };
  const handleLongPressEnd = (e) => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
    // Double-tap detection for touch (copy content)
    // Skip if the finger moved significantly (scroll gesture, not a tap)
    const endY = e.changedTouches?.[0]?.clientY ?? 0;
    const movedTooFar = Math.abs(endY - touchStartYRef.current) > 10;
    if (!canModify && !movedTooFar) {
      const now = Date.now();
      if (now - lastTapRef.current < DOUBLE_TAP_WINDOW) {
        handleDoubleClick();
      }
      lastTapRef.current = now;
    }
  };
  const handleDoubleClick = () => {
    if (canModify) {
      setShowActions(true);
      return;
    }
    navigator.clipboard.writeText(message.content || "").then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), COPY_TOAST_DURATION);
    }).catch(() => {});
  };

  const handleCancel = () => {
    setShowActions(false);
    onCancelMessage?.(message.id);
  };
  const handleSendNow = () => {
    setShowActions(false);
    onSendNow?.(message.id);
  };
  const handleEdit = () => {
    setShowActions(false);
    setEditContent(message.content);
    setEditing(true);
  };
  const handleEditSave = () => {
    const data = {};
    const trimmed = editContent.trim();
    if (trimmed && trimmed !== message.content) {
      data.content = trimmed;
    }
    if (editSchedule) {
      const d = new Date(editSchedule);
      if (!isNaN(d.getTime())) {
        data.scheduled_at = d.toISOString();
      }
    } else if (isScheduled) {
      // User cleared the schedule — remove it
      data.scheduled_at = "";
    }
    if (Object.keys(data).length > 0) {
      onUpdateMessage?.(message.id, data);
    }
    setEditing(false);
  };
  const handleEditCancel = () => {
    setEditing(false);
    setEditContent(message.content);
  };

  const attachments = useMemo(() => {
    const base = extractFileAttachments(message.content, project, message.role, message.metadata);
    if (!toolEntries?.length) return base;
    // Extract media paths from preceding tool entry summaries (Read/Edit/Write paths)
    const toolText = toolEntries.map(e => e.summary || "").join("\n");
    if (!toolText) return base;
    const toolFiles = extractFileAttachments(toolText, project, "AGENT", null);
    if (!toolFiles.length) return base;
    const seen = new Set(base.map(a => a.path));
    return [...base, ...toolFiles.filter(f => !seen.has(f.path))];
  }, [message.content, project, message.role, message.metadata, toolEntries]);

  // Display content: use contentOverride for interleaved mode, otherwise
  // use message.content directly (backend handles attachment stripping).
  // Fallback: strip tags for old display files without metadata.attachments.
  const displayContent = useMemo(() => {
    if (contentOverride != null) return contentOverride;
    if (isUser && !message.metadata?.attachments) return stripAttachmentTags(message.content);
    return message.content;
  }, [contentOverride, isUser, message.content, message.metadata]);

  const scheduledTime = isScheduled
    ? new Date(message.scheduled_at).toLocaleTimeString([], TIME_SHORT)
    : null;

  // Editing UI for scheduled/pending messages
  const [showEditPicker, setShowEditPicker] = useState(false);

  if (editing && isScheduled) {
    const scheduleLabel = editSchedule
      ? new Date(editSchedule).toLocaleString([], DATE_SHORT)
      : "Set time";

    return (
      <div className="flex justify-end my-2">
        <div className="max-w-[min(85%,30rem)] min-w-0">
          <div className="rounded-2xl px-4 py-2.5 bg-amber-600/60 text-white rounded-br-md space-y-2 overflow-hidden">
            <textarea
              ref={editTextareaRef}
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              rows={2}
              className="w-full rounded-lg bg-black/20 border border-amber-400/40 px-2 py-1.5 text-sm text-white placeholder-amber-200/50 resize-none focus:border-amber-300 focus:outline-none"
            />
            <div className="relative">
              <button
                type="button"
                onClick={() => setShowEditPicker(v => !v)}
                className="w-full rounded-lg bg-amber-500 hover:bg-amber-400 text-white text-sm py-1.5 font-medium transition-colors flex items-center justify-center gap-2"
              >
                <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                {scheduleLabel}
              </button>
              {showEditPicker && (
                <SendLaterPicker
                  title="Schedule At"
                  onSelect={(iso) => { setEditSchedule(new Date(iso).toISOString().slice(0, 16)); setShowEditPicker(false); }}
                  onClose={() => setShowEditPicker(false)}
                  onClear={editSchedule ? () => { setEditSchedule(""); setShowEditPicker(false); } : undefined}
                />
              )}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleEditSave}
                className="flex-1 rounded-lg bg-amber-500 hover:bg-amber-400 text-white text-xs py-1.5 font-medium transition-colors"
              >
                Save
              </button>
              <button
                type="button"
                onClick={handleEditCancel}
                className="flex-1 rounded-lg bg-white/10 hover:bg-white/20 text-white text-xs py-1.5 font-medium transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const userInsights = isUser ? message.metadata?.insights : null;

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} my-2`}>
      <div className={`max-w-[min(85%,30rem)] min-w-0 relative overflow-hidden ${isUser ? "flex flex-col items-end" : ""}`}>
        <div className={isUser ? "flex items-center gap-2 max-w-full" : undefined}>
        {isUndeliveredTimedOut && (
          <div className="flex-shrink-0 text-red-400" title="Message not delivered — Claude may not have received this">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M12 2L2 20h20L12 2z" />
            </svg>
          </div>
        )}
        <div
          className={`rounded-2xl px-4 py-2.5 ${
            isUser
              ? isScheduled
                ? "bg-amber-600/80 text-white rounded-br-md"
                : isPending
                  ? "bg-cyan-600/60 text-white/80 rounded-br-md"
                  : isUndeliveredTimedOut
                    ? "bg-red-600/40 text-white/70 rounded-br-md"
                    : isWebUndelivered
                      ? "bg-cyan-600/70 text-white/80 rounded-br-md"
                      : "bg-cyan-600 text-white rounded-br-md"
              : "bg-surface shadow-card text-body rounded-bl-md"
          } ${canModify ? "select-none" : ""} overflow-hidden`}
          onDoubleClick={handleDoubleClick}
          onTouchStart={handleLongPressStart}
          onTouchEnd={handleLongPressEnd}
          onTouchCancel={handleLongPressEnd}
        >
          {isUser ? (
            editing ? (
              <div className="space-y-2">
                <textarea
                  ref={editTextareaRef}
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  rows={2}
                  className="w-full rounded-lg bg-black/10 border border-cyan-300/30 px-2 py-1.5 text-sm text-white placeholder-cyan-200/50 resize-none focus:border-cyan-300 focus:outline-none"
                />
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={handleEditSave}
                    className="flex-1 rounded-lg bg-cyan-500/40 hover:bg-cyan-500/60 text-white text-xs py-1.5 font-medium transition-colors"
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    onClick={handleEditCancel}
                    className="flex-1 rounded-lg bg-white/10 hover:bg-white/20 text-white text-xs py-1.5 font-medium transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              displayContent && (
                <div className="text-sm user-md break-words chat-bubble-content">
                  <SafeMarkdown fallback={displayContent}>
                    {renderMarkdown(displayContent, project)}
                  </SafeMarkdown>
                </div>
              )
            )
          ) : (
            <div className="text-sm break-words chat-bubble-content" ref={markdownRef} onClick={handleMarkdownClick}>
              <SafeMarkdown fallback={displayContent}>
                {renderMarkdown(displayContent, project)}
              </SafeMarkdown>
            </div>
          )}
          <div className={`text-xs mt-1 flex items-center gap-1.5 ${
            isUser
              ? isScheduled ? "text-amber-200" : "text-cyan-200"
              : "text-dim"
          }`}>
            {isScheduled ? (
              <span className="inline-flex items-center gap-1">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="10" />
                  <path strokeLinecap="round" d="M12 6v6l4 2" />
                </svg>
                {scheduledTime}
              </span>
            ) : (
              relativeTime(message.completed_at || message.created_at)
            )}
            {isPending && (
              <span className="text-cyan-300/70">
                {queueTotal > 1 ? `queued (${queuePosition} of ${queueTotal})` : "queued"}
              </span>
            )}
            {message.source && (
              <span className={`px-1 py-0.5 rounded text-[10px] font-medium leading-none ${
                message.source === "web"
                  ? "bg-cyan-500/20 text-cyan-300"
                  : "bg-emerald-500/20 text-emerald-300"
              }`}>
                {message.source}
              </span>
            )}
            {message.status === "FAILED" && (
              <span className="text-red-400" title={message.error_message || ""}>Failed</span>
            )}
            {message.status === "TIMEOUT" && (
              <span className="text-orange-400">Timed out</span>
            )}
            {isUser && message.source === "web" && (isSlashCommand ? (
              message.delivered_at && (
                message.completed_at ? (
                  <span className="ml-auto text-green-400" title={`Executed ${new Date(message.completed_at).toLocaleTimeString()}`}>
                    <svg className="w-4 h-3.5" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 28 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2 13l4 4L16 7" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M10 13l4 4L24 7" />
                    </svg>
                  </span>
                ) : (
                  <span className="ml-auto text-cyan-300/60" title="Received — executing...">
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  </span>
                )
              )
            ) : (
              <span className={`ml-auto ${message.delivered_at ? "text-green-400" : isUndeliveredTimedOut ? "text-red-400" : "text-cyan-300/40"}`}
                    title={message.delivered_at ? `Delivered ${new Date(message.delivered_at).toLocaleTimeString()}` : isUndeliveredTimedOut ? "Undelivered" : "Sending..."}>
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
                  {message.delivered_at ? (
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  ) : isUndeliveredTimedOut ? (
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  ) : (
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" opacity="0.5" />
                  )}
                </svg>
              </span>
            ))}
          </div>
        </div>
        </div>
        {message.status === "FAILED" && message.error_message && (
          <p className="text-xs text-red-400/70 mt-1 px-1">{message.error_message}</p>
        )}
        {copied && (
          <div className="fixed inset-0 flex items-center justify-center pointer-events-none z-[9999]">
            <div className="bg-black/80 text-white text-sm font-medium px-4 py-2 rounded-xl shadow-lg">
              Copied
            </div>
          </div>
        )}
        {userInsights && userInsights.length > 0 && (
          <InsightsBubble insights={userInsights} />
        )}
        {attachments.length > 0 && <FileAttachments attachments={attachments} compact={message.role === "USER"} />}
        {inlineLightbox && (
          <ImageLightbox
            media={inlineLightbox.media}
            initialIndex={inlineLightbox.initialIndex}
            onClose={() => setInlineLightbox(null)}
          />
        )}
        {!isUser && message.metadata?.interactive?.length > 0 && (
          <InteractiveBubbles metadata={message.metadata} agentId={agentId} onAnswered={onRefresh} messageContent={message.content} project={project} />
        )}
        {/* Action popover for scheduled/pending messages */}
        {showActions && (
          <div className="absolute top-0 right-0 -translate-y-full mb-1 z-50">
            <div className="bg-surface border border-divider rounded-xl shadow-lg overflow-hidden flex">
              {isScheduled && (
                <button
                  type="button"
                  onClick={handleSendNow}
                  title="Send now"
                  className="px-3 py-2 text-emerald-400 hover:bg-emerald-600/10 transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
                  </svg>
                </button>
              )}
              <button
                type="button"
                onClick={handleEdit}
                title="Edit"
                className="px-3 py-2 text-heading hover:bg-input transition-colors"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
              </button>
              <button
                type="button"
                onClick={handleCancel}
                title="Cancel"
                className="px-3 py-2 text-red-400 hover:bg-red-600/10 transition-colors"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
              <button
                type="button"
                onClick={() => setShowActions(false)}
                title="Close"
                className="px-2 py-2 text-dim hover:bg-input transition-colors"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// --- Tool log entry rendering helpers ---

const _tlIcon = () => "▸";
const _tlColor = () => "text-cyan-300";

function ToolLogEntry({ entry }) {
  return (
    <div className="flex items-center gap-1.5 text-dim">
      <span className="shrink-0">{_tlIcon(entry)}</span>
      <span className={_tlColor(entry)}>{entry.name}</span>
      {entry.summary && <span className="text-faint truncate max-w-[160px]">{entry.summary}</span>}
    </div>
  );
}

/**
 * Standalone mini-bubble for tool entries parsed from message content.
 * Rendered as a separate element between ChatBubble components in the message list.
 */
function ToolLogBubble({ entries }) {
  const [expanded, setExpanded] = useState(false);
  if (!entries?.length) return null;

  const LIMIT = 5;
  const shouldCollapse = entries.length > LIMIT;

  return (
    <div className="flex justify-start my-2">
      <div className="max-w-[min(85%,30rem)] rounded-2xl px-4 py-2.5 bg-surface shadow-card rounded-bl-md">
        <div className="space-y-0.5 text-xs font-mono">
          {shouldCollapse && !expanded ? (
            <button type="button" onClick={() => setExpanded(true)} className="flex items-center gap-1.5 text-dim hover:text-cyan-300">
              <span className="shrink-0">▸</span>
              <span>{entries.length} tool calls</span>
            </button>
          ) : (
            <>
              {entries.map((e, j) => <ToolLogEntry key={j} entry={e} />)}
              {shouldCollapse && (
                <button type="button" onClick={() => setExpanded(false)} className="text-faint hover:text-dim text-[11px] mt-0.5">
                  collapse
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Collapsed skill.md insertion bubble. Mirrors Claude Code CLI's Skill tool-use
 * byline but lets the user expand to read the injected skill body.
 */
function SkillBubble({ message, project }) {
  const [expanded, setExpanded] = useState(false);
  const meta = message.metadata || {};
  const skillName = meta.skill_name || "";
  const body = meta.skill_body || "";
  const hasBody = body.trim().length > 0;
  return (
    <div className="flex justify-start my-2">
      <div className="max-w-[min(85%,30rem)] rounded-2xl px-4 py-2.5 bg-surface shadow-card rounded-bl-md">
        <button
          type="button"
          onClick={() => hasBody && setExpanded(e => !e)}
          disabled={!hasBody}
          className="flex items-center gap-1.5 text-xs font-mono text-dim hover:text-cyan-300 disabled:hover:text-dim disabled:cursor-default"
        >
          <span className="shrink-0">{expanded ? "▾" : "▸"}</span>
          <span className="text-cyan-300">Skill</span>
          {skillName && <span className="text-faint">{skillName}</span>}
        </button>
        {expanded && hasBody && (
          <div className="mt-2 pt-2 border-t border-divider/40 text-sm break-words chat-bubble-content">
            <SafeMarkdown fallback={body}>
              {renderMarkdown(body, project)}
            </SafeMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}


// --- Tool Activity Log (real-time feed of tool calls via CC hooks) ---

function ActiveToolEntry({ entry, nameColor }) {
  const [elapsed, setElapsed] = useState(() => Math.floor((serverNow() - entry.startTime) / 1000));
  useEffect(() => {
    const timer = setInterval(() => {
      setElapsed(Math.floor((serverNow() - entry.startTime) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [entry.startTime]);

  return (
    <div className="flex items-center gap-1.5 text-dim">
      {entry.kind === "permission" ? (
        <span className="text-amber-400 shrink-0">&#x23F3;</span>
      ) : (
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse shrink-0" />
      )}
      <span className={nameColor(entry)}>{entry.name}</span>
      {entry.summary && <span className="text-faint truncate max-w-[160px]">{entry.summary}</span>}
      {entry.kind === "permission" && <span className="text-amber-400/70">awaiting permission</span>}
      {entry.kind !== "permission" && elapsed > 2 && <span className="text-faint">({elapsed}s)</span>}
    </div>
  );
}

function ToolActivityBubble({ message }) {
  const meta = message.metadata || {};
  const toolName = meta.tool_name || "";
  const toolKind = meta.tool_kind || "tool";
  const phase = meta.phase || "";
  const outputSummary = meta.output_summary || "";
  const isError = meta.is_error || false;
  const isDone = phase === "end" || message.status === "COMPLETED";
  const summary = message.content || "";

  const icon = () => {
    if (!isDone) return <span className="animate-pulse">&#9679;</span>;
    if (isError) return "\u2717";
    if (toolKind === "subagent") return "\u25C6";
    return "\u2713";
  };
  const nameColor = () => {
    if (toolKind === "permission") return "text-amber-400";
    if (toolKind === "subagent") return "text-violet-400";
    if (toolKind === "compact") return "text-orange-400";
    return "text-cyan-300";
  };

  return (
    <div className="flex justify-start my-1">
      <div className="max-w-[min(85%,30rem)] rounded-2xl px-3 py-1.5 bg-surface shadow-card rounded-bl-md">
        <div className={`flex items-center gap-1.5 text-xs font-mono ${isError ? "text-red-400" : "text-dim"}`}>
          <span className="shrink-0">{icon()}</span>
          <span className={nameColor()}>{toolName}</span>
          {summary && <span className="text-faint truncate max-w-[160px]">{summary}</span>}
          {outputSummary && (
            <span className={isError ? "text-red-400/70" : "text-faint"}>&rarr; {outputSummary}</span>
          )}
        </div>
      </div>
    </div>
  );
}




// --- Typing Indicator (shown when executing but no streaming content yet) ---

function TypingIndicator() {
  return (
    <div className="flex justify-start my-2">
      <div className="bg-surface shadow-card rounded-2xl rounded-bl-md px-5 py-3.5">
        <div className="flex items-center gap-[5px]">
          <span className="typing-dot" style={{ animationDelay: "0ms" }} />
          <span className="typing-dot" style={{ animationDelay: "200ms" }} />
          <span className="typing-dot" style={{ animationDelay: "400ms" }} />
        </div>
      </div>
    </div>
  );
}


// --- Initializing Indicator (shown when agent is starting with no messages) ---

function InitializingIndicator() {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <div className="flex items-center gap-[5px]">
        <span className="typing-dot" style={{ animationDelay: "0ms" }} />
        <span className="typing-dot" style={{ animationDelay: "200ms" }} />
        <span className="typing-dot" style={{ animationDelay: "400ms" }} />
      </div>
      <span className="text-sm text-dim">Starting agent...</span>
    </div>
  );
}

// --- Sync Prompt (shown when a CLI session is detected but not yet synced) ---

function SyncPrompt({ agentId, onSync }) {
  const [syncing, setSyncing] = useState(false);
  const handleSync = async () => {
    setSyncing(true);
    try {
      await wakeSync(agentId);
      // Give the sync loop a moment to import, then refresh
      setTimeout(() => onSync(), 800);
    } catch {
      setSyncing(false);
    }
  };
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <span className="text-sm text-dim">CLI session detected</span>
      <button
        onClick={handleSync}
        disabled={syncing}
        className="px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 disabled:opacity-50 transition-colors"
      >
        {syncing ? "Syncing..." : "Sync Now"}
      </button>
    </div>
  );
}

// --- Streaming Bubble (live output while agent is executing) ---

function StreamingBubble({ content, project, activeTool }) {
  return (
    <div className="flex justify-start my-2">
      <div className="max-w-[min(85%,30rem)] min-w-0">
        <div className="rounded-2xl px-4 py-2.5 bg-surface shadow-card text-body rounded-bl-md overflow-hidden">
          <div className="text-sm break-words chat-bubble-content">
            <SafeMarkdown fallback={content}>
              {renderMarkdown(content, project)}
            </SafeMarkdown>
          </div>
          <div className="flex items-center gap-1.5 mt-1">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            {activeTool ? (
              <span className="text-xs text-dim"><code className="text-[11px] px-1 py-0.5 rounded bg-elevated text-cyan-300 font-mono">{activeTool.name}</code> running...</span>
            ) : (
              <span className="text-xs text-dim">Streaming...</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// --- Send Later Time Picker ---

import SendLaterPicker from "../components/SendLaterPicker";

// --- Chat Input ---

function ChatInput({ agentId, onSend, onSendLater, disabled, disabledReason, isBusy, tmuxMode, onEscape, escapeUrgent, escapeAvailable = true, escapeDisabled = false, voiceTarget, scrollButton, kbOpen = false }) {
  const [text, setText] = useDraft(agentId ? `chat:${agentId}` : null, "");
  const [showPicker, setShowPicker] = useState(false);
  const [showSkillPicker, setShowSkillPicker] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState(null);
  const [escCooldown, setEscCooldown] = useState(false);

  const [attPreviewIndex, setAttPreviewIndex] = useState(null);
  const attachmentCacheKey = agentId ? `draft:chat:${agentId}:attachments` : null;
  const [attachments, setAttachments] = useState(() => {
    if (!attachmentCacheKey) return [];
    try {
      const cached = localStorage.getItem(attachmentCacheKey);
      if (cached) {
        return JSON.parse(cached).map((a) => ({
          ...a,
          uploading: false,
          file: null,
          previewUrl: a.thumbnailUrl || null,
        }));
      }
    } catch { /* ignore */ }
    return [];
  });
  const [uploadError, setUploadError] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const dragCountRef = useRef(0);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const pendingSendRef = useRef(null);

  // Voice target ref — redirects transcription to feedback textarea when stop modal is open
  const voiceTargetRef = useRef(setText);
  useEffect(() => {
    voiceTargetRef.current = voiceTarget || setText;
  }, [voiceTarget]);
  const voice = useVoiceRecorder({
    onTranscript: (t) => voiceTargetRef.current((prev) => (prev ? prev + " " + t : t)),
    onError: (msg) => setVoiceError(msg),
  });
  const [voiceError, setVoiceError] = useState(null);
  useEffect(() => {
    if (voiceError) {
      const t = setTimeout(() => setVoiceError(null), ERROR_TOAST_DURATION);
      return () => clearTimeout(t);
    }
  }, [voiceError]);

  useEffect(() => {
    if (uploadError) {
      const t = setTimeout(() => setUploadError(null), ERROR_TOAST_DURATION);
      return () => clearTimeout(t);
    }
  }, [uploadError]);

  // Auto-grow textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  }, [text]);

  // Cleanup blob URLs on unmount (only revoke actual blob: URLs, not server URLs)
  useEffect(() => {
    return () => {
      attachments.forEach((a) => { if (a.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl); });
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync completed attachments to localStorage cache
  useEffect(() => {
    if (!attachmentCacheKey) return;
    const completed = attachments.filter((a) => !a.uploading && a.uploadedPath);
    if (completed.length > 0) {
      const toCache = completed.map((a) => ({
        id: a.id,
        uploadedPath: a.uploadedPath,
        originalName: a.originalName,
        size: a.size,
        mimeType: a.mimeType || a.file?.type || null,
        thumbnailUrl: a.thumbnailUrl || (
          (a.mimeType || a.file?.type || "").startsWith("image/")
            ? uploadUrl(a.uploadedPath.split("/").pop())
            : null
        ),
      }));
      try { localStorage.setItem(attachmentCacheKey, JSON.stringify(toCache)); } catch { /* ignore */ }
    } else {
      try { localStorage.removeItem(attachmentCacheKey); } catch { /* ignore */ }
    }
  }, [attachments, attachmentCacheKey]);

  const buildMessageText = useCallback((baseText, atts, skill) => {
    let msg = baseText;
    for (const a of atts) {
      if (a.uploadedPath) msg += `\n[Attached file: ${a.uploadedPath}]`;
    }
    if (skill) msg = `/${skill}\n${msg}`;
    return msg;
  }, []);

  const clearAttachments = useCallback(() => {
    setAttachments((prev) => {
      prev.forEach((a) => { if (a.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl); });
      return [];
    });
    if (attachmentCacheKey) {
      try { localStorage.removeItem(attachmentCacheKey); } catch { /* ignore */ }
    }
  }, [attachmentCacheKey]);

  const handleSend = useCallback(() => {
    const uploading = attachments.some((a) => a.uploading);
    if (uploading) {
      pendingSendRef.current = "send";
      return;
    }
    const uploaded = attachments.filter((a) => a.uploadedPath);
    if (!text.trim() && uploaded.length === 0) {
      pendingSendRef.current = null;
      return;
    }
    if (disabled && !isBusy) return;
    const msg = buildMessageText(text.trim(), uploaded, selectedSkill);
    onSend(msg);
    if (selectedSkill) { bumpSkillUsage(selectedSkill); setSelectedSkill(null); }
    setText("");
    clearAttachments();
    pendingSendRef.current = null;
  }, [text, attachments, disabled, isBusy, onSend, setText, buildMessageText, clearAttachments, selectedSkill]);

  const handleSchedule = useCallback((scheduledAt) => {
    const uploading = attachments.some((a) => a.uploading);
    if (uploading) {
      pendingSendRef.current = { type: "schedule", scheduledAt };
      return;
    }
    const uploaded = attachments.filter((a) => a.uploadedPath);
    if (!text.trim() && uploaded.length === 0) {
      pendingSendRef.current = null;
      return;
    }
    const msg = buildMessageText(text.trim(), uploaded, selectedSkill);
    onSendLater(msg, scheduledAt);
    if (selectedSkill) { bumpSkillUsage(selectedSkill); setSelectedSkill(null); }
    setText("");
    clearAttachments();
    setShowPicker(false);
    pendingSendRef.current = null;
  }, [text, attachments, onSendLater, setText, buildMessageText, clearAttachments, selectedSkill]);

  // Check pending send when attachments finish uploading
  useEffect(() => {
    if (!pendingSendRef.current) return;
    if (attachments.some((a) => a.uploading)) return;
    const pending = pendingSendRef.current;
    if (pending === "send") {
      handleSend();
    } else if (pending?.type === "schedule") {
      handleSchedule(pending.scheduledAt);
    }
  }, [attachments, handleSend, handleSchedule]);

  const addFiles = useCallback((files) => {
    for (const file of files) {
      if (file.size > 50 * 1024 * 1024) {
        setUploadError(`${file.name} exceeds 50 MB limit`);
        continue;
      }
      const id = Math.random().toString(36).slice(2, 10);
      const isImage = file.type.startsWith("image/");
      const previewUrl = isImage ? URL.createObjectURL(file) : null;

      setAttachments((prev) => [...prev, {
        id, file, previewUrl, uploading: true, uploadedPath: null,
        originalName: file.name, size: file.size, mimeType: file.type,
      }]);

      uploadFile(file).then((result) => {
        setAttachments((prev) => prev.map((a) =>
          a.id === id ? { ...a, uploading: false, uploadedPath: result.path } : a
        ));
      }).catch((err) => {
        setAttachments((prev) => prev.filter((a) => a.id !== id));
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        setUploadError(`Upload failed: ${err.message}`);
      });
    }
  }, []);

  const handleFileSelect = useCallback((e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (files.length > 0) addFiles(files);
  }, [addFiles]);

  const handleDragEnter = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCountRef.current++;
    if (e.dataTransfer?.types?.includes("Files")) setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCountRef.current--;
    if (dragCountRef.current <= 0) { dragCountRef.current = 0; setDragOver(false); }
  }, []);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCountRef.current = 0;
    setDragOver(false);
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length > 0) addFiles(files);
  }, [addFiles]);

  const handlePaste = useCallback((e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      addFiles(files);
    }
  }, [addFiles]);

  const removeAttachment = useCallback((id) => {
    setAttachments((prev) => {
      const att = prev.find((a) => a.id === id);
      if (att?.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(att.previewUrl);
      return prev.filter((a) => a.id !== id);
    });
  }, []);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleEscape = async () => {
    if (!onEscape || escCooldown) return;
    setEscCooldown(true);
    try {
      await onEscape();
    } finally {
      // Cooldown on the frontend to match backend rate limit
      setTimeout(() => setEscCooldown(false), ESCAPE_COOLDOWN);
    }
  };

  const canType = !disabled || isBusy;
  const anyUploading = attachments.some((a) => a.uploading);
  const hasContent = text.trim() || attachments.some((a) => a.uploadedPath);
  const sendDisabled = (disabled && !isBusy) || !hasContent || anyUploading;

  // No-op: keyboard dismiss handled by App-level focusout micro-scroll
  const handleBlur = useCallback(() => {}, []);

  return (
    <div
      className={`absolute left-0 right-0 flex flex-col items-center px-4 z-20 pointer-events-none ${kbOpen ? "" : "pb-[13px]"}`}
      style={{ bottom: 'var(--kb-h, 0px)' }}
    >
      {scrollButton}
      <div
        className={`glass-bar-nav rounded-[22px] px-3 pt-2 ${kbOpen ? "pb-1" : "pb-2.5"} flex flex-col gap-2 w-full relative pointer-events-auto`}
        style={{ maxWidth: "24rem" }}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        {/* Drop zone overlay */}
        {dragOver && (
          <div className="absolute inset-0 z-30 rounded-[22px] bg-cyan-500/15 border-2 border-dashed border-cyan-500 flex items-center justify-center pointer-events-none">
            <span className="text-sm font-medium text-cyan-400">Drop files here</span>
          </div>
        )}
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onBlur={handleBlur}
          placeholder={tmuxMode ? "Send via tmux..." : isBusy ? "Send (queued until ready)..." : disabled ? disabledReason : "Type a message..."}
          disabled={!canType}
          rows={2}
          className="w-full min-h-[48px] max-h-[180px] rounded-xl bg-transparent px-3 py-2 text-sm text-heading placeholder-hint resize-none focus:outline-none transition-colors disabled:opacity-50"
        />
        {(voice.streamingText || voice.refining) && (
          <div className="px-3 pb-1 text-sm text-cyan-400/80 italic animate-pulse">
            {voice.refining ? "Refining..." : voice.streamingText}
          </div>
        )}
        {/* Attachment preview chips */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-1">
            {attachments.map((att, i) => (
              <div key={att.id} className="flex items-center gap-1 px-2 py-1 rounded-lg bg-elevated text-xs max-w-[140px] cursor-pointer"
                onClick={() => { if (!att.uploading) setAttPreviewIndex(i); }}>
                {att.previewUrl ? (
                  <img src={att.previewUrl} alt="" className="w-8 h-8 rounded object-cover shrink-0" />
                ) : (
                  <svg className="w-4 h-4 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                  </svg>
                )}
                <span className="truncate text-label flex-1 min-w-0">{att.originalName}</span>
                {att.uploading ? (
                  <svg className="w-3.5 h-3.5 text-cyan-400 animate-spin shrink-0" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                ) : (
                  <button type="button" onClick={() => removeAttachment(att.id)} className="text-dim hover:text-heading shrink-0">
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        {(uploadError || voiceError) && (
          <p className="text-xs text-red-400 px-1">{uploadError || voiceError}</p>
        )}
        {selectedSkill && (
          <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-cyan-500/15 text-xs w-fit ml-1">
            <Sparkles className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
            <span className="text-cyan-300 font-mono">/{selectedSkill}</span>
            <button
              type="button"
              onClick={() => setSelectedSkill(null)}
              title="Clear skill"
              className="text-dim hover:text-heading shrink-0"
            >
              <XIcon className="w-3 h-3" />
            </button>
          </div>
        )}
        <input ref={fileInputRef} type="file" accept="image/*,video/*,.pdf,.txt,.csv,.json,.md,.py,.js,.ts,.jsx,.tsx,.html,.css,.yaml,.yml,.xml,.log,.zip,.tar,.gz" multiple className="hidden" onChange={handleFileSelect} />
        <div className="flex items-center gap-1.5 px-1">
          {/* Attach button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            title="Attach files"
            className="shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors bg-elevated hover:bg-hover text-label"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
          </button>
          {/* Skill picker button */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowSkillPicker(v => !v)}
              title={selectedSkill ? `Skill: /${selectedSkill}` : "Select a skill"}
              className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                selectedSkill
                  ? "bg-cyan-500/80 hover:bg-cyan-500 text-white"
                  : "bg-elevated hover:bg-hover text-label"
              }`}
            >
              <Sparkles className="w-5 h-5" />
            </button>
            {showSkillPicker && (
              <SkillPickerPanel
                selected={selectedSkill}
                onSelect={setSelectedSkill}
                onClose={() => setShowSkillPicker(false)}
              />
            )}
          </div>
          <div className="flex-1 min-w-0 flex items-center justify-end gap-1.5">
            {voice.recording && voice.remainingSeconds != null && (
              <span className={`text-xs font-semibold tabular-nums ${voice.remainingSeconds <= 10 ? "text-red-400" : "text-red-500"}`}>
                {voice.remainingSeconds >= 60
                  ? `${Math.floor(voice.remainingSeconds / 60)}:${String(voice.remainingSeconds % 60).padStart(2, "0")}`
                  : voice.remainingSeconds}
              </span>
            )}
            <VoiceRecorder
              recording={voice.recording}
              voiceLoading={voice.voiceLoading}
              refining={voice.refining}
              micError={voice.micError}
              onToggle={voice.toggleRecording}
            />
          </div>
          {/* Escape button — sends Esc to tmux (always visible for tmux agents, disabled when stopped/error) */}
          {onEscape && (
            <button
              type="button"
              onClick={handleEscape}
              disabled={escapeDisabled || !escapeAvailable || !escapeUrgent || escCooldown}
              title={escapeDisabled ? "Agent is stopped" : !escapeAvailable ? "No tmux pane attached" : "Send Escape to agent (dismiss prompt)"}
              className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                escapeDisabled || !escapeAvailable
                  ? "bg-elevated text-dim/30 cursor-not-allowed"
                  : escapeUrgent && !escCooldown
                    ? "bg-red-500/80 hover:bg-red-500 text-white cursor-pointer"
                    : "bg-elevated text-dim cursor-not-allowed"
              }`}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
          {/* Send later (clock) button */}
          <div className="relative">
            <button
              type="button"
              onClick={() => hasContent && setShowPicker(!showPicker)}
              disabled={!hasContent}
              title="Schedule message for later"
              className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                !hasContent
                  ? "bg-elevated text-dim cursor-not-allowed"
                  : "bg-amber-500 hover:bg-amber-400 text-white"
              }`}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6l4 2m6-2a10 10 0 11-20 0 10 10 0 0120 0z" />
              </svg>
            </button>
            {showPicker && (
              <SendLaterPicker
                title="Schedule At"
                onSelect={handleSchedule}
                onClose={() => setShowPicker(false)}
              />
            )}
          </div>
          {/* Send button */}
          <button
            type="button"
            onClick={handleSend}
            disabled={sendDisabled}
            className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
              sendDisabled
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
      {attPreviewIndex != null && attachments.length > 0 && (
        <ImageLightbox
          media={attachments.filter(a => !a.uploading).map(a => ({
            src: a.previewUrl || uploadUrl(a.uploadedPath?.split("/").pop()),
            filename: a.originalName,
            type: "image",
          }))}
          initialIndex={Math.min(attPreviewIndex, attachments.filter(a => !a.uploading).length - 1)}
          onClose={() => setAttPreviewIndex(null)}
        />
      )}
    </div>
  );
}

// --- Main Page ---

export default function AgentChatPage({ theme, onToggleTheme, agentId: propAgentId, embedded, onClose, onNavigateAgent }) {
  const { id: routeId } = useParams();
  const id = propAgentId || routeId;
  const navigate = useNavigate();
  const visible = usePageVisible();
  const [agent, setAgent] = useState(null);
  const [taskData, setTaskData] = useState(null);
  const [messages, setMessages] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const toastCtx = useToast();
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [permMode, setPermMode] = useState("auto"); // "normal" | "auto" | "plan"
  const [cyclingMode, setCyclingMode] = useState(false);
  const permModeInitedRef = useRef(false);
  useEffect(() => {
    if (!id) return;
    permModeInitedRef.current = false;
    const stored = localStorage.getItem(`xy.permMode.${id}`);
    if (stored === "normal" || stored === "auto" || stored === "plan") {
      setPermMode(stored);
      permModeInitedRef.current = true;
    }
  }, [id]);
  // When agent loads, if no localStorage override, sync permMode with skip_permissions
  // from project-page launch toggle: true → "auto", false → "normal".
  useEffect(() => {
    if (permModeInitedRef.current) return;
    if (agent?.skip_permissions === undefined) return;
    setPermMode(agent.skip_permissions ? "auto" : "normal");
    permModeInitedRef.current = true;
  }, [agent?.skip_permissions]);
  const handleSetMode = async (target) => {
    if (cyclingMode || target === permMode) return;
    const order = ["normal", "auto", "plan"];
    const steps = (order.indexOf(target) - order.indexOf(permMode) + 3) % 3;
    if (steps === 0) return;
    setCyclingMode(true);
    try {
      await cyclePermissionMode(id, steps);
      setPermMode(target);
      try { localStorage.setItem(`xy.permMode.${id}`, target); } catch { /* ignore */ }
    } catch (e) {
      toastCtx?.error?.(`Mode switch failed: ${e.message || e}`);
    } finally {
      setCyclingMode(false);
    }
  };
  const [generateSummary, setGenerateSummary] = useState(false);
  const [taskOutcome, setTaskOutcome] = useState("complete"); // "complete" | "incomplete" | "drop"
  const [incompleteReason, setIncompleteReason] = useState("");
  const [feedbackAttachments, setFeedbackAttachments] = useState([]);
  const feedbackFileInputRef = useRef(null);
  const feedbackVoice = useVoiceRecorder({
    onTranscript: (t) => setIncompleteReason((prev) => (prev ? prev + " " + t : t)),
    onError: (msg) => console.warn("Feedback voice error:", msg),
  });
  const [showTaskCard, setShowTaskCard] = useState(false);
  const [selectedAttempt, setSelectedAttempt] = useState(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  // Sync generateSummary with per-project localStorage preference
  const generateSummaryInitialized = useRef(false);
  useEffect(() => {
    if (!agent?.project || generateSummaryInitialized.current) return;
    const stored = localStorage.getItem(`pref:generateSummary:${agent.project}`);
    if (stored !== null) setGenerateSummary(stored === "true");
    generateSummaryInitialized.current = true;
  }, [agent?.project]);
  useEffect(() => {
    if (!agent?.project || !generateSummaryInitialized.current) return;
    localStorage.setItem(`pref:generateSummary:${agent.project}`, String(generateSummary));
  }, [generateSummary, agent?.project]);

  // Feedback attachment helpers
  const addFeedbackFiles = (files) => {
    for (const file of files) {
      if (file.size > 50 * 1024 * 1024) continue;
      const fid = Math.random().toString(36).slice(2, 10);
      const isImage = file.type.startsWith("image/");
      const previewUrl = isImage ? URL.createObjectURL(file) : null;
      setFeedbackAttachments((prev) => [...prev, { id: fid, file, previewUrl, uploading: true, uploadedPath: null, originalName: file.name, size: file.size, mimeType: file.type }]);
      uploadFile(file).then((result) => {
        setFeedbackAttachments((prev) => prev.map((a) => a.id === fid ? { ...a, uploading: false, uploadedPath: result.path } : a));
      }).catch(() => {
        setFeedbackAttachments((prev) => prev.filter((a) => a.id !== fid));
        if (previewUrl) URL.revokeObjectURL(previewUrl);
      });
    }
  };
  const removeFeedbackAttachment = (fid) => {
    setFeedbackAttachments((prev) => {
      const att = prev.find((a) => a.id === fid);
      if (att?.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(att.previewUrl);
      return prev.filter((a) => a.id !== fid);
    });
  };
  const clearFeedbackAttachments = () => {
    setFeedbackAttachments((prev) => { prev.forEach((a) => { if (a.previewUrl?.startsWith("blob:")) URL.revokeObjectURL(a.previewUrl); }); return []; });
  };
  const handleFeedbackPaste = (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) { if (item.kind === "file") { const f = item.getAsFile(); if (f) files.push(f); } }
    if (files.length > 0) { e.preventDefault(); addFeedbackFiles(files); }
  };

  const [resuming, setResuming] = useState(false);
  const [starred, setStarred] = useState(false);
  const [starLoading, setStarLoading] = useState(false);
  const [muted, setMuted] = useState(() => isAgentMuted(id));
  const [streamingContent, setStreamingContent] = useState(null);
  const [activeTool, setActiveTool] = useState(null);
  const [toolStartTime, setToolStartTime] = useState(null);
  // Permission cards are now persisted in DB as interactive messages (no WS-only state needed)
  const streamTimeoutRef = useRef(null);
  const generationIdRef = useRef(null); // tracks current backend generation_id
  // Debug: ring buffer of recent WS events for frontend-state reporter
  const wsEventLog = useRef([]);
  const pushWsEvent = (type, data) => {
    const entry = `${new Date().toISOString().slice(11,23)} ${type} ${JSON.stringify(data).slice(0,200)}`;
    wsEventLog.current.push(entry);
    if (wsEventLog.current.length > 50) wsEventLog.current.shift();
  };
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const nameInputRef = useRef(null);
  const [fileModal, setFileModal] = useState(null); // "CLAUDE.md" | "PROGRESS.md" | null
  const [showBrowser, setShowBrowser] = useState(false);
  const [fileExists, setFileExists] = useState({ "CLAUDE.md": null, "PROGRESS.md": null });
  const [headerExpanded, setHeaderExpanded] = useState(false);
  const [syncRefreshing, setSyncRefreshing] = useState(false);
  const messagesEndRef = useRef(null);
  const health = useHealthStatus();

  const showToast = useCallback((message, type = "success") => {
    if (type === "error") toastCtx.error(message);
    else toastCtx.success(message);
  }, [toastCtx]);

  // Load agent + messages with AbortController support.
  // On initial load, errors propagate to console so failures are visible.
  // On subsequent poll refreshes, errors are silenced (transient network issues).
  const initialLoadDone = useRef(false);
  const abortRef = useRef(null);
  const messagesRef = useRef([]);
  const hasPendingInteractiveRef = useRef(false);
  // Display API cursor tracking
  const nextOffsetRef = useRef(0);
  const hasEarlierRef = useRef(false);
  // Buffer for message_delivered events that arrive before the message
  // exists in state (race condition: WS event beats HTTP POST response).
  const pendingDeliveriesRef = useRef(new Map());

  // Keep messagesRef in sync + apply any buffered delivery events
  useEffect(() => {
    messagesRef.current = messages;
    if (pendingDeliveriesRef.current.size > 0) {
      const pending = pendingDeliveriesRef.current;
      let applied = false;
      const updated = messages.map((m) => {
        if (!m.delivered_at && pending.has(m.id)) {
          applied = true;
          return { ...m, delivered_at: pending.get(m.id) };
        }
        return m;
      });
      if (applied) {
        // Clear only the entries we just applied
        for (const m of updated) {
          if (pending.has(m.id) && m.delivered_at) pending.delete(m.id);
        }
        setMessages(updated);
      }
    }
  }, [messages]);

  // Shared: apply display API response to message state.
  // initial=true replaces all messages (preserving WS-delivered_at);
  // initial=false merges incrementally (handles _replace entries from update_last).
  const applyDisplayData = useCallback((data, { initial = false } = {}) => {
    if (data.next_offset != null) {
      nextOffsetRef.current = data.next_offset;
    }
    if (data.has_earlier != null) {
      hasEarlierRef.current = !!data.has_earlier;
    }

    setMessages((prev) => {
      // Initial: start fresh; incremental: merge into existing
      const byId = new Map(initial ? [] : prev.map((m) => [m.id, m]));

      // For initial loads, preserve delivered_at set by WS events
      const prevById = (initial && prev.length)
        ? new Map(prev.map((m) => [m.id, m]))
        : null;

      for (const msg of data.messages || []) {
        if (prevById) {
          const p = prevById.get(msg.id);
          if (p?.delivered_at && (!msg.delivered_at || p.delivered_at > msg.delivered_at)) {
            byId.set(msg.id, { ...msg, delivered_at: p.delivered_at });
            continue;
          }
        }
        byId.set(msg.id, msg);
      }

      // Sequenced (displayed) messages — Map preserves insertion order
      const displayed = [...byId.values()].filter((m) => m.seq != null);

      // Queued messages not yet in display file
      const displayedIds = new Set(displayed.map((m) => m.id));
      const queued = (data.queued || []).filter((q) => !displayedIds.has(q.id));

      return [...displayed, ...queued];
    });
  }, []);

  // Initial load: fetch agent + latest 50 messages
  const loadData = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      // Reset display cursor for fresh initial load
      nextOffsetRef.current = 0;
      const [agentData] = await Promise.all([
        fetchAgent(id),
      ]);
      if (controller.signal.aborted) return;
      if (!agentData || !agentData.id) return;
      setAgent(agentData);
      // Fetch linked task for retry context (fire-and-forget)
      if (agentData.task_id && !taskData) {
        fetchTaskV2(agentData.task_id).then(t => setTaskData(t)).catch(() => {});
      }

      // Display API is the sole authority for message ordering
      const displayData = await fetchDisplay(id, { tailBytes: 50000 });
      if (controller.signal.aborted) return;
      applyDisplayData(displayData, { initial: true });
      setHasMore(!!displayData.has_earlier);
      // Restore active tool state from display file — check if last tool_activity is still running.
      {
        const allMsgs = [...(displayData.messages || []), ...(displayData.queued || [])];
        const lastToolMsg = [...allMsgs].reverse().find((m) => m.kind === "tool_activity");
        if (lastToolMsg && lastToolMsg.status !== "COMPLETED") {
          const meta = lastToolMsg.metadata || {};
          setActiveTool({ name: meta.tool_name || "", summary: lastToolMsg.content || "" });
          setToolStartTime(new Date(lastToolMsg.created_at).getTime());
        }
      }
      if (!initialLoadDone.current && agentData.muted != null) {
        setMuted(agentData.muted);
        setAgentMuted(id, agentData.muted);
      }
      initialLoadDone.current = true;
    } catch (err) {
      if (controller.signal.aborted) return;
      if (!initialLoadDone.current) {
        console.error("AgentChatPage: initial load failed", err);
        showToast("Failed to load agent: " + (err.message || "Unknown error"), "error");
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [id, showToast]);

  // Load older messages (scroll-up pagination)
  const loadOlderMessages = useCallback(async () => {
    const current = messagesRef.current;
    if (!current.length || loadingMore) return;
    setLoadingMore(true);
    try {
      let older = [];
      let moreFlag = false;
      // Load earlier messages via display API only
      if (hasEarlierRef.current) {
        const data = await fetchDisplay(id, { offset: 0, tailBytes: nextOffsetRef.current });
        older = Array.isArray(data?.messages) ? data.messages : [];
        moreFlag = !!data?.has_earlier;
        hasEarlierRef.current = moreFlag;
      }
      if (older.length) {
        // Capture scroll height before DOM update for scroll preservation
        const el = scrollContainerRef.current;
        if (el) savedScrollHeight.current = el.scrollHeight;
        // Deduplicate: only prepend messages not already in the list
        setMessages((prev) => {
          const seenIds = new Set(prev.map((m) => m.id));
          const unique = older.filter((m) => !seenIds.has(m.id));
          return unique.length ? [...unique, ...prev] : prev;
        });
      }
      setHasMore(moreFlag);
    } catch (err) {
      console.warn("Failed to load older messages:", err);
    } finally {
      setLoadingMore(false);
    }
  }, [id, loadingMore]);

  // Incremental refresh via display API.
  // Fetches only bytes after the last known offset and merges into state.
  const refreshMessages = useCallback(async () => {
    try {
      const agentData = await fetchAgent(id);
      if (!agentData || !agentData.id) return;
      setAgent(agentData);

      const isInitial = nextOffsetRef.current === 0;
      const params = isInitial
        ? { tailBytes: 50000 }
        : { offset: nextOffsetRef.current };

      const data = await fetchDisplay(id, params);
      applyDisplayData(data, { initial: isInitial });
    } catch {
      // Transient errors during polling — silently ignore
    }
  }, [id, applyDisplayData]);

  // Initial load + clear notification flag for this agent
  useEffect(() => {
    clearAgentNotified(id);
    registerViewing(id);
    initialLoadDone.current = false;
    setLoading(true);
    loadData();
    return () => { abortRef.current?.abort(); unregisterViewing(id); };
  }, [loadData, id]);

  // Check if any interactive cards are waiting for an answer
  // (must be before polling useEffect which depends on it)
  const hasPendingInteractive = useMemo(() => {
    // Only block input when agent is actively waiting (IDLE) for a response
    if (agent?.status !== "IDLE") return false;

    // Only check the LAST agent message with interactive metadata
    // (old cards are stale — agent has continued past them)
    let lastInteractiveMsg = null;
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.role === "AGENT" && msg.metadata?.interactive?.length) {
        lastInteractiveMsg = msg;
        break;
      }
    }
    if (!lastInteractiveMsg) return false;

    for (const item of lastInteractiveMsg.metadata.interactive) {
      // Skip auto-approved items
      if (item.auto_approved) continue;
      // Skip dismissed items
      const answer = item.answer;
      if (typeof answer === "string" && (
        answer.startsWith("The user doesn't want to proceed") ||
        answer.startsWith("User declined") ||
        answer.startsWith("Tool use rejected")
      )) continue;

      const questions = item.questions || [];
      if (questions.length > 1) {
        // Multi-question: pending if any question lacks a selection
        const indices = item.selected_indices || {};
        for (let qi = 0; qi < questions.length; qi++) {
          if (indices[String(qi)] == null && answer == null) return true;
        }
      } else {
        // Single-question or ExitPlanMode
        if (answer == null && item.selected_index == null) return true;
      }
    }
    return false;
  }, [messages, agent?.status]);
  hasPendingInteractiveRef.current = hasPendingInteractive;

  // Polling — faster when executing, pauses when page hidden
  useEffect(() => {
    if (!visible) return;
    const isActive = agent?.status === "EXECUTING" || agent?.status === "IDLE" || hasPendingInteractiveRef.current;
    const interval = isActive ? POLL_ACTIVE_INTERVAL : POLL_IDLE_INTERVAL;
    const timer = setInterval(refreshMessages, interval);
    return () => clearInterval(timer);
  }, [refreshMessages, agent?.status, visible, hasPendingInteractive]);

  // Mark as read on mount and when new messages arrive
  useEffect(() => {
    if (agent && agent.unread_count > 0) {
      markAgentRead(id).then(() => {
        window.dispatchEvent(new CustomEvent("agents-data-changed"));
      }).catch((err) => {
        console.warn("Failed to mark agent as read:", err);
      });
    }
  }, [id, messages.length, agent?.unread_count]);

  // Fetch starred status once agent is loaded
  useEffect(() => {
    if (!agent) return;
    const sessionId = agent.session_id || agent.id;
    fetchProjectSessions(agent.project)
      .then((sessions) => {
        const match = sessions.find((s) => s.session_id === sessionId);
        setStarred(match?.starred ?? false);
      })
      .catch((err) => {
        console.warn("Failed to fetch starred status:", err);
      });
  }, [agent?.project, agent?.session_id, agent?.id]);

  // Check CLAUDE.md / PROGRESS.md existence once agent is loaded
  useEffect(() => {
    if (!agent?.project) return;
    Promise.all([
      fetchProjectFile(agent.project, "CLAUDE.md").catch(() => ({ exists: false })),
      fetchProjectFile(agent.project, "PROGRESS.md").catch(() => ({ exists: false })),
    ]).then(([c, p]) => {
      setFileExists({ "CLAUDE.md": c.exists, "PROGRESS.md": p.exists });
    });
  }, [agent?.project]);

  const handleToggleStar = async () => {
    if (!agent || starLoading) return;
    const sessionId = agent.session_id || agent.id;
    setStarLoading(true);
    try {
      if (starred) {
        await unstarSession(agent.project, sessionId);
      } else {
        await starSession(agent.project, sessionId);
      }
      const nextStarred = !starred;
      setStarred(nextStarred);
      window.dispatchEvent(new CustomEvent("agent-star-changed", { detail: { agentId: id, starred: nextStarred } }));
    } catch (err) {
      showToast("Failed to update star: " + err.message, "error");
    } finally {
      setStarLoading(false);
    }
  };

  const handleToggleMute = () => {
    const nextMuted = !muted;
    setAgentMuted(id, nextMuted);
    setMuted(nextMuted);
    updateAgent(id, { muted: nextMuted }).catch((err) => {
      showToast("Failed to save mute setting: " + (err.message || "Unknown error"), "error");
    });
    showToast(nextMuted ? "Notifications muted for this agent" : "Notifications enabled for this agent");
  };

  // Sync mute & star state across split-screen panes
  useEffect(() => {
    const onMute = (e) => { if (e.detail?.agentId === id) setMuted(e.detail.muted); };
    const onStar = (e) => { if (e.detail?.agentId === id) setStarred(e.detail.starred); };
    const onRename = (e) => { if (e.detail?.agentId === id) setAgent((prev) => prev ? { ...prev, name: e.detail.name } : prev); };
    window.addEventListener("agent-mute-changed", onMute);
    window.addEventListener("agent-star-changed", onStar);
    window.addEventListener("agent-renamed", onRename);
    return () => {
      window.removeEventListener("agent-mute-changed", onMute);
      window.removeEventListener("agent-star-changed", onStar);
      window.removeEventListener("agent-renamed", onRename);
    };
  }, [id]);


  // Track keyboard height via visualViewport.  Uses direct DOM (CSS variable
  // --kb-h) for instant, jank-free updates.  Input bar uses transform (GPU-
  // composited, no layout reflow) instead of bottom.  Scroll container padding
  // is debounced to avoid expensive reflow every frame.
  const [kbOpen, setKbOpen] = useState(false);
  const kbContainerRef = useRef(null);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    let rafId = null;
    let stopTimer = null;
    let padTimer = null;
    let prevOff = 0;
    let isOpen = false;

    // Prevent touchmove outside the message scroll container so iOS
    // visual-viewport doesn't scroll when dragging on the input bar.
    const blockTouchOutsideScroll = (e) => {
      const sc = scrollContainerRef.current;
      if (sc && sc.contains(e.target)) return; // allow message list scroll
      // Don't interfere with overlay sheets (file browser, new task, etc.)
      if (e.target.closest('[data-overlay]')) return;
      e.preventDefault();
    };

    // --- KB debug logging: batch samples and flush to backend ---
    const kbSamples = [];
    let kbFlushTimer = null;
    const kbFlush = () => {
      if (!kbSamples.length) return;
      const batch = kbSamples.splice(0);
      fetch("/api/debug/kb-log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ samples: batch }),
      }).catch(() => {});
    };
    const kbLog = (containerH, off, open) => {
      kbSamples.push({
        t: Date.now(),
        cH: containerH,
        iH: window.innerHeight,
        vvH: Math.round(vv.height),
        vvOT: Math.round(vv.offsetTop),
        off,
        open,
      });
      clearTimeout(kbFlushTimer);
      kbFlushTimer = setTimeout(kbFlush, 500);
    };

    const update = () => {
      const el = kbContainerRef.current;
      if (!el) return;

      const containerH = el.clientHeight;
      // rawDelta: detects keyboard presence (ignores viewport scroll)
      const rawDelta = Math.max(0, Math.round(containerH - vv.height));
      // kbOffset: actual positioning offset — subtracts vv.offsetTop so
      // the input bar stays flush with the keyboard even when iOS scrolls
      // the visual viewport (common on 2nd+ keyboard open).
      const kbOffset = Math.max(0, Math.round(containerH - vv.height - vv.offsetTop));

      kbLog(containerH, kbOffset, rawDelta > 100);

      const open = rawDelta > 100;

      if (open) {
        // Only update CSS var when change > 3px to suppress sub-pixel jitter
        if (Math.abs(kbOffset - prevOff) > 3) {
          prevOff = kbOffset;
          el.style.setProperty('--kb-h', `${kbOffset}px`);
          // Keep messages pinned to bottom while keyboard is animating
          // (margin-bottom shrinks the scroll container via --kb-h)
          const sc = scrollContainerRef.current;
          if (sc && !userScrolledUp.current) {
            sc.scrollTop = sc.scrollHeight;
          }
        }
      } else if (prevOff !== 0) {
        prevOff = 0;
        el.style.removeProperty('--kb-h');
      }

      // Scroll container padding: keep small so messages peek behind glass
      // input bar (like iMessage).  When KB is closed, pb-36 from the class
      // provides generous spacing instead.
      if (open !== isOpen) {
        clearTimeout(padTimer);
        padTimer = setTimeout(() => {
          const sc = scrollContainerRef.current;
          if (!sc) return;
          sc.style.paddingBottom = open ? 'calc(var(--kb-h, 0px) + 80px)' : '';
        }, 80);
      }

      if (open && !isOpen) {
        isOpen = true;
        // Lock body to prevent iOS viewport scroll — CSS lock is
        // applied once (no per-frame fight), so no shake.
        document.body.style.position = 'fixed';
        document.body.style.width = '100%';
        document.body.style.top = '0';
        document.body.style.touchAction = 'none';
        window.scrollTo(0, 0);
        // Block touchmove outside scroll container to prevent iOS
        // visual-viewport scroll via touch gestures on the input bar.
        document.addEventListener('touchmove', blockTouchOutsideScroll, { passive: false });
        setKbOpen(true);
        const sc = scrollContainerRef.current;
        if (sc && !userScrolledUp.current) {
          requestAnimationFrame(() => { sc.scrollTop = sc.scrollHeight; });
        }
      } else if (!open && isOpen) {
        isOpen = false;
        // Unlock body
        document.body.style.position = '';
        document.body.style.width = '';
        document.body.style.top = '';
        document.body.style.touchAction = '';
        document.removeEventListener('touchmove', blockTouchOutsideScroll);
        setKbOpen(false);
        const sc = scrollContainerRef.current;
        if (sc && !userScrolledUp.current) {
          requestAnimationFrame(() => { sc.scrollTop = sc.scrollHeight; });
        }
      }
    };

    const poll = () => {
      update();
      rafId = requestAnimationFrame(poll);
    };
    const startPoll = () => {
      if (stopTimer) { clearTimeout(stopTimer); stopTimer = null; }
      if (!rafId) rafId = requestAnimationFrame(poll);
    };
    const stopPoll = () => {
      // Delay stop — keyboard switch may briefly lose focus
      if (stopTimer) clearTimeout(stopTimer);
      stopTimer = setTimeout(() => {
        if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
        update();
      }, 400);
    };
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    document.addEventListener("focusin", startPoll);
    document.addEventListener("focusout", stopPoll);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
      document.removeEventListener("focusin", startPoll);
      document.removeEventListener("focusout", stopPoll);
      if (rafId) cancelAnimationFrame(rafId);
      if (stopTimer) clearTimeout(stopTimer);
      clearTimeout(padTimer);
      document.removeEventListener('touchmove', blockTouchOutsideScroll);
      document.body.style.touchAction = '';
      kbFlush(); // flush remaining samples
    };
  }, []);

  // Auto-scroll to bottom on new messages or streaming content
  const scrollContainerRef = useRef(null);
  const userScrolledUp = useRef(false);
  const scrollSaveTimer = useRef(null);
  const prevLastMsgId = useRef(null);
  const prevFirstMsgId = useRef(null);
  const savedScrollHeight = useRef(null);
  const scrollKey = `scroll:chat:${id}`;
  const scrollCountKey = `scroll:chat:${id}:count`;

  // Detect if user has scrolled up (to avoid forcing scroll during streaming)
  // and persist scroll position (debounced) for restore on navigate-back
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    userScrolledUp.current = distFromBottom > 100;
    const shouldShow = distFromBottom > 300;
    setShowScrollToBottom(shouldShow);
    // Scroll-up trigger for lazy loading
    if (el.scrollTop < 200 && hasMore && !loadingMore) {
      loadOlderMessages();
    }
    clearTimeout(scrollSaveTimer.current);
    scrollSaveTimer.current = setTimeout(() => {
      try { localStorage.setItem(scrollKey, String(el.scrollTop)); } catch { /* ignore */ }
    }, SCROLL_SAVE_DEBOUNCE);
  }, [scrollKey, hasMore, loadingMore, loadOlderMessages]);

  // Save scroll position on unmount
  useEffect(() => {
    return () => {
      clearTimeout(scrollSaveTimer.current);
      const el = scrollContainerRef.current;
      if (el) {
        try { localStorage.setItem(scrollKey, String(el.scrollTop)); } catch { /* ignore */ }
      }
    };
  }, [scrollKey]);

  // useLayoutEffect so scroll adjustments happen before browser paint (no flicker)
  useLayoutEffect(() => {
    if (loading || !messages.length) return;
    const lastId = messages[messages.length - 1]?.id;
    const firstId = messages[0]?.id;
    const isFirstLoad = prevLastMsgId.current === null;
    const newMessagesAppended = !isFirstLoad && prevLastMsgId.current !== lastId;
    const olderMessagesPrepended = !isFirstLoad && prevFirstMsgId.current !== firstId && prevLastMsgId.current === lastId;

    prevLastMsgId.current = lastId;
    prevFirstMsgId.current = firstId;

    if (isFirstLoad) {
      // Restore saved position if message count matches (no new messages since last visit)
      try {
        const savedCount = localStorage.getItem(scrollCountKey);
        const savedPos = localStorage.getItem(scrollKey);
        if (savedPos && savedCount && Number(savedCount) === messages.length) {
          const el = scrollContainerRef.current;
          if (el) {
            el.scrollTop = Number(savedPos);
            userScrolledUp.current = true;
            return;
          }
        }
      } catch { /* ignore */ }
      // No saved position or count mismatch — scroll to bottom
      messagesEndRef.current?.scrollIntoView({ behavior: "instant" });
      return;
    }

    // Older messages prepended — preserve scroll position
    if (olderMessagesPrepended) {
      const el = scrollContainerRef.current;
      if (el && savedScrollHeight.current != null) {
        el.scrollTop += el.scrollHeight - savedScrollHeight.current;
      }
      return;
    }

    // New messages appended — clear saved position, auto-scroll
    if (newMessagesAppended) {
      try {
        localStorage.removeItem(scrollKey);
        localStorage.setItem(scrollCountKey, String(messages.length));
      } catch { /* ignore */ }
    }
    if (!userScrolledUp.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [loading, messages, streamingContent, scrollKey, scrollCountKey]);

  // Keep saved message count in sync for future visits
  useEffect(() => {
    if (!loading && messages.length > 0) {
      try { localStorage.setItem(scrollCountKey, String(messages.length)); } catch { /* ignore */ }
    }
  }, [loading, messages.length, scrollCountKey]);

  // Auto-load older messages when content doesn't overflow the viewport
  // but has_earlier is true (scroll-based trigger can't fire without overflow)
  useEffect(() => {
    if (loading || !messages.length || !hasMore || loadingMore) return;
    const el = scrollContainerRef.current;
    if (!el) return;
    // Content fits without scrollbar — onScroll will never fire
    if (el.scrollHeight <= el.clientHeight + 10) {
      loadOlderMessages();
    }
  }, [loading, messages, hasMore, loadingMore, loadOlderMessages]);

  // WebSocket: re-fetch on new_message events, handle streaming
  const { sendWsMessage } = useWebSocket();

  // Notify backend which agent we're viewing (suppresses notifications)
  useEffect(() => {
    sendWsMessage({ type: "viewing", agent_id: id });
    return () => sendWsMessage({ type: "viewing", agent_id: null, _unview: id });
  }, [id, sendWsMessage]);

  // Use subscriber-based WS handler — every event is delivered, none lost
  // to React 18 batching (unlike the old setLastEvent/useEffect pattern).
  const refreshMessagesRef = useRef(refreshMessages);
  refreshMessagesRef.current = refreshMessages;
  useWsEvent((event) => {
    if (event.data?.agent_id !== id) return;

    if (event.type === "agent_stream") {
      console.log('[ws] agent_stream', event.data.agent_id?.slice(0,8), 'len=', event.data.content?.length);
      pushWsEvent('agent_stream', { len: event.data.content?.length, gid: event.data.generation_id });
      const gid = event.data.generation_id;
      if (gid != null && generationIdRef.current != null && gid < generationIdRef.current) return;
      if (gid != null) generationIdRef.current = gid;
      setStreamingContent(event.data.content);
      clearTimeout(streamTimeoutRef.current);
      streamTimeoutRef.current = setTimeout(() => {
        setStreamingContent(null);
      }, STREAM_TIMEOUT);
      return;
    }

    if (event.type === "agent_stream_end") {
      console.log('[ws] agent_stream_end', event.data);
      pushWsEvent('agent_stream_end', event.data);
      const gid = event.data.generation_id;
      if (gid != null && generationIdRef.current != null && gid < generationIdRef.current) return;
      clearTimeout(streamTimeoutRef.current);
      setStreamingContent(null);
      setActiveTool(null);
      setToolStartTime(null);
      return;
    }

    if (event.type === "tool_activity") {
      console.log('[ws] tool_activity', event.data.tool_name, event.data.phase);
      pushWsEvent('tool_activity', { tool: event.data.tool_name, phase: event.data.phase });
      const { tool_name, phase, summary } = event.data;
      if (phase === "start") {
        setActiveTool({ name: tool_name, summary: summary || "" });
        setToolStartTime(Date.now());
      } else if (phase === "end") {
        setActiveTool(null);
        setToolStartTime(null);
      }
      return;
    }

    // permission_request / permission_resolved: now handled via DB interactive
    // cards — WS events trigger a display file refresh so the card appears.
    if (event.type === "permission_request" || event.type === "permission_resolved") {
      refreshMessagesRef.current({ syncHint: true });
      return;
    }

    if (event.type === "new_message") {
      console.log('[ws] new_message', event.data);
      pushWsEvent('new_message', event.data);
      const hasActiveCompact = activeTool?.name === "Compact";
      if (hasActiveCompact) {
        refreshMessagesRef.current({ syncHint: true });
        return;
      }
      clearTimeout(streamTimeoutRef.current);
      setStreamingContent(null);
      setActiveTool(null);
      setToolStartTime(null);
      refreshMessagesRef.current({ syncHint: event.data?.message_id === "sync" });
      return;
    }

    if (event.type === "metadata_update") {
      const { message_id, metadata } = event.data;
      setMessages((prev) =>
        prev.map((m) => (m.id === message_id ? { ...m, metadata } : m))
      );
      return;
    }

    if (event.type === "message_delivered") {
      console.log('[ws] message_delivered', event.data);
      pushWsEvent('message_delivered', event.data);
      const { message_id, delivered_at } = event.data;
      setMessages((prev) => {
        const found = prev.some((m) => m.id === message_id);
        if (!found) {
          // Message not in state yet (race: WS event beat HTTP POST response).
          // Buffer it so the useEffect on messages can apply it later.
          pendingDeliveriesRef.current.set(message_id, delivered_at);
          return prev;
        }
        return prev.map((m) => (m.id === message_id ? { ...m, delivered_at } : m));
      });
      return;
    }

    if (event.type === "message_update") {
      const { message_id, status, error_message, completed_at } = event.data;
      if (status === "CANCELLED") {
        setMessages((prev) => prev.filter((m) => m.id !== message_id));
      } else {
        setMessages((prev) =>
          prev.map((m) => (m.id === message_id
            ? { ...m, status, ...(error_message ? { error_message } : {}), ...(completed_at ? { completed_at } : {}) }
            : m))
        );
      }
      return;
    }

    if (event.type === "agent_update") {
      console.log('[ws] agent_update', event.data.agent_id?.slice(0,8), event.data.status);
      pushWsEvent('agent_update', { status: event.data.status });
      const status = event.data.status;
      // Update agent status directly from WS — no wait for REST poll
      if (event.data.agent_id === id) {
        const patch = { status };
        if ("insight_status" in event.data) {
          patch.insight_status = event.data.insight_status || null;
        }
        if ("sync_stale" in event.data) {
          patch.sync_stale = event.data.sync_stale;
        }
        setAgent((prev) => prev ? { ...prev, ...patch } : prev);
      }
      if (status !== "EXECUTING" && status !== "IDLE") {
        clearTimeout(streamTimeoutRef.current);
        setStreamingContent(null);
        setActiveTool(null);
        setToolStartTime(null);
        generationIdRef.current = null;
      }
      refreshMessagesRef.current();
    }

    if (event.type === "progress_suggestions_ready") {
      if (event.data.agent_id === id) {
        setAgent((prev) => prev ? { ...prev, has_pending_suggestions: true, insight_status: null } : prev);
      }
      loadData();
      showToast("Insights ready for review");
    }
  }, [id]);

  // Cleanup
  useEffect(() => {
    return () => {
      clearTimeout(streamTimeoutRef.current);
    };
  }, []);

  // Debug: POST rendered message state + DOM elements to backend every 10s
  useEffect(() => {
    if (!id || !messages?.length) return;
    const timer = setInterval(() => {
      // Scan DOM for actual rendered message elements
      const domElements = [];
      const container = document.querySelector('[data-chat-container]');
      if (container) {
        const containerRect = container.getBoundingClientRect();
        container.querySelectorAll('[data-msg-id]').forEach(el => {
          const rect = el.getBoundingClientRect();
          domElements.push({
            msgId: el.dataset.msgId,
            type: el.dataset.msgType || '?',
            y: Math.round(rect.top - containerRect.top),
            h: Math.round(rect.height),
            visible: rect.top < window.innerHeight && rect.bottom > 0,
            text: el.textContent?.slice(0, 80)?.replace(/\n/g, ' '),
          });
        });
      }
      const snapshot = {
        agentId: id,
        page: 'AgentChatPage',
        messages: messages.map(m => ({
          id: m.id, role: m.role, kind: m.kind,
          session_seq: m.session_seq, source: m.source,
          created_at: m.created_at,
          content: (m.content || '').slice(0, 100),
        })),
        domElements,
        wsEvents: wsEventLog.current.slice(),
      };
      fetch('/api/debug/frontend-state', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(snapshot),
      }).catch(() => {});  // fire and forget
    }, 1000);
    return () => clearInterval(timer);
  }, [id, messages]);

  // Auto-select name input when rename starts (useEffect runs after DOM commit)
  useEffect(() => {
    if (editingName) nameInputRef.current?.select();
  }, [editingName]);

  // Rename agent
  const startRename = () => {
    setNameDraft(agent?.name || "");
    setEditingName(true);
  };
  const submitRename = async () => {
    const trimmed = nameDraft.trim();
    if (!trimmed || trimmed === agent?.name) {
      setEditingName(false);
      return;
    }
    try {
      await renameAgent(id, trimmed);
      setAgent((prev) => prev ? { ...prev, name: trimmed } : prev);
      window.dispatchEvent(new CustomEvent("agent-renamed", { detail: { agentId: id, name: trimmed } }));
      showToast("Renamed");
    } catch (err) {
      showToast("Rename failed: " + err.message, "error");
    }
    setEditingName(false);
  };

  // Send message — backend decides tmux-immediate (QUEUED) vs PENDING
  const handleSend = async (content) => {
    try {
      // New turn — clear previous tool activity state
      setActiveTool(null);
      setToolStartTime(null);
      await sendMessage(id, content);
      refreshMessages();
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    }
  };

  // Send later (queued with scheduled_at)
  const handleSendLater = async (content, scheduledAt) => {
    try {
      await sendMessage(id, content, { scheduled_at: scheduledAt });
      const when = new Date(scheduledAt);
      const timeStr = when.toLocaleTimeString([], TIME_SHORT);
      showToast(`Scheduled for ${timeStr}`);
      loadData();
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    }
  };

  // Cancel a scheduled/pending message
  const handleCancelMessage = async (messageId) => {
    try {
      await cancelMessage(id, messageId);
      setMessages((prev) => prev.filter((m) => m.id !== messageId));
      showToast("Message cancelled");
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    }
  };

  // Update a scheduled/pending message
  const handleUpdateMessage = async (messageId, data) => {
    try {
      const updated = await updateMessage(id, messageId, data);
      setMessages((prev) => prev.map((m) => (m.id === messageId ? updated : m)));
      showToast("Message updated");
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    }
  };

  // Send a scheduled message immediately (clear its scheduled_at)
  const handleSendNow = async (messageId) => {
    try {
      const updated = await updateMessage(id, messageId, { scheduled_at: "" });
      setMessages((prev) => prev.map((m) => (m.id === messageId ? updated : m)));
      showToast("Sending now");
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    }
  };

  // Stop agent
  const handleStop = async () => {
    setStopping(true);
    try {
      // Build feedback text with any attached files
      let feedbackText = incompleteReason.trim();
      const uploaded = feedbackAttachments.filter((a) => a.uploadedPath);
      if (uploaded.length > 0) {
        for (const a of uploaded) feedbackText += `\n[Attached file: ${a.uploadedPath}]`;
      }
      await stopAgent(id, {
        generateSummary: generateSummary && !!agent?.task_id,
        taskComplete: agent?.task_id ? taskOutcome === "complete" : true,
        taskDrop: taskOutcome === "drop",
        incompleteReason: (taskOutcome !== "complete" && feedbackText) ? feedbackText : null,
      });
      const label = !agent?.task_id
        ? "Agent stopped"
        : taskOutcome === "complete"
          ? "Agent stopped — task complete"
          : taskOutcome === "drop"
            ? "Agent stopped — task dropped"
            : "Agent stopped — task returned to inbox";
      showToast(generateSummary && agent?.task_id ? label + " (generating insights...)" : label);
      loadData();
      window.dispatchEvent(new CustomEvent("agents-data-changed"));
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setStopping(false);
      setShowStopConfirm(false);
      setTaskOutcome("complete");
      setIncompleteReason("");
      clearFeedbackAttachments();
    }
  };

  // Resume agent — always uses tmux
  const handleResume = async () => {
    setResuming(true);
    try {
      // All agents resume as tmux sessions
      const mode = !agent?.successor_id ? "tmux" : null;
      await resumeAgent(id, mode ? { mode } : null);
      showToast("Agent resumed");
      loadData();
      window.dispatchEvent(new CustomEvent("agents-data-changed"));
    } catch (err) {
      // Handle superseded agent (409)
      try {
        const info = JSON.parse(err.message);
        if (info.reason === "superseded") {
          showToast("This agent was continued by a successor", "error");
          loadData(); // refresh to pick up successor_id
          return;
        }
      } catch {}
      showToast("Failed: " + err.message, "error");
    } finally {
      setResuming(false);
    }
  };

  // Agent status is written directly by backend hooks — no frontend derivation needed.
  // Smooth EXECUTING→off: hold true for 1s to avoid flicker
  // (must be before early returns to maintain hooks ordering)
  const isExecutingRaw = agent?.status === "EXECUTING";
  const [isExecuting, setIsExecuting] = useState(isExecutingRaw);
  const execTimerRef = useRef(null);
  useEffect(() => {
    if (isExecutingRaw) {
      clearTimeout(execTimerRef.current);
      setIsExecuting(true);
    } else {
      execTimerRef.current = setTimeout(() => setIsExecuting(false), 1000);
    }
    return () => clearTimeout(execTimerRef.current);
  }, [isExecutingRaw]);

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
        <button type="button" onClick={() => { if (onClose) onClose(); else navigate("/agents"); }} className="mt-2 text-sm text-cyan-400 underline">
          Back to Agents
        </button>
      </div>
    );
  }

  const isHealthy = isSystemHealthy(health);
  const healthChipCls = health === null
    ? "bg-gray-500/15 text-gray-400"
    : isHealthy
      ? "bg-green-500/15 text-green-500"
      : "bg-red-500/15 text-red-400";
  const healthDotColor = health === null ? "bg-gray-400" : isHealthy ? "bg-green-500" : "bg-red-500";
  const healthLabel = health === null ? "..." : isHealthy ? "OK" : "Error";

  // Status is authoritative from backend — read directly
  const statusDot = AGENT_STATUS_COLORS[agent.status] || "bg-gray-500";
  const statusText = AGENT_STATUS_TEXT_COLORS[agent.status] || "text-dim";
  const isIdle = agent.status === "IDLE";
  const hasTmux = isIdle && !!agent.tmux_pane;
  const hasTmuxPane = !!agent.tmux_pane;
  const isStopped = agent.status === "STOPPED";
  const isError = agent.status === "ERROR";
  const isStarting = agent.status === "STARTING";
  const compactHeader = embedded && !headerExpanded;

  let disabledReason = "";
  if (isStarting) disabledReason = "Agent is starting…";
  else if (isStopped) disabledReason = "Agent is stopped — click Resume to restart";
  else if (isError) disabledReason = "Agent errored — click Resume to restart";
  else if (hasPendingInteractive) {
    // Determine if the pending card is a plan or question
    let pendingType = "question";
    for (let i = messages.length - 1; i >= 0; i--) {
      const meta = messages[i].metadata;
      if (messages[i].role === "AGENT" && meta?.interactive?.length) {
        for (const item of meta.interactive) {
          if (item.auto_approved) continue;
          if (item.answer == null && item.selected_index == null) {
            pendingType = item.type === "exit_plan_mode" ? "plan" : "question";
            break;
          }
        }
        break;
      }
    }
    disabledReason = pendingType === "plan"
      ? "Approve or reject the plan above"
      : "Answer the question above first";
  }

  return (
    <div ref={kbContainerRef} className="flex flex-col h-full relative">

      {/* Header */}
      <div className={`shrink-0 bg-surface border-b border-divider px-4 ${compactHeader ? "py-1.5" : "py-2"} safe-area-pt relative z-10`}>
        <div className={`${embedded ? "" : "max-w-2xl"} mx-auto ${compactHeader ? "" : "space-y-1.5"}`}>
          {/* Row 1: Back + name | project + icon buttons */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => { if (onClose) onClose(); else navigate("/agents"); }}
              className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
            >
              {embedded ? (
                <svg className="w-5 h-5 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              ) : (
                <svg className="w-5 h-5 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                </svg>
              )}
            </button>

            {editingName ? (
              <input
                ref={nameInputRef}
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onBlur={submitRename}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitRename();
                  if (e.key === "Escape") setEditingName(false);
                }}
                maxLength={200}
                className="text-sm font-semibold text-heading min-w-0 flex-1 bg-input border border-cyan-500 rounded px-1.5 py-0.5 outline-none"
              />
            ) : (
              <h1
                onDoubleClick={startRename}
                title="Double-tap to rename"
                className="text-sm font-semibold text-heading truncate min-w-0 flex-1 select-none"
              >
                {agent.name}
              </h1>
            )}

            {compactHeader ? (
              /* Compact: status dot + stop/resume + expand chevron */
              <div className="shrink-0 flex items-center gap-1.5">
                <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${statusDot}`} />
                {!(isStopped || isError) && (
                  <PermModeSegmented mode={permMode} onSelect={handleSetMode} disabled={cyclingMode} compact />
                )}
                {(isStopped || isError) ? (
                  <button type="button" onClick={() => handleResume()} disabled={resuming}
                    className="px-2 h-6 flex items-center gap-1 rounded-md text-[10px] font-medium bg-cyan-600 text-white disabled:opacity-50 enabled:hover:bg-cyan-500">
                    <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24"><path d="M6 4l14 8-14 8V4z" /></svg>
                    {resuming ? "..." : "Resume"}
                  </button>
                ) : (
                  <button type="button" onClick={() => setShowStopConfirm(true)}
                    className="px-2 h-6 flex items-center gap-1 rounded-md text-[10px] font-medium bg-red-600 text-white hover:bg-red-500">
                    <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
                    Stop
                  </button>
                )}
                <button type="button" onClick={() => setHeaderExpanded(true)} title="Show details"
                  className="w-6 h-6 flex items-center justify-center rounded-lg text-dim hover:text-body hover:bg-input transition-colors">
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              </div>
            ) : (
              /* Full: icon buttons */
              <div className="shrink-0 flex items-center">
                <button
                  type="button"
                  onClick={async () => {
                    if (syncRefreshing) return;
                    setSyncRefreshing(true);
                    try {
                      await wakeSync(id);
                      setTimeout(() => refreshMessages(), 800);
                    } catch {}
                    setTimeout(() => setSyncRefreshing(false), 400);
                  }}
                  title="Refresh (incremental sync)"
                  className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg text-zinc-400 hover:text-zinc-300 hover:bg-input transition-colors"
                >
                  <svg className={`w-4 h-4 ${syncRefreshing ? "animate-spin" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                </button>
                <button
                  type="button"
                  onClick={() => setShowBrowser(true)}
                  title="Browse files"
                  className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg text-zinc-400 hover:text-zinc-300 hover:bg-input transition-colors"
                >
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                  </svg>
                </button>
                <button
                  type="button"
                  onClick={handleToggleMute}
                  title={muted ? "Unmute notifications" : "Mute notifications"}
                  className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
                >
                  {muted ? (
                    <BellOff className="w-3.5 h-3.5 text-dim hover:text-cyan-400 transition-colors" />
                  ) : (
                    <Bell className="w-3.5 h-3.5 text-cyan-400" />
                  )}
                </button>

                <button
                  type="button"
                  onClick={handleToggleStar}
                  disabled={starLoading}
                  title={starred ? "Unstar session" : "Star session"}
                  className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-input transition-colors disabled:opacity-50"
                >
                  {starred ? (
                    <svg className="w-3.5 h-3.5 text-amber-400" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
                    </svg>
                  ) : (
                    <svg className="w-3.5 h-3.5 text-dim hover:text-amber-400 transition-colors" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
                    </svg>
                  )}
                </button>

                {!embedded && (
                <button
                  type="button"
                  onClick={onToggleTheme}
                  title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                  className="w-7 h-7 flex items-center justify-center rounded-lg text-dim hover:text-heading hover:bg-input transition-colors"
                >
                  {theme === "dark" ? (
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
                    </svg>
                  ) : (
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
                    </svg>
                  )}
                </button>
                )}

                {embedded && (
                <button type="button" onClick={() => setHeaderExpanded(false)} title="Collapse"
                  className="w-7 h-7 flex items-center justify-center rounded-lg text-dim hover:text-body hover:bg-input transition-colors">
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
                  </svg>
                </button>
                )}
              </div>
            )}
          </div>


          {/* Row 2: Status + model + branch | action buttons (ml-9 aligns with name after back btn) */}
          {!compactHeader && <div className="flex items-center gap-2 ml-9">
            <div className="flex items-center gap-1.5 min-w-0 flex-1">
              <span className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${statusDot}${agent.status === "EXECUTING" ? " animate-pulse" : ""}`} />
              <span className={`text-xs shrink-0 ${statusText}`}>
                {agent.status.toLowerCase().replace("_", " ")}
              </span>
              {hasTmux && (
                <span className="text-[10px] text-emerald-400 font-medium px-1.5 py-0.5 rounded bg-emerald-500/15 shrink-0">
                  tmux
                </span>
              )}
              {agent.model && (
                <span className="text-[10px] text-faint font-medium px-1.5 py-0.5 rounded bg-elevated shrink-0">
                  {modelDisplayName(agent.model)}
                </span>
              )}
              <span
                className="text-[10px] text-cyan-400 font-medium px-1.5 py-0.5 rounded bg-cyan-500/10 truncate cursor-pointer hover:bg-cyan-500/20 transition-colors"
                onClick={() => navigate(`/projects/${encodeURIComponent(agent.project)}`)}
                title={agent.project}
              >
                {agent.project}
              </span>
              {agent.branch && (
                <span className="text-xs text-violet-400 font-mono truncate max-w-[120px]">
                  {agent.branch}
                </span>
              )}
            </div>

            <div className="shrink-0 flex items-center gap-1.5">
              {/* "Continued" link — only when a successor exists */}
              {(isStopped || isError) && agent?.successor_id && (
                <button
                  type="button"
                  onClick={() => embedded && onNavigateAgent ? onNavigateAgent(agent.successor_id) : navigate(`/agents/${agent.successor_id}`)}
                  className="px-2.5 h-7 flex items-center gap-1 rounded-lg text-xs font-medium bg-violet-600 hover:bg-violet-500 text-white transition-colors"
                >
                  Continued
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                  </svg>
                </button>
              )}
              {/* Permission mode segmented — hidden when stopped */}
              {!(isStopped || isError) && (
                <PermModeSegmented mode={permMode} onSelect={handleSetMode} disabled={cyclingMode} />
              )}
              {/* Resume / Stop — show one at a time */}
              {(isStopped || isError) ? (
                <button
                  type="button"
                  onClick={() => handleResume()}
                  disabled={resuming}
                  className="px-2.5 h-7 flex items-center gap-1 rounded-lg text-xs font-medium bg-cyan-600 text-white transition-colors disabled:opacity-50 enabled:hover:bg-cyan-500"
                >
                  <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M6 4l14 8-14 8V4z" />
                  </svg>
                  {resuming ? "..." : "Resume"}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setShowStopConfirm(true)}
                  className="px-2.5 h-7 flex items-center gap-1 rounded-lg text-xs font-medium bg-red-600 text-white transition-colors hover:bg-red-500"
                >
                  <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                    <rect x="6" y="6" width="12" height="12" rx="2" />
                  </svg>
                  Stop
                </button>
              )}

              <button
                type="button"
                onClick={() => navigate("/monitor")}
                title={health === null ? "Checking..." : isHealthy ? "System healthy" : "System issue"}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium transition-colors hover:opacity-80 ${healthChipCls}`}
              >
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${healthDotColor} ${!isHealthy && health !== null ? "animate-pulse" : ""}`} />
                {healthLabel}
              </button>
            </div>
          </div>}
        </div>
      </div>

      {/* Agent ID + session size + parent link */}
      {!compactHeader && <div className="shrink-0 bg-surface border-b border-divider px-4 py-1">
        <div className={`${embedded ? "" : "max-w-2xl"} mx-auto flex items-center gap-2 pl-2.5`}>
          {agent.parent_id && (
            <button
              type="button"
              onClick={() => embedded && onNavigateAgent ? onNavigateAgent(agent.parent_id) : navigate(`/agents/${agent.parent_id}`)}
              className="text-[10px] text-cyan-400 hover:underline flex items-center gap-0.5"
            >
              <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
              </svg>
              Continued from previous session
            </button>
          )}
          {agent.task_id && (
            <span
              className="shrink-0 text-[10px] text-orange-500 opacity-50 cursor-pointer hover:opacity-80 transition-colors"
              onClick={() => setShowTaskCard(true)}
              title="View task"
            >
              Working on task
            </span>
          )}
          <span className="ml-auto flex items-center gap-2">
            <span
              className="text-[10px] text-faint font-mono opacity-50 cursor-pointer active:text-cyan-400 transition-colors select-none"
              onDoubleClick={() => {
                navigator.clipboard.writeText(agent.id).then(() => {
                  showToast("Copied " + agent.id);
                }).catch(() => {});
              }}
              title="Double-tap to copy"
            >{agent.id}</span>
            {agent.session_size_bytes != null && agent.session_size_bytes > 0 && (
              <span className="flex items-center gap-1" title="Large sessions use more tokens per message. Consider using /compact in the CLI.">
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                  agent.session_size_bytes < 512000 ? "bg-green-500" :
                  agent.session_size_bytes < 2097152 ? "bg-amber-500" : "bg-red-500"
                }`} />
                <span className="text-[10px] text-dim">
                  {agent.session_size_bytes < 1024 ? `${agent.session_size_bytes} B` :
                    agent.session_size_bytes < 1048576 ? `${(agent.session_size_bytes / 1024).toFixed(1)} KB` :
                    `${(agent.session_size_bytes / 1048576).toFixed(1)} MB`}
                </span>
              </span>
            )}
          </span>
        </div>
      </div>}

      {/* Messages */}
      <div
        ref={scrollContainerRef}
        data-chat-container
        onScroll={handleScroll}
        className={`flex-1 overflow-y-auto overflow-x-hidden px-4 py-3 ${kbOpen ? "" : "pb-36"} ${embedded ? "" : "max-w-2xl"} mx-auto w-full flex flex-col`}
        style={{ overflowAnchor: "auto", overscrollBehavior: "contain" }}
      >
        <div className="mt-auto" />
        {messages.length === 0 && agent.status === "STARTING" ? (
          <InitializingIndicator />
        ) : messages.length === 0 && agent.status === "IDLE" ? (
          <SyncPrompt agentId={id} onSync={refreshMessages} />
        ) : (
          <>
            {/* Lazy-load indicator at top */}
            {loadingMore && (
              <div className="text-center py-3 text-xs opacity-60">Loading older messages...</div>
            )}
            {!hasMore && messages.length > 0 && (
              <div className="text-center py-3 text-xs opacity-40">Beginning of conversation</div>
            )}

            {/* Previous attempt summary + attempt navigation for retry agents */}
            {(() => {
              const attempts = taskData?.attempt_agents || [];
              const myIdx = attempts.findIndex(a => a.agent_id === id);
              if (myIdx < 1) return null;
              const total = attempts.length;
              const sel = selectedAttempt ?? myIdx;
              const selAgent = attempts[sel];
              const isSelCurrent = selAgent?.agent_id === id;
              // agent_summary describes attempt (total-2); retry_context is feedback about (total-2)
              const showSummary = sel < total - 1 && sel === total - 2 && taskData.agent_summary;
              const showUserFeedback = sel < total - 1 && sel === total - 2 && taskData.retry_context;
              return (
                <div className="mx-auto w-[85%] mb-3 rounded-xl bg-orange-500/8 border border-orange-500/20 px-4 py-3">
                  {/* Attempt pills + Enter Chat */}
                  <div className="flex items-center gap-1.5 mb-2">
                    <span className="text-[10px] font-semibold text-orange-500 dark:text-orange-400 mr-1">Attempts</span>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {attempts.map((a, i) => (
                        <button
                          key={a.agent_id}
                          type="button"
                          onClick={() => setSelectedAttempt(i)}
                          className={`px-2 py-0.5 rounded-full text-[10px] font-medium transition-colors ${
                            i === sel
                              ? "bg-orange-500 text-white"
                              : "bg-transparent border border-orange-500/40 text-orange-500 dark:text-orange-400 hover:bg-orange-500/15"
                          }`}
                        >
                          #{i + 1}
                        </button>
                      ))}
                    </div>
                    {!isSelCurrent && (
                      <button
                        type="button"
                        onClick={() => embedded && onNavigateAgent ? onNavigateAgent(selAgent.agent_id) : navigate(`/agents/${selAgent.agent_id}`)}
                        className="ml-auto text-[10px] text-orange-500 dark:text-orange-400 hover:underline"
                      >
                        Enter Chat →
                      </button>
                    )}
                  </div>
                  {taskData.description && (
                    <p className="text-xs text-dim/80 whitespace-pre-wrap">{taskData.description.replace(/\[Attached file: [^\]]+\]/g, "").trim()}</p>
                  )}
                  {showSummary && (
                    <details className="mt-2">
                      <summary className="text-[10px] font-medium text-orange-500 dark:text-orange-400 cursor-pointer select-none list-none flex items-center gap-1">
                        <span className="transition-transform duration-200 text-[8px] [details[open]>&]:rotate-90">▶</span>
                        Agent Summary
                      </summary>
                      <div className="mt-1">
                        {taskData.agent_summary === ":::generating:::" ? (
                          <p className="text-xs text-dim/50 italic">Generating summary...</p>
                        ) : (
                          <p className="text-xs text-dim/80 whitespace-pre-wrap">{taskData.agent_summary}</p>
                        )}
                      </div>
                    </details>
                  )}
                  {showUserFeedback && (
                    <details className="mt-2">
                      <summary className="text-[10px] font-medium text-orange-500 dark:text-orange-400 cursor-pointer select-none list-none flex items-center gap-1">
                        <span className="transition-transform duration-200 text-[8px] [details[open]>&]:rotate-90">▶</span>
                        User Feedback
                      </summary>
                      <div className="mt-1">
                        <p className="text-xs text-dim/80 whitespace-pre-wrap">{taskData.retry_context}</p>
                      </div>
                    </details>
                  )}
                </div>
              );
            })()}

            {(() => {
              const isRetryAgent = (taskData?.attempt_agents || []).findIndex(a => a.agent_id === id) >= 1;
              const retryContext = taskData?.retry_context;
              let replacedFirstTask = false;
              const visible = messages.filter((m) => !(m.role === "USER" && (m.status === "PENDING" || m.status === "QUEUED")) && m.kind !== "stop_hook")
                .map((m) => {
                  // For retry agents, replace first source=task user message text with retry_context,
                  // but keep all [Attached file: ...] tags from both original and retry_context
                  if (isRetryAgent && retryContext && !replacedFirstTask && m.role === "USER" && m.source === "task") {
                    replacedFirstTask = true;
                    const origAttachments = (m.content || "").match(/\[Attached file: [^\]]+\]/g) || [];
                    const retryText = retryContext.replace(/^User feedback:\s*/i, "");
                    const retryAttachments = retryText.match(/\[Attached file: [^\]]+\]/g) || [];
                    const cleanText = retryText.replace(/\[Attached file: [^\]]+\]/g, "").trim();
                    const allAttachments = [...new Set([...retryAttachments, ...origAttachments])];
                    return { ...m, content: cleanText + (allAttachments.length ? "\n" + allAttachments.join("\n") : "") };
                  }
                  return m;
                });
              console.log('[messages] rendering', visible.length, 'messages after filter');
              // Build tool groups: consecutive tool_use + tool_activity messages get merged.
              // Skill tool_uses render as their own SkillBubble — they break the group
              // so they're neither collapsed into ToolLogBubble nor absorb neighbors.
              const toolGroups = new Map(); // first msg id -> [entries]
              let groupStart = null;
              for (let i = 0; i < visible.length; i++) {
                const m = visible[i];
                const isToolUse = m.role === "AGENT" && m.kind === "tool_use";
                const isToolActivity = m.kind === "tool_activity";
                const isSkill = isToolUse && m.metadata?.tool_name === "Skill";
                if (isSkill) {
                  groupStart = null;
                  continue;
                }
                if (isToolUse || isToolActivity) {
                  if (!groupStart) groupStart = m.id;
                  if (!toolGroups.has(groupStart)) toolGroups.set(groupStart, []);
                  // Skip tool_activity start-phase entries (duplicates of end-phase)
                  if (isToolActivity && (m.metadata?.phase) === "start") continue;
                  if (isToolUse) {
                    const match = (m.content || "").match(TOOL_SUMMARY_RE);
                    toolGroups.get(groupStart).push({
                      name: match ? match[1] : "Tool",
                      summary: match ? match[2] : m.content || "",
                    });
                  } else {
                    const meta = m.metadata || {};
                    toolGroups.get(groupStart).push({
                      name: meta.tool_name || "Tool",
                      summary: m.content || "",
                    });
                  }
                } else {
                  groupStart = null;
                }
              }
              if (toolGroups.size > 0) console.log('[render] tool groups:', toolGroups.size, 'groups,', [...toolGroups.values()].map(g => g.length + ' entries'));
              // Map each text message to preceding tool entries for media extraction
              const toolEntriesForText = new Map();
              {
                let acc = [];
                for (const m of visible) {
                  const isToolMsg = (m.role === "AGENT" && m.kind === "tool_use") || m.kind === "tool_activity";
                  if (isToolMsg) {
                    const ge = toolGroups.get(m.id);
                    if (ge) acc.push(...ge);
                  } else if (m.role === "AGENT" && m.kind === "text") {
                    if (acc.length > 0) { toolEntriesForText.set(m.id, acc); acc = []; }
                  } else {
                    acc = [];
                  }
                }
              }
              return visible.map((msg) => {
                // Skill tool_use — standalone collapsible bubble with injected skill.md body
                if (msg.kind === "tool_use" && msg.metadata?.tool_name === "Skill") {
                  return <div key={msg.id} data-msg-id={msg.id} data-msg-type="skill"><SkillBubble message={msg} project={agent.project} /></div>;
                }
                // Grouped tool entries (tool_use + tool_activity) — handled before role checks
                if (msg.kind === "tool_use" || msg.kind === "tool_activity") {
                  if (toolGroups.has(msg.id)) {
                    const entries = toolGroups.get(msg.id);
                    if (entries.length > 0) {
                      return <div key={msg.id} data-msg-id={msg.id} data-msg-type="tool_group"><ToolLogBubble entries={entries} /></div>;
                    }
                  }
                  return null; // part of group or start-only leader with no entries
                }
                if (msg.role === "AGENT" && !(msg.content || "").trimStart().startsWith("<task-notification>")) {
                  console.log('[render] msg', msg.id, 'role=', msg.role, 'kind=', msg.kind);
                  // Case 2: text kind — render as simple ChatBubble
                  if (msg.kind === "text") {
                    return <div key={msg.id} data-msg-id={msg.id} data-msg-type="agent_text"><ChatBubble message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={refreshMessages} toolEntries={toolEntriesForText.get(msg.id)} /></div>;
                  }
                  // Case 3: null/undefined kind (legacy) — existing splitMessageSegments logic
                  console.log('[render] legacy split for msg', msg.id);
                  const segments = splitMessageSegments(msg.content || "");
                  if (segments.length > 1 || segments[0]?.type === "tools") {
                    const lastTextIdx = segments.findLastIndex((s) => s.type === "text");
                    return (
                      <div key={msg.id} data-msg-id={msg.id} data-msg-type="legacy_split">
                        {segments.map((seg, i) => {
                          if (seg.type === "tools") return <ToolLogBubble key={`${msg.id}-t${i}`} entries={seg.entries} />;
                          if (i === lastTextIdx) return <ChatBubble key={`${msg.id}-c`} message={msg} contentOverride={seg.text} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={refreshMessages} />;
                          return <AgentTextSegment key={`${msg.id}-s${i}`} text={seg.text} project={agent.project} />;
                        })}
                        {lastTextIdx === -1 && msg.metadata?.interactive?.length > 0 && (
                          <ChatBubble key={`${msg.id}-c`} message={msg} contentOverride="" project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={refreshMessages} />
                        )}
                      </div>
                    );
                  }
                }
                return <div key={msg.id} data-msg-id={msg.id} data-msg-type={msg.role === "USER" ? "user" : msg.role === "SYSTEM" ? "system" : "agent_default"}><ChatBubble message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={refreshMessages} /></div>;
              });
            })()}

            {/* Streaming output or typing indicator while executing/idle */}
            {(() => {
              // Guard: don't show streaming bubble if content matches
              // the last saved AGENT message (prevents duplicate bubbles
              // when the sync loop re-emits already-committed content).
              if (streamingContent) {
                const lastAgent = [...messages].reverse().find((m) => m.role === "AGENT");
                const isDuplicate = lastAgent && (
                  lastAgent.content === streamingContent
                  || lastAgent.content.startsWith(streamingContent.slice(0, 200))
                );
                if (!isDuplicate) return <div data-msg-id="streaming" data-msg-type="streaming_bubble"><StreamingBubble content={streamingContent} project={agent.project} activeTool={activeTool} /></div>;
              }
              // Tool activity log — shows completed + in-progress tool calls
              return isExecuting ? <div data-msg-id="typing" data-msg-type="typing_indicator"><TypingIndicator /></div> : null;
            })()}

            {/* Queued/pending/scheduled messages always at the bottom */}
            {(() => {
              const queued = messages.filter((m) => m.role === "USER" && (m.status === "QUEUED" || (m.status === "PENDING" && !m.scheduled_at)));
              const scheduled = messages.filter((m) => m.role === "USER" && m.status === "PENDING" && m.scheduled_at);
              return (
                <>
                  {queued.map((msg, idx) => (
                    <div key={msg.id} data-msg-id={msg.id} data-msg-type="queued_msg"><ChatBubble message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={refreshMessages} queuePosition={idx + 1} queueTotal={queued.length} /></div>
                  ))}
                  {scheduled.map((msg) => (
                    <div key={msg.id} data-msg-id={msg.id} data-msg-type="scheduled_msg"><ChatBubble message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={refreshMessages} /></div>
                  ))}
                </>
              );
            })()}
          </>
        )}


        {/* Progress suggestions card */}
        {agent?.status === "STOPPED" && agent?.insight_status === "generating" && (
          <InsightStatusCard status="generating" agentId={id} onRetry={loadData} />
        )}
        {agent?.status === "STOPPED" && agent?.insight_status === "failed" && (
          <InsightStatusCard status="failed" agentId={id} onRetry={loadData} />
        )}
        {agent?.has_pending_suggestions && agent?.status === "STOPPED" && !agent?.insight_status && (
          <ProgressSuggestionsCard agentId={id} onDone={loadData} />
        )}
        {!agent?.has_pending_suggestions && agent?.status === "STOPPED" && !agent?.insight_status && (
          <InsightsHistoryCard agentId={id} />
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Sync stale banner */}
      {agent.sync_stale && (
        <div className="shrink-0 flex items-center justify-center gap-2 px-4 py-2 bg-yellow-500/10 border-t border-yellow-500/20 text-yellow-400 text-xs">
          <span>No new messages for 10+ minutes</span>
          <button
            type="button"
            onClick={async () => {
              if (syncRefreshing) return;
              setSyncRefreshing(true);
              try {
                await wakeSync(id);
                setTimeout(() => refreshMessages(), 800);
              } catch {}
              setTimeout(() => setSyncRefreshing(false), 400);
            }}
            className="px-2 py-0.5 rounded bg-yellow-500/20 hover:bg-yellow-500/30 text-yellow-300 transition-colors"
          >
            {syncRefreshing ? "Syncing…" : "Wake Sync"}
          </button>
        </div>
      )}

      {/* Input bar */}
      <ChatInput
        agentId={id}
        onSend={handleSend}
        onSendLater={handleSendLater}
        disabled={isStarting || isStopped || isError || hasPendingInteractive}
        disabledReason={disabledReason}
        isBusy={!hasTmux && isExecuting}
        tmuxMode={hasTmux}
        onEscape={hasTmuxPane ? async () => {
          try { await escapeAgent(id); loadData(); } catch (e) { showToast(e.message || "Escape failed", "error"); }
        } : null}
        escapeDisabled={isStopped || isError}
        escapeUrgent={isExecuting || hasPendingInteractive}
        escapeAvailable={hasTmuxPane}
        scrollButton={showScrollToBottom ? (
          <button
            type="button"
            onPointerDown={(e) => e.preventDefault()}
            onClick={() => {
              messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
              setShowScrollToBottom(false);
            }}
            className="glass-bar w-9 h-9 rounded-full flex items-center justify-center text-dim hover:text-heading transition-all active:scale-90 pointer-events-auto mb-2"
          >
            <svg className="w-4.5 h-4.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 14l-7 7m0 0l-7-7m7 7V3" />
            </svg>
          </button>
        ) : null}
        kbOpen={kbOpen}
      />

      {/* Stop confirmation modal */}
      {showStopConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-surface rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-card">
            <h3 className="text-lg font-bold text-heading">Stop Agent?</h3>
            <p className="text-sm text-label">
              This will stop the agent. You won't be able to send more messages.
            </p>
            {agent?.task_id && (
              <>
                {/* Task outcome toggle */}
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setTaskOutcome("complete")}
                    className={`flex-1 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                      taskOutcome === "complete"
                        ? "bg-green-600 text-white"
                        : "bg-input text-body hover:bg-elevated"
                    }`}
                  >
                    Done
                  </button>
                  <button
                    type="button"
                    onClick={() => setTaskOutcome("incomplete")}
                    className={`flex-1 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                      taskOutcome === "incomplete"
                        ? "bg-amber-600 text-white"
                        : "bg-input text-body hover:bg-elevated"
                    }`}
                  >
                    Redo
                  </button>
                  <button
                    type="button"
                    onClick={() => setTaskOutcome("drop")}
                    className={`flex-1 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                      taskOutcome === "drop"
                        ? "bg-gray-600 text-white"
                        : "bg-input text-body hover:bg-elevated"
                    }`}
                  >
                    Drop
                  </button>
                </div>
                {/* Feedback note for Redo / Drop */}
                {taskOutcome !== "complete" && (
                  <div className="space-y-2">
                    <textarea
                      value={incompleteReason}
                      onChange={(e) => setIncompleteReason(e.target.value)}
                      onPaste={handleFeedbackPaste}
                      placeholder="What should be done differently? (optional)"
                      rows={3}
                      className="w-full px-3 py-2 rounded-lg bg-input border border-divider text-body text-sm placeholder:text-dim resize-none focus:outline-none focus:ring-1 focus:ring-amber-500/50"
                    />
                    {/* Feedback attachment previews */}
                    {feedbackAttachments.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {feedbackAttachments.map((att) => (
                          <div key={att.id} className="flex items-center gap-1 px-2 py-1 rounded-lg bg-elevated text-xs max-w-[140px]">
                            {att.previewUrl ? (
                              <img src={att.previewUrl} alt="" className="w-8 h-8 rounded object-cover shrink-0" />
                            ) : (
                              <svg className="w-4 h-4 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                              </svg>
                            )}
                            <span className="truncate text-label flex-1 min-w-0">{att.originalName}</span>
                            {att.uploading ? (
                              <svg className="w-3.5 h-3.5 text-amber-400 animate-spin shrink-0" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                              </svg>
                            ) : (
                              <button type="button" onClick={() => removeFeedbackAttachment(att.id)} className="text-dim hover:text-heading shrink-0">
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                                </svg>
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    {/* Toolbar: attach + voice */}
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => feedbackFileInputRef.current?.click()}
                        title="Attach files"
                        className="w-8 h-8 rounded-full flex items-center justify-center bg-elevated hover:bg-hover text-label transition-colors"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                        </svg>
                      </button>
                      <input ref={feedbackFileInputRef} type="file" accept="image/*,video/*,.pdf,.txt,.csv,.json,.md,.py,.js,.ts,.jsx,.tsx,.html,.css,.yaml,.yml,.xml,.log,.zip,.tar,.gz" multiple className="hidden" onChange={(e) => { const files = Array.from(e.target.files || []); e.target.value = ""; if (files.length > 0) addFeedbackFiles(files); }} />
                      <div className="flex-1" />
                      {feedbackVoice.recording && feedbackVoice.remainingSeconds != null && (
                        <span className={`text-xs font-semibold tabular-nums ${feedbackVoice.remainingSeconds <= 10 ? "text-red-400" : "text-red-500"}`}>
                          {feedbackVoice.remainingSeconds >= 60
                            ? `${Math.floor(feedbackVoice.remainingSeconds / 60)}:${String(feedbackVoice.remainingSeconds % 60).padStart(2, "0")}`
                            : feedbackVoice.remainingSeconds}
                        </span>
                      )}
                      <VoiceRecorder
                        recording={feedbackVoice.recording}
                        voiceLoading={feedbackVoice.voiceLoading}
                        refining={feedbackVoice.refining}
                        micError={feedbackVoice.micError}
                        onToggle={feedbackVoice.toggleRecording}
                      />
                    </div>
                    {feedbackVoice.streamingText && (
                      <div className="px-1 text-xs text-amber-400/80 italic animate-pulse">{feedbackVoice.streamingText}</div>
                    )}
                  </div>
                )}
                {/* Generate summary checkbox */}
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={generateSummary}
                    onChange={(e) => setGenerateSummary(e.target.checked)}
                    className="w-4 h-4 rounded border-divider bg-input text-amber-500 focus:ring-amber-500/50"
                  />
                  <span className="text-sm text-body">Generate progress summary</span>
                </label>
              </>
            )}
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
                onClick={() => { setShowStopConfirm(false); setTaskOutcome("complete"); setIncompleteReason(""); clearFeedbackAttachments(); }}
                className="flex-1 min-h-[44px] rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {fileModal && agent && (
        <ProjectFileModal
          project={agent.project}
          filename={fileModal}
          onClose={() => {
            setFileModal(null);
            Promise.all([
              fetchProjectFile(agent.project, "CLAUDE.md").catch(() => ({ exists: false })),
              fetchProjectFile(agent.project, "PROGRESS.md").catch(() => ({ exists: false })),
            ]).then(([c, p]) => {
              setFileExists({ "CLAUDE.md": c.exists, "PROGRESS.md": p.exists });
            });
          }}
        />
      )}

      {showBrowser && agent && (
        <ProjectBrowserModal
          project={agent.project}
          agentId={id}
          onClose={() => setShowBrowser(false)}
        />
      )}

      {showTaskCard && agent?.task_id && (
        <FloatingTaskCard
          taskId={agent.task_id}
          onClose={() => setShowTaskCard(false)}
          onAction={(action) => {
            setShowTaskCard(false);
            if (action === "complete") {
              setTaskOutcome("complete");
              setShowStopConfirm(true);
            } else if (action === "incomplete") {
              setTaskOutcome("incomplete");
              setShowStopConfirm(true);
            }
          }}
        />
      )}
    </div>
  );
}
