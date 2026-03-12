import { useState, useEffect, useLayoutEffect, useCallback, useRef, useMemo, Component } from "react";
import { Bell, BellOff } from "lucide-react";
import { useParams, useNavigate } from "react-router-dom";
import {
  fetchAgent,
  fetchMessages,
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
  uploadFile,
  fetchProjectFile,
} from "../lib/api";
import ProjectFileModal from "../components/ProjectFileModal";
import ProjectBrowserModal from "../components/ProjectBrowserModal";
import { relativeTime, renderMarkdown, extractFileAttachments, stripAttachmentTags } from "../lib/formatters";

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
import WaveformVisualizer from "../components/WaveformVisualizer";
import useDraft from "../hooks/useDraft";
import useVoiceRecorder from "../hooks/useVoiceRecorder";
import useWebSocket, { isAgentMuted, setAgentMuted, clearAgentNotified, registerViewing, unregisterViewing } from "../hooks/useWebSocket";
import useHealthStatus from "../hooks/useHealthStatus";
import usePageVisible from "../hooks/usePageVisible";
import { useToast } from "../contexts/ToastContext";

const ACTIVE_AGENT_STATUSES = new Set(["EXECUTING", "SYNCING"]);
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
        className={`inline-block max-w-[90%] px-3 py-1 rounded-lg bg-elevated text-xs text-dim text-left ${isLong ? "cursor-pointer hover:bg-hover transition-colors" : "cursor-default"}`}
      >
        <div className="flex items-center gap-1.5">
          <span className="shrink-0 opacity-60">sys</span>
          <span className="truncate">{label}</span>
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

// --- Sub-agent task notification bubble (collapsible) ---

function SubAgentBubble({ message, project }) {
  const [expanded, setExpanded] = useState(false);
  const content = message.content || "";
  const status = content.match(/<status>(.*?)<\/status>/s)?.[1] || "";
  const summary = content.match(/<summary>([\s\S]*?)<\/summary>/)?.[1] || "";
  const result = content.match(/<result>([\s\S]*?)<\/result>/)?.[1]?.trim();

  const statusColor = status === "completed"
    ? "text-emerald-400"
    : status === "failed" ? "text-red-400" : "text-amber-400";
  const borderColor = status === "completed"
    ? "border-emerald-500/30"
    : status === "failed" ? "border-red-500/30" : "border-amber-500/30";

  return (
    <div className="flex justify-start my-2">
      <div className="max-w-[85%]">
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
        const isAnswered = answeredIdx != null || isDismissed;
        // Sequential lock: question qi is locked if any prior question is unanswered
        const isLocked = qi > 0 && !isDismissed && resolveIdx(questions[qi - 1], qi - 1) == null;

        // Badge text & style
        let badgeText = null;
        let badgeClass = "";
        if (isDismissed) {
          badgeText = "Dismissed";
          badgeClass = "bg-red-500/20 text-red-300";
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

// --- Interactive: ExitPlanMode ---

// Must match the exact order Claude CLI shows in the ExitPlanMode TUI
const PLAN_OPTIONS = [
  { label: "Yes, clear context & bypass", description: "Approve, compact context, and skip permission prompts", color: "emerald" },
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
  if (/clear context/.test(a)) return 0;
  if (/bypass/.test(a) && !/clear/.test(a) && !/manual/.test(a)) return 1;
  if (/manual/.test(a)) return 2;
  if (/feedback|type here|tell claude/.test(a)) return 3;
  if (/^yes\b/.test(a) || a === "approve" || a === "approved") return 2; // safe default: manual approval
  if (/^no\b/.test(a) || a === "reject") return 3; // feedback mode, not destructive
  return null; // unrecognized input — do not select any option
}

function _isPlanDismissed(item) {
  if (!item.answer || typeof item.answer !== "string") return false;
  return item.answer.startsWith("The user doesn't want to proceed") || item.answer.startsWith("User declined") || item.answer.startsWith("Tool use rejected");
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
  const isAnswered = effectiveIdx != null || isDismissed;

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

function ChatBubble({ message, project, onCancelMessage, onUpdateMessage, onSendNow, agentId, onRefresh, queuePosition, queueTotal }) {
  if (message.role === "SYSTEM") {
    return <SystemBubble message={message} />;
  }

  // Sub-agent task notifications get their own collapsible bubble
  if ((message.content || "").trimStart().startsWith("<task-notification>")) {
    return <SubAgentBubble message={message} project={project} />;
  }

  const isUser = message.role === "USER";
  const isScheduled = isUser && message.scheduled_at && message.status === "PENDING";
  const isPending = isUser && message.status === "PENDING" && !message.scheduled_at;

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

  const attachments = useMemo(
    () => extractFileAttachments(message.content, project, message.role),
    [message.content, project, message.role],
  );

  // Strip [Attached file: ...] tags from user message display text
  const displayContent = useMemo(
    () => isUser ? stripAttachmentTags(message.content) : message.content,
    [isUser, message.content],
  );

  const scheduledTime = isScheduled
    ? new Date(message.scheduled_at).toLocaleTimeString([], TIME_SHORT)
    : null;

  // Editing UI for scheduled/pending messages
  const editDateRef = useRef(null);

  if (editing) {
    const scheduleLabel = editSchedule
      ? new Date(editSchedule).toLocaleString([], DATE_SHORT)
      : "Set time";

    return (
      <div className="flex justify-end my-2">
        <div className="max-w-[85%]">
          <div className="rounded-2xl px-4 py-2.5 bg-amber-600/60 text-white rounded-br-md space-y-2 overflow-hidden">
            <textarea
              ref={editTextareaRef}
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              rows={2}
              className="w-full rounded-lg bg-black/20 border border-amber-400/40 px-2 py-1.5 text-sm text-white placeholder-amber-200/50 resize-none focus:border-amber-300 focus:outline-none"
            />
            <input
              ref={editDateRef}
              type="datetime-local"
              value={editSchedule}
              onChange={(e) => setEditSchedule(e.target.value)}
              min={(() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`; })()}
              className="sr-only"
              tabIndex={-1}
            />
            <button
              type="button"
              onClick={() => editDateRef.current?.showPicker?.() || editDateRef.current?.click()}
              className="w-full rounded-lg bg-amber-500 hover:bg-amber-400 text-white text-sm py-1.5 font-medium transition-colors flex items-center justify-center gap-2"
            >
              <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              {scheduleLabel}
            </button>
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
      <div className="max-w-[85%] relative">
        <div
          className={`rounded-2xl px-4 py-2.5 ${
            isUser
              ? isScheduled
                ? "bg-amber-600/80 text-white rounded-br-md"
                : isPending
                  ? "bg-cyan-600/60 text-white/80 rounded-br-md"
                  : "bg-cyan-600 text-white rounded-br-md"
              : "bg-surface shadow-card text-body rounded-bl-md"
          } ${canModify ? "select-none" : ""}`}
          onDoubleClick={handleDoubleClick}
          onTouchStart={handleLongPressStart}
          onTouchEnd={handleLongPressEnd}
          onTouchCancel={handleLongPressEnd}
        >
          {isUser ? (
            displayContent && <p className="text-sm whitespace-pre-wrap">{displayContent}</p>
          ) : (
            <div className="text-sm" ref={markdownRef} onClick={handleMarkdownClick}>
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
        {attachments.length > 0 && <FileAttachments attachments={attachments} />}
        {userInsights && userInsights.length > 0 && (
          <InsightsBubble insights={userInsights} />
        )}
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

// --- Typing Indicator (shown when executing but no streaming content yet) ---

function TypingIndicator({ activeTool, toolStartTime }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!toolStartTime) { setElapsed(0); return; }
    setElapsed(Math.floor((Date.now() - toolStartTime) / 1000));
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - toolStartTime) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [toolStartTime]);

  return (
    <div className="flex justify-start my-2">
      <div className="bg-surface shadow-card rounded-2xl rounded-bl-md px-5 py-3.5">
        {activeTool ? (
          <div className="flex items-center gap-2">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            <span className="text-xs text-dim">
              <code className="text-[11px] px-1 py-0.5 rounded bg-elevated text-cyan-300 font-mono">{activeTool.name}</code>
              {" "}running...
              {activeTool.summary && (
                <span className="text-faint ml-1 font-mono text-[11px] truncate inline-block max-w-[200px] align-bottom">{activeTool.summary}</span>
              )}
              {elapsed > 3 && <span className="text-faint ml-1">({elapsed}s)</span>}
            </span>
          </div>
        ) : (
          <div className="flex items-center gap-[5px]">
            <span className="typing-dot" style={{ animationDelay: "0ms" }} />
            <span className="typing-dot" style={{ animationDelay: "200ms" }} />
            <span className="typing-dot" style={{ animationDelay: "400ms" }} />
          </div>
        )}
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

// --- Streaming Bubble (live output while agent is executing) ---

function StreamingBubble({ content, project, activeTool }) {
  return (
    <div className="flex justify-start my-2">
      <div className="max-w-[85%]">
        <div className="rounded-2xl px-4 py-2.5 bg-surface shadow-card text-body rounded-bl-md">
          <div className="text-sm">
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

function ChatInput({ agentId, onSend, onSendLater, disabled, disabledReason, isBusy, tmuxMode, onEscape, escapeUrgent, escapeAvailable = true, escapeDisabled = false }) {
  const [text, setText] = useDraft(agentId ? `chat:${agentId}` : null, "");
  const [showPicker, setShowPicker] = useState(false);
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

  const voice = useVoiceRecorder({
    onTranscript: (t) => setText((prev) => (prev ? prev + " " + t : t)),
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
            ? `/api/uploads/${a.uploadedPath.split("/").pop()}`
            : null
        ),
      }));
      try { localStorage.setItem(attachmentCacheKey, JSON.stringify(toCache)); } catch { /* ignore */ }
    } else {
      try { localStorage.removeItem(attachmentCacheKey); } catch { /* ignore */ }
    }
  }, [attachments, attachmentCacheKey]);

  const buildMessageText = useCallback((baseText, atts) => {
    let msg = baseText;
    for (const a of atts) {
      if (a.uploadedPath) msg += `\n[Attached file: ${a.uploadedPath}]`;
    }
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
    if (!text.trim() && uploaded.length === 0) return;
    if (disabled && !isBusy) return;
    const msg = buildMessageText(text.trim(), uploaded);
    onSend(msg);
    setText("");
    clearAttachments();
    pendingSendRef.current = null;
  }, [text, attachments, disabled, isBusy, onSend, setText, buildMessageText, clearAttachments]);

  const handleSchedule = useCallback((scheduledAt) => {
    const uploading = attachments.some((a) => a.uploading);
    if (uploading) {
      pendingSendRef.current = { type: "schedule", scheduledAt };
      return;
    }
    const uploaded = attachments.filter((a) => a.uploadedPath);
    if (!text.trim() && uploaded.length === 0) return;
    const msg = buildMessageText(text.trim(), uploaded);
    onSendLater(msg, scheduledAt);
    setText("");
    clearAttachments();
    setShowPicker(false);
    pendingSendRef.current = null;
  }, [text, attachments, onSendLater, setText, buildMessageText, clearAttachments]);

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
    <div className="absolute bottom-0 left-0 right-0 pb-2 safe-area-pb-tight flex justify-center px-4 z-20 pointer-events-none">
      <div
        className="glass-bar-nav rounded-[22px] px-3 pt-2 pb-2.5 flex flex-col gap-2 w-full relative pointer-events-auto"
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
          <div className="flex-1 min-w-0 overflow-hidden">
            {voice.recording && voice.analyserNode && (
              <WaveformVisualizer analyserNode={voice.analyserNode} remainingSeconds={voice.remainingSeconds} onTap={voice.toggleRecording} className="h-8" />
            )}
          </div>
          <VoiceRecorder
            recording={voice.recording}
            voiceLoading={voice.voiceLoading}
            micError={voice.micError}
            onToggle={voice.toggleRecording}
          />
          {/* Escape button — sends Esc to tmux (always visible for cli_sync agents, disabled when stopped/error) */}
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
            src: a.previewUrl || `/api/uploads/${a.uploadedPath?.split("/").pop()}`,
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
  const [messages, setMessages] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const toastCtx = useToast();
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [showResumeModal, setShowResumeModal] = useState(false);
  const [starred, setStarred] = useState(false);
  const [starLoading, setStarLoading] = useState(false);
  const [muted, setMuted] = useState(() => isAgentMuted(id));
  const [streamingContent, setStreamingContent] = useState(null);
  const [activeTool, setActiveTool] = useState(null);
  const [toolStartTime, setToolStartTime] = useState(null);
  const streamTimeoutRef = useRef(null);
  const generationIdRef = useRef(null); // tracks current backend generation_id
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const nameInputRef = useRef(null);
  const [fileModal, setFileModal] = useState(null); // "CLAUDE.md" | "PROGRESS.md" | null
  const [showBrowser, setShowBrowser] = useState(false);
  const [fileExists, setFileExists] = useState({ "CLAUDE.md": null, "PROGRESS.md": null });
  const [headerExpanded, setHeaderExpanded] = useState(false);
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

  // Keep messagesRef in sync
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  // Initial load: fetch agent + latest 50 messages
  const loadData = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const [agentData, msgData] = await Promise.all([
        fetchAgent(id),
        fetchMessages(id),
      ]);
      if (controller.signal.aborted) return;
      if (!agentData || !agentData.id) return;
      setAgent(agentData);
      const msgs = Array.isArray(msgData?.messages) ? msgData.messages : [];
      setMessages(msgs);
      setHasMore(!!msgData?.has_more);
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
      const oldest = current[0];
      const data = await fetchMessages(id, { before: oldest.created_at });
      const older = Array.isArray(data?.messages) ? data.messages : [];
      if (older.length) {
        // Capture scroll height before DOM update for scroll preservation
        const el = scrollContainerRef.current;
        if (el) savedScrollHeight.current = el.scrollHeight;
        setMessages((prev) => [...older, ...prev]);
      }
      setHasMore(!!data?.has_more);
    } catch (err) {
      console.warn("Failed to load older messages:", err);
    } finally {
      setLoadingMore(false);
    }
  }, [id, loadingMore]);

  // Incremental refresh: fetch messages newer than the latest, and optionally
  // merge the tail to catch in-place content updates from sync agents (sync loop
  // grows the last message without changing created_at, so after= misses it).
  const refreshMessages = useCallback(async ({ syncHint = false } = {}) => {
    try {
      const agentData = await fetchAgent(id);
      if (!agentData || !agentData.id) return;
      setAgent(agentData);
      const current = messagesRef.current;
      if (!current.length) {
        // No messages yet — do a full fetch so the first message appears
        const data = await fetchMessages(id, { limit: 50 });
        const msgs = Array.isArray(data?.messages) ? data.messages : [];
        if (msgs.length) setMessages(msgs);
        return;
      }
      const newest = current[current.length - 1];
      const hasPending = current.some((m) => m.role === "USER" && m.status === "PENDING");
      const needTail = syncHint || agentData.status === "SYNCING" || hasPending;
      const fetches = [fetchMessages(id, { after: newest.created_at })];
      if (needTail) fetches.push(fetchMessages(id, { limit: 5 }));
      const [afterData, tailData] = await Promise.all(fetches);
      const newer = Array.isArray(afterData?.messages) ? afterData.messages : [];
      const tail = tailData ? (Array.isArray(tailData?.messages) ? tailData.messages : []) : [];

      setMessages((prev) => {
        let result = prev;
        // Merge tail: replace existing messages whose content grew in-place
        if (tail.length) {
          const tailById = new Map(tail.map((m) => [m.id, m]));
          let anyChanged = false;
          const merged = result.map((m) => {
            const fresh = tailById.get(m.id);
            if (fresh && (fresh.content !== m.content || fresh.completed_at !== m.completed_at || fresh.status !== m.status || JSON.stringify(fresh.metadata) !== JSON.stringify(m.metadata))) {
              anyChanged = true;
              return fresh;
            }
            return m;
          });
          if (anyChanged) result = merged;
        }
        // Append truly new messages from either source (dedup across newer+tail)
        const seenIds = new Set(result.map((m) => m.id));
        const unique = [...newer, ...tail].filter((m) => {
          if (seenIds.has(m.id)) return false;
          seenIds.add(m.id);
          return true;
        });
        if (unique.length) return [...result, ...unique];
        return result !== prev ? result : prev;
      });
    } catch {
      // Transient errors during polling — silently ignore
    }
  }, [id]);

  // Initial load + clear notification flag for this agent
  useEffect(() => {
    clearAgentNotified(id);
    registerViewing(id);
    initialLoadDone.current = false;
    setLoading(true);
    loadData();
    return () => { abortRef.current?.abort(); unregisterViewing(id); };
  }, [loadData, id]);

  // Polling — faster when executing, pauses when page hidden
  useEffect(() => {
    if (!visible) return;
    const isActive = agent?.status === "EXECUTING" || agent?.status === "SYNCING";
    const interval = isActive ? POLL_ACTIVE_INTERVAL : POLL_IDLE_INTERVAL;
    const timer = setInterval(refreshMessages, interval);
    return () => clearInterval(timer);
  }, [refreshMessages, agent?.status, visible]);

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
    // Scroll-up trigger for lazy loading
    if (el.scrollTop < 200 && hasMore && !loadingMore) {
      loadOlderMessages();
    }
    clearTimeout(scrollSaveTimer.current);
    scrollSaveTimer.current = setTimeout(() => {
      try { sessionStorage.setItem(scrollKey, String(el.scrollTop)); } catch { /* ignore */ }
    }, SCROLL_SAVE_DEBOUNCE);
  }, [scrollKey, hasMore, loadingMore, loadOlderMessages]);

  // Save scroll position on unmount
  useEffect(() => {
    return () => {
      clearTimeout(scrollSaveTimer.current);
      const el = scrollContainerRef.current;
      if (el) {
        try { sessionStorage.setItem(scrollKey, String(el.scrollTop)); } catch { /* ignore */ }
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
        const savedCount = sessionStorage.getItem(scrollCountKey);
        const savedPos = sessionStorage.getItem(scrollKey);
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
        sessionStorage.removeItem(scrollKey);
        sessionStorage.setItem(scrollCountKey, String(messages.length));
      } catch { /* ignore */ }
    }
    if (!userScrolledUp.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [loading, messages, streamingContent, scrollKey, scrollCountKey]);

  // Keep saved message count in sync for future visits
  useEffect(() => {
    if (!loading && messages.length > 0) {
      try { sessionStorage.setItem(scrollCountKey, String(messages.length)); } catch { /* ignore */ }
    }
  }, [loading, messages.length, scrollCountKey]);

  // WebSocket: re-fetch on new_message events, handle streaming
  const { lastEvent, sendWsMessage } = useWebSocket();

  // Notify backend which agent we're viewing (suppresses notifications)
  useEffect(() => {
    sendWsMessage({ type: "viewing", agent_id: id });
    return () => sendWsMessage({ type: "viewing", agent_id: null, _unview: id });
  }, [id, sendWsMessage]);
  useEffect(() => {
    if (!lastEvent) return;

    if (lastEvent.type === "agent_stream" && lastEvent.data?.agent_id === id) {
      const gid = lastEvent.data.generation_id;
      // Reject stream chunks from a stale generation
      if (gid != null && generationIdRef.current != null && gid < generationIdRef.current) return;
      // Track the current generation
      if (gid != null) generationIdRef.current = gid;
      setStreamingContent(lastEvent.data.content);
      // Safety fallback: auto-clear streaming content after inactivity in
      // case agent_stream_end is never received (e.g., WS disconnect).
      clearTimeout(streamTimeoutRef.current);
      streamTimeoutRef.current = setTimeout(() => {
        setStreamingContent(null);
      }, STREAM_TIMEOUT);
      return;
    }

    if (lastEvent.type === "agent_stream_end" && lastEvent.data?.agent_id === id) {
      const gid = lastEvent.data.generation_id;
      // Ignore end for a stale generation
      if (gid != null && generationIdRef.current != null && gid < generationIdRef.current) return;
      clearTimeout(streamTimeoutRef.current);
      setStreamingContent(null);
      return;
    }

    // Hook-driven tool activity (PreToolUse/PostToolUse HTTP hooks)
    // Much more reliable than JSONL polling — fires synchronously with
    // each tool call regardless of file growth or idle thresholds.
    if (lastEvent.type === "tool_activity" && lastEvent.data?.agent_id === id) {
      if (lastEvent.data.phase === "start") {
        setActiveTool({ name: lastEvent.data.tool_name, summary: lastEvent.data.summary || "" });
        setToolStartTime(Date.now());
      } else {
        setActiveTool(null);
        setToolStartTime(null);
      }
      return;
    }

    if (lastEvent.type === "new_message" && lastEvent.data?.agent_id === id) {
      clearTimeout(streamTimeoutRef.current);
      setStreamingContent(null);
      setActiveTool(null);
      setToolStartTime(null);
      refreshMessages({ syncHint: lastEvent.data?.message_id === "sync" });
      return;
    }

    if (lastEvent.type === "message_update" && lastEvent.data?.agent_id === id) {
      const { message_id, status, error_message } = lastEvent.data;
      if (status === "CANCELLED") {
        setMessages((prev) => prev.filter((m) => m.id !== message_id));
      } else {
        setMessages((prev) =>
          prev.map((m) => (m.id === message_id
            ? { ...m, status, ...(error_message ? { error_message } : {}) }
            : m))
        );
      }
      return;
    }

    if (lastEvent.type === "agent_update" && lastEvent.data?.agent_id === id) {
      const status = lastEvent.data.status;
      if (status !== "EXECUTING" && status !== "SYNCING") {
        // Agent no longer active — clear all streaming & tool state
        clearTimeout(streamTimeoutRef.current);
        setStreamingContent(null);
        setActiveTool(null);
        setToolStartTime(null);
        generationIdRef.current = null;
      }
      refreshMessages();
    }

  }, [lastEvent, id, refreshMessages]);

  // Cleanup
  useEffect(() => {
    return () => {
      clearTimeout(streamTimeoutRef.current);
    };
  }, []);

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

  // Send message (auto-queues if agent is busy)
  const handleSend = async (content) => {
    try {
      const busy = agent.status === "EXECUTING" || (agent.status === "SYNCING" && !agent.tmux_pane);
      await sendMessage(id, content, busy ? { queue: true } : {});
      if (busy) showToast("Queued — will send when ready");
      loadData();
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    }
  };

  // Send later (queued with scheduled_at)
  const handleSendLater = async (content, scheduledAt) => {
    try {
      await sendMessage(id, content, { queue: true, scheduled_at: scheduledAt });
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
      await stopAgent(id);
      showToast("Agent stopped");
      loadData();
      window.dispatchEvent(new CustomEvent("agents-data-changed"));
    } catch (err) {
      showToast("Failed: " + err.message, "error");
    } finally {
      setStopping(false);
      setShowStopConfirm(false);
    }
  };

  // Resume agent
  const handleResume = async (mode = null) => {
    // For cli_sync agents without a successor, show the resume modal
    if (!mode && agent?.cli_sync && !agent?.successor_id) {
      setShowResumeModal(true);
      return;
    }
    setResuming(true);
    setShowResumeModal(false);
    try {
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

  // Check if any interactive cards are waiting for an answer
  // (must be before early returns to maintain hooks ordering)
  const hasPendingInteractive = useMemo(() => {
    for (const msg of messages) {
      const meta = msg.metadata;
      if (!meta?.interactive) continue;
      for (const item of meta.interactive) {
        const questions = item.questions || [];
        if (questions.length > 1) {
          // Multi-question: pending if any question lacks a selection
          const indices = item.selected_indices || {};
          for (let qi = 0; qi < questions.length; qi++) {
            if (indices[String(qi)] == null && item.answer == null) return true;
          }
        } else {
          // Single-question: existing backward-compat logic
          if (item.answer == null && item.selected_index == null) return true;
        }
      }
    }
    return false;
  }, [messages]);

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

  const statusDot = AGENT_STATUS_COLORS[agent.status] || "bg-gray-500";
  const statusText = AGENT_STATUS_TEXT_COLORS[agent.status] || "text-dim";
  const isExecuting = agent.status === "EXECUTING";
  const isSyncing = agent.status === "SYNCING";
  const hasTmux = isSyncing && !!agent.tmux_pane;
  const hasTmuxPane = !!agent.tmux_pane;
  const isStopped = agent.status === "STOPPED";
  const isError = agent.status === "ERROR";
  const compactHeader = embedded && !headerExpanded;

  let disabledReason = "";
  if (isStopped) disabledReason = "Agent is stopped — click Resume to restart";
  else if (isError) disabledReason = "Agent errored — click Resume to restart";
  else if (hasPendingInteractive) disabledReason = "Answer the question above first";

  return (
    <div className="flex flex-col h-full relative">

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
                {["CLAUDE.md", "PROGRESS.md"].map((fn) => {
                  const letter = fn === "CLAUDE.md" ? "C" : "P";
                  const exists = fileExists[fn];
                  const color = exists === false ? "text-zinc-500 hover:text-zinc-400" : "text-cyan-400 hover:text-cyan-300";
                  return (
                    <button
                      key={fn}
                      type="button"
                      onClick={() => setFileModal(fn)}
                      title={fn}
                      className={`shrink-0 w-7 h-7 flex items-center justify-center rounded-lg hover:bg-input transition-colors ${color}`}
                    >
                      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M14 2v6h6" />
                        <text x="12" y="17" textAnchor="middle" fill="currentColor" stroke="none" fontSize="7" fontWeight="700" fontFamily="system-ui">{letter}</text>
                      </svg>
                    </button>
                  );
                })}
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

          {/* Task banner */}
          {!compactHeader && agent.task_id && (
            <div className="ml-9 flex items-center gap-1.5">
              <svg className="w-3.5 h-3.5 text-amber-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
              </svg>
              <button
                type="button"
                onClick={() => navigate(`/tasks/${agent.task_id}`)}
                className="text-xs text-amber-400 hover:text-amber-300 underline truncate"
              >
                Working on task
              </button>
            </div>
          )}

          {/* Row 2: Status + model + branch | action buttons (ml-9 aligns with name after back btn) */}
          {!compactHeader && <div className="flex items-center gap-2 ml-9">
            <div className="flex items-center gap-1.5 min-w-0 flex-1">
              <span className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${statusDot}`} />
              <span className={`text-xs shrink-0 ${statusText}`}>
                {agent.status.toLowerCase().replace("_", " ")}
                {activeTool && (isExecuting || isSyncing) && (
                  <span className="text-faint">: <span className="font-mono">{activeTool.name}</span></span>
                )}
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
        <div className={`${embedded ? "" : "max-w-2xl"} mx-auto flex items-center gap-2`}>
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
        onScroll={handleScroll}
        className={`flex-1 overflow-y-auto overflow-x-hidden px-4 py-3 pb-36 ${embedded ? "" : "max-w-2xl"} mx-auto w-full flex flex-col`}
      >
        <div className="mt-auto" />
        {messages.length === 0 && agent.status === "STARTING" ? (
          <InitializingIndicator />
        ) : (
          <>
            {/* Lazy-load indicator at top */}
            {loadingMore && (
              <div className="text-center py-3 text-xs opacity-60">Loading older messages...</div>
            )}
            {!hasMore && messages.length > 0 && (
              <div className="text-center py-3 text-xs opacity-40">Beginning of conversation</div>
            )}

            {messages.filter((m) => !(m.role === "USER" && m.status === "PENDING")).map((msg) => (
              <ChatBubble key={msg.id} message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={refreshMessages} />
            ))}

            {/* Streaming output or typing indicator while executing/syncing */}
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
                if (!isDuplicate) return <StreamingBubble content={streamingContent} project={agent.project} activeTool={activeTool} />;
              }
              return (isExecuting || agent?.is_generating) ? <TypingIndicator activeTool={activeTool} toolStartTime={toolStartTime} /> : null;
            })()}

            {/* Pending/scheduled messages always at the bottom */}
            {(() => {
              const pending = messages.filter((m) => m.role === "USER" && m.status === "PENDING");
              const queued = pending.filter((m) => !m.scheduled_at);
              const scheduled = pending.filter((m) => m.scheduled_at);
              return (
                <>
                  {queued.map((msg, idx) => (
                    <ChatBubble key={msg.id} message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={refreshMessages} queuePosition={idx + 1} queueTotal={queued.length} />
                  ))}
                  {scheduled.map((msg) => (
                    <ChatBubble key={msg.id} message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={refreshMessages} />
                  ))}
                </>
              );
            })()}
          </>
        )}


        <div ref={messagesEndRef} />
      </div>

      {/* Input bar */}
      <ChatInput
        agentId={id}
        onSend={handleSend}
        onSendLater={handleSendLater}
        disabled={isStopped || isError || hasPendingInteractive}
        disabledReason={disabledReason}
        isBusy={isExecuting || (isSyncing && !hasTmux)}
        tmuxMode={hasTmux}
        onEscape={(agent.cli_sync || hasTmuxPane) ? async () => {
          try { await escapeAgent(id); loadData(); } catch (e) { showToast(e.message || "Escape failed", "error"); }
        } : null}
        escapeDisabled={isStopped || isError}
        escapeUrgent={isExecuting || hasPendingInteractive || agent.is_generating || (hasTmux && (streamingContent || (messages.length > 0 && messages[messages.length - 1].role === "USER")))}
        escapeAvailable={hasTmuxPane}
      />

      {/* Stop confirmation modal */}
      {showStopConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-surface rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-card">
            <h3 className="text-lg font-bold text-heading">Stop Agent?</h3>
            <p className="text-sm text-label">
              This will stop the agent. You won't be able to send more messages.
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

      {showResumeModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-surface rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-card">
            <h3 className="text-lg font-bold text-heading">Resume Agent</h3>
            <p className="text-sm text-label">
              This was a tmux CLI session. How would you like to resume it?
            </p>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                disabled={resuming}
                onClick={() => handleResume("tmux")}
                className="min-h-[44px] rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-semibold text-sm transition-colors disabled:opacity-50"
              >
                {resuming ? "Resuming..." : "Resume in new tmux session"}
              </button>
              <button
                type="button"
                disabled={resuming}
                onClick={() => handleResume("normal")}
                className="min-h-[44px] rounded-lg bg-input hover:bg-elevated text-body font-semibold text-sm transition-colors disabled:opacity-50"
              >
                Resume as normal agent
              </button>
              <button
                type="button"
                onClick={() => setShowResumeModal(false)}
                className="min-h-[44px] rounded-lg text-dim text-sm transition-colors hover:text-body"
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
          onClose={() => setShowBrowser(false)}
        />
      )}
    </div>
  );
}
