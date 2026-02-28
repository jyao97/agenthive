import { useState, useEffect, useCallback, useRef, useMemo, Component } from "react";
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
} from "../lib/api";
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
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS, modelDisplayName } from "../lib/constants";
import VoiceRecorder from "../components/VoiceRecorder";
import WaveformVisualizer from "../components/WaveformVisualizer";
import useDraft from "../hooks/useDraft";
import useVoiceRecorder from "../hooks/useVoiceRecorder";
import useWebSocket, { isAgentMuted, setAgentMuted, clearAgentNotified } from "../hooks/useWebSocket";
import useHealthStatus from "../hooks/useHealthStatus";

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

// --- Interactive: AskUserQuestion ---

function QuestionBubble({ item, agentId, onAnswered }) {
  // Track locally chosen index so the selection sticks immediately
  // (before the DB roundtrip updates item.answer).
  const [chosenIdx, setChosenIdx] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const questions = item.questions || [];
  // Detect dismissed/escaped answers (not a real selection)
  const isDismissed = item.answer != null && typeof item.answer === "string" &&
    (item.answer.startsWith("The user doesn't want to proceed") || item.answer.startsWith("User declined"));

  const handleSubmit = async (idx) => {
    setChosenIdx(idx);
    setSubmitting(true);
    try {
      await answerAgent(agentId, {
        tool_use_id: item.tool_use_id,
        type: "ask_user_question",
        selected_index: idx,
      });
      onAnswered?.();
    } catch (e) {
      console.error("Failed to answer:", e);
      setChosenIdx(null); // revert on failure
    } finally {
      setSubmitting(false);
    }
  };

  // Resolve which option index was selected (per-question, but shared for single-Q items)
  const resolveIdx = (q) => {
    // 1. Best: backend stored the numeric index directly
    if (item.selected_index != null) return item.selected_index;
    // 2. Parse the answer string for ="label" pattern
    if (item.answer != null && typeof item.answer === "string" && !isDismissed) {
      const allMatches = [...item.answer.matchAll(/="([^"]+)"/g)];
      for (let i = allMatches.length - 1; i >= 0; i--) {
        const idx = (q.options || []).findIndex((o) => o.label === allMatches[i][1]);
        if (idx !== -1) return idx;
      }
    }
    // 3. Local optimistic choice
    if (chosenIdx != null) return chosenIdx;
    return null;
  };

  return (
    <div className="mt-3 space-y-3">
      {questions.map((q, qi) => {
        const answeredIdx = resolveIdx(q);
        const isAnswered = answeredIdx != null || isDismissed;

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
          <div key={qi} className="rounded-xl bg-indigo-500/10 border border-indigo-500/20 p-3">
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
            <div className="space-y-1.5 max-h-80 overflow-y-auto">
              {(q.options || []).map((opt, oi) => {
                const isChosen = answeredIdx === oi;
                const dimmed = isAnswered && !isChosen;

                return (
                  <button
                    key={oi}
                    type="button"
                    disabled={isAnswered || submitting}
                    onClick={() => {
                      if (!isAnswered) handleSubmit(oi);
                    }}
                    className={`w-full text-left rounded-lg px-3 py-2 text-sm transition-all border ${
                      isChosen
                        ? "bg-cyan-500/20 border-cyan-500/40 text-heading"
                        : dimmed
                          ? "bg-surface/30 border-divider/30 text-dim/50"
                          : "bg-surface/50 border-divider hover:bg-hover hover:border-heading/20 text-body"
                    } ${isAnswered ? "cursor-default" : "cursor-pointer"}`}
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
            {submitting && (
              <p className="text-xs text-dim mt-2 flex items-center gap-1.5">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
                Sending answer...
              </p>
            )}
          </div>
        );
      })}
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
  if (answer.startsWith("The user doesn't want to proceed") || answer.startsWith("User declined")) return null;
  const a = answer.toLowerCase().trim();
  if (/clear context/.test(a)) return 0;
  if (/bypass/.test(a) && !/clear/.test(a) && !/manual/.test(a)) return 1;
  if (/manual/.test(a)) return 2;
  if (/feedback|type here|tell claude/.test(a)) return 3;
  if (/^yes\b/.test(a) || a === "approve" || a === "approved") return 0;
  if (/^no\b/.test(a) || a === "reject") return 2;
  return 0; // default to first option
}

function _isPlanDismissed(item) {
  if (!item.answer || typeof item.answer !== "string") return false;
  return item.answer.startsWith("The user doesn't want to proceed") || item.answer.startsWith("User declined");
}

function PlanBubble({ item, agentId, onAnswered }) {
  const [chosenIdx, setChosenIdx] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const isDismissed = _isPlanDismissed(item);
  // Determine the effective selected index: stored index > answer parse > local choice
  const serverIdx = item.selected_index ?? (item.answer != null ? _detectPlanIdx(item.answer) : null);
  const effectiveIdx = serverIdx ?? chosenIdx;
  const isAnswered = effectiveIdx != null || isDismissed;

  const handleSelect = async (idx) => {
    setChosenIdx(idx);
    setSubmitting(true);
    try {
      await answerAgent(agentId, {
        tool_use_id: item.tool_use_id,
        type: "exit_plan_mode",
        selected_index: idx,
      });
      onAnswered?.();
    } catch (e) {
      console.error("Failed to answer plan:", e);
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
        {badgeText && (
          <span className={`ml-auto px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider ${badgeClass}`}>
            {badgeText}
          </span>
        )}
      </div>
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
    </div>
  );
}

// --- Interactive items renderer ---

function InteractiveBubbles({ metadata, agentId, onAnswered }) {
  if (!metadata?.interactive?.length) return null;
  return metadata.interactive.map((item) => {
    if (item.type === "ask_user_question") {
      return <QuestionBubble key={item.tool_use_id} item={item} agentId={agentId} onAnswered={onAnswered} />;
    }
    if (item.type === "exit_plan_mode") {
      return <PlanBubble key={item.tool_use_id} item={item} agentId={agentId} onAnswered={onAnswered} />;
    }
    return null;
  });
}

function ChatBubble({ message, project, onCancelMessage, onUpdateMessage, onSendNow, agentId, onRefresh }) {
  if (message.role === "SYSTEM") {
    return <SystemBubble message={message} />;
  }

  const isUser = message.role === "USER";
  const isScheduled = isUser && message.scheduled_at && message.status === "PENDING";
  const isPending = isUser && message.status === "PENDING" && !message.scheduled_at;

  const [showActions, setShowActions] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(message.content);
  const [editSchedule, setEditSchedule] = useState("");
  const longPressTimer = useRef(null);
  const editTextareaRef = useRef(null);

  // Initialize editSchedule from message when entering edit mode
  useEffect(() => {
    if (editing && message.scheduled_at) {
      const d = new Date(message.scheduled_at);
      const local = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
      setEditSchedule(local);
    }
  }, [editing, message.scheduled_at]);

  // Auto-focus textarea when editing starts
  useEffect(() => {
    if (editing) {
      setTimeout(() => editTextareaRef.current?.focus(), 0);
    }
  }, [editing]);

  const canModify = isScheduled || isPending;

  const handleLongPressStart = () => {
    if (!canModify) return;
    longPressTimer.current = setTimeout(() => {
      setShowActions(true);
    }, 500);
  };
  const handleLongPressEnd = () => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  };
  const handleDoubleClick = () => {
    if (!canModify) return;
    setShowActions(true);
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
    ? new Date(message.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  // Editing UI for scheduled/pending messages
  const editDateRef = useRef(null);

  if (editing) {
    const scheduleLabel = editSchedule
      ? new Date(editSchedule).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
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
            <div className="text-sm">
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
              <span className="text-cyan-300/70">queued</span>
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
              <span className="text-red-400">Failed</span>
            )}
            {message.status === "TIMEOUT" && (
              <span className="text-orange-400">Timed out</span>
            )}
          </div>
        </div>
        {attachments.length > 0 && <FileAttachments attachments={attachments} />}
        {!isUser && message.metadata?.interactive?.length > 0 && (
          <InteractiveBubbles metadata={message.metadata} agentId={agentId} onAnswered={onRefresh} />
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

function TypingIndicator() {
  return (
    <div className="flex justify-start my-2">
      <div className="bg-surface shadow-card rounded-2xl rounded-bl-md px-5 py-3.5 flex items-center gap-[5px]">
        <span className="typing-dot" style={{ animationDelay: "0ms" }} />
        <span className="typing-dot" style={{ animationDelay: "200ms" }} />
        <span className="typing-dot" style={{ animationDelay: "400ms" }} />
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

function StreamingBubble({ content, project }) {
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
            <span className="text-xs text-dim">Streaming...</span>
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
  const [attachments, setAttachments] = useState([]);
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
      const t = setTimeout(() => setVoiceError(null), 4000);
      return () => clearTimeout(t);
    }
  }, [voiceError]);

  useEffect(() => {
    if (uploadError) {
      const t = setTimeout(() => setUploadError(null), 4000);
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

  // Cleanup blob URLs on unmount
  useEffect(() => {
    return () => {
      attachments.forEach((a) => { if (a.previewUrl) URL.revokeObjectURL(a.previewUrl); });
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const buildMessageText = useCallback((baseText, atts) => {
    let msg = baseText;
    for (const a of atts) {
      if (a.uploadedPath) msg += `\n[Attached file: ${a.uploadedPath}]`;
    }
    return msg;
  }, []);

  const clearAttachments = useCallback(() => {
    setAttachments((prev) => {
      prev.forEach((a) => { if (a.previewUrl) URL.revokeObjectURL(a.previewUrl); });
      return [];
    });
  }, []);

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
        originalName: file.name, size: file.size,
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
      if (att?.previewUrl) URL.revokeObjectURL(att.previewUrl);
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
    } catch (e) {
      console.error("Escape failed:", e);
    }
    // 2.5s cooldown on the frontend to match backend rate limit
    setTimeout(() => setEscCooldown(false), 2500);
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
            {attachments.map((att) => (
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
          <div className="flex-1 min-w-0">
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
  const [showResumeModal, setShowResumeModal] = useState(false);
  const [starred, setStarred] = useState(false);
  const [starLoading, setStarLoading] = useState(false);
  const [muted, setMuted] = useState(() => isAgentMuted(id));
  const [streamingContent, setStreamingContent] = useState(null);
  const streamTimeoutRef = useRef(null);
  const streamLockedRef = useRef(false); // prevents late agent_stream after new_message
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const nameInputRef = useRef(null);
  const messagesEndRef = useRef(null);
  const toastTimer = useRef(null);
  const health = useHealthStatus();

  const showToast = useCallback((message, type = "success") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  // Load agent + messages with AbortController support.
  // On initial load, errors propagate to console so failures are visible.
  // On subsequent poll refreshes, errors are silenced (transient network issues).
  const initialLoadDone = useRef(false);
  const abortRef = useRef(null);
  const loadData = useCallback(async () => {
    // Abort any in-flight request from a previous call
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
      setMessages(Array.isArray(msgData) ? msgData : []);
      if (!initialLoadDone.current && agentData.muted != null) {
        setMuted(agentData.muted);
        setAgentMuted(id, agentData.muted);
      }
      initialLoadDone.current = true;
    } catch (err) {
      if (controller.signal.aborted) return;
      if (!initialLoadDone.current) {
        console.error("AgentChatPage: initial load failed", err);
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [id]);

  // Initial load + clear notification flag for this agent
  useEffect(() => {
    clearAgentNotified(id);
    initialLoadDone.current = false;
    setLoading(true);
    loadData();
    return () => abortRef.current?.abort();
  }, [loadData, id]);

  // Polling — faster when executing
  useEffect(() => {
    const isActive = agent?.status === "EXECUTING" || agent?.status === "SYNCING";
    const interval = isActive ? 3000 : 10000;
    const timer = setInterval(loadData, interval);
    return () => clearInterval(timer);
  }, [loadData, agent?.status]);

  // Mark as read on mount and when new messages arrive
  useEffect(() => {
    if (agent && agent.unread_count > 0) {
      markAgentRead(id).catch(() => {});
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
      .catch(() => {});
  }, [agent?.project, agent?.session_id, agent?.id]);

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
      setStarred(!starred);
    } catch (err) {
      showToast("Failed to update star: " + err.message, "error");
    } finally {
      setStarLoading(false);
    }
  };

  const handleToggleMute = async () => {
    const nextMuted = !muted;
    setAgentMuted(id, nextMuted);
    setMuted(nextMuted);
    try {
      await updateAgent(id, { muted: nextMuted });
    } catch {
      // Backend update failed — local state still applies for browser notifs
    }
    showToast(nextMuted ? "Notifications muted for this agent" : "Notifications enabled for this agent");
  };

  // Auto-scroll to bottom on new messages or streaming content
  const scrollContainerRef = useRef(null);
  const userScrolledUp = useRef(false);
  const scrollSaveTimer = useRef(null);
  const prevMsgCount = useRef(null);
  const scrollKey = `scroll:chat:${id}`;
  const scrollCountKey = `scroll:chat:${id}:count`;

  // Detect if user has scrolled up (to avoid forcing scroll during streaming)
  // and persist scroll position (debounced) for restore on navigate-back
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    userScrolledUp.current = distFromBottom > 100;
    clearTimeout(scrollSaveTimer.current);
    scrollSaveTimer.current = setTimeout(() => {
      try { sessionStorage.setItem(scrollKey, String(el.scrollTop)); } catch { /* ignore */ }
    }, 200);
  }, [scrollKey]);

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

  useEffect(() => {
    if (loading) return;
    const isFirstLoad = prevMsgCount.current === null;
    const msgCountChanged = prevMsgCount.current !== null && prevMsgCount.current !== messages.length;
    prevMsgCount.current = messages.length;

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

    // New messages arrived after initial load — clear saved position, auto-scroll
    if (msgCountChanged) {
      try {
        sessionStorage.removeItem(scrollKey);
        sessionStorage.setItem(scrollCountKey, String(messages.length));
      } catch { /* ignore */ }
    }
    if (!userScrolledUp.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [loading, messages.length, streamingContent, scrollKey, scrollCountKey]);

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
    return () => sendWsMessage({ type: "viewing", agent_id: null });
  }, [id, sendWsMessage]);
  useEffect(() => {
    if (!lastEvent) return;
    const syncing = agent?.status === "SYNCING";

    if (lastEvent.type === "agent_stream" && lastEvent.data?.agent_id === id) {
      // Reject late agent_stream that arrives after new_message already cleared streaming
      if (streamLockedRef.current) return;
      setStreamingContent(lastEvent.data.content);
      // Safety fallback: auto-clear after inactivity in case agent_stream_end
      // is never received (e.g., WS disconnect).  Generous timeout since the
      // deterministic agent_stream_end is the primary signal.
      clearTimeout(streamTimeoutRef.current);
      streamTimeoutRef.current = setTimeout(() => {
        setStreamingContent(null);
      }, syncing ? 8000 : 6000);
      return;
    }

    if (lastEvent.type === "agent_stream_end" && lastEvent.data?.agent_id === id) {
      // Deterministic signal from backend that generation finished.
      // Lock streaming briefly, then auto-unlock for next message.
      streamLockedRef.current = true;
      clearTimeout(streamTimeoutRef.current);
      streamTimeoutRef.current = setTimeout(() => {
        setStreamingContent(null);
        streamLockedRef.current = false;
      }, 300);
      return;
    }

    if (lastEvent.type === "new_message" && lastEvent.data?.agent_id === id) {
      // New message arrived — clear streaming, briefly lock to reject late agent_stream.
      // Auto-unlock after 1.5s so the NEXT message's streams can come through
      // (SYNCING agents don't emit agent_update between messages to unlock).
      streamLockedRef.current = true;
      clearTimeout(streamTimeoutRef.current);
      streamTimeoutRef.current = setTimeout(() => { streamLockedRef.current = false; }, 1500);
      setStreamingContent(null);
      loadData();
      return;
    }

    if (lastEvent.type === "agent_update" && lastEvent.data?.agent_id === id) {
      const status = lastEvent.data.status;
      if (status === "EXECUTING" || status === "SYNCING") {
        // New execution starting — unlock streaming for fresh events
        streamLockedRef.current = false;
      } else {
        // Agent no longer executing — lock and clear streaming
        streamLockedRef.current = true;
        clearTimeout(streamTimeoutRef.current);
        setStreamingContent(null);
      }
      loadData();
    }
  }, [lastEvent, id, loadData, agent?.status]);

  // Cleanup
  useEffect(() => {
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
      clearTimeout(streamTimeoutRef.current);
    };
  }, []);

  // Rename agent
  const startRename = () => {
    setNameDraft(agent?.name || "");
    setEditingName(true);
    setTimeout(() => nameInputRef.current?.select(), 0);
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
      const timeStr = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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
        // No answer at all and no local selection → still pending
        if (item.answer == null && item.selected_index == null) return true;
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
        <button type="button" onClick={() => navigate("/agents")} className="mt-2 text-sm text-cyan-400 underline">
          Back to Agents
        </button>
      </div>
    );
  }

  const isHealthy = health && health.status === "ok" && health.db === "ok" && health.claude_cli === "ok";
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

  let disabledReason = "";
  if (isStopped) disabledReason = "Agent is stopped — click Resume to restart";
  else if (isError) disabledReason = "Agent errored — click Resume to restart";
  else if (hasPendingInteractive) disabledReason = "Answer the question above first";

  return (
    <div className="flex flex-col h-full relative">
      {/* Toast */}
      {toast && (
        <div className={`fixed left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-lg shadow-lg text-sm font-medium safe-area-toast ${toast.type === "error" ? "bg-red-600 text-white" : "bg-cyan-600 text-white"}`}>
          {toast.message}
        </div>
      )}

      {/* Header */}
      <div className="shrink-0 bg-surface border-b border-divider px-4 py-2 safe-area-pt z-10">
        <div className="max-w-2xl mx-auto space-y-1.5">
          {/* Row 1: Back + name | project + icon buttons */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => navigate("/agents")}
              className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
            >
              <svg className="w-5 h-5 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
              </svg>
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

            <span className="shrink-0 text-xs text-dim">{agent.project}</span>

            {/* Icon buttons */}
            <div className="shrink-0 flex items-center">
              <button
                type="button"
                onClick={handleToggleMute}
                title={muted ? "Unmute notifications" : "Mute notifications"}
                className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
              >
                {muted ? (
                  <svg className="w-3.5 h-3.5 text-dim hover:text-cyan-400 transition-colors" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9.143 17.082a24.248 24.248 0 003.718.918m-3.718-.918A23.848 23.848 0 013.69 15.772 8.966 8.966 0 016 9.75V9a6 6 0 0112 0v.75m-9.857 7.332a3 3 0 005.714 0M3 3l18 18" />
                  </svg>
                ) : (
                  <svg className="w-3.5 h-3.5 text-cyan-400" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M12 22c1.1 0 2-.9 2-2h-4a2 2 0 002 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
                  </svg>
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
            </div>
          </div>

          {/* Row 2: Status + model + branch | action buttons (ml-9 aligns with name after back btn) */}
          <div className="flex items-center gap-2 ml-9">
            <div className="flex items-center gap-1.5 min-w-0 flex-1">
              <span className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${statusDot}`} />
              <span className={`text-xs shrink-0 ${statusText}`}>{agent.status.toLowerCase().replace("_", " ")}</span>
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
                  onClick={() => navigate(`/agents/${agent.successor_id}`)}
                  className="px-2.5 h-7 flex items-center gap-1 rounded-lg text-xs font-medium bg-violet-600 hover:bg-violet-500 text-white transition-colors"
                >
                  Continued
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                  </svg>
                </button>
              )}
              {/* Resume — always visible, disabled when not stopped */}
              <button
                type="button"
                onClick={() => handleResume()}
                disabled={!(isStopped || isError) || resuming}
                className="px-2.5 h-7 flex items-center gap-1 rounded-lg text-xs font-medium bg-cyan-600 text-white transition-colors disabled:opacity-30 disabled:cursor-not-allowed enabled:hover:bg-cyan-500"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 3l14 9-14 9V3z" />
                </svg>
                {resuming ? "..." : "Resume"}
              </button>
              {/* Stop — always visible, disabled when already stopped */}
              <button
                type="button"
                onClick={() => setShowStopConfirm(true)}
                disabled={isStopped || isError}
                className="px-2.5 h-7 flex items-center gap-1 rounded-lg text-xs font-medium text-red-400 transition-colors disabled:opacity-30 disabled:cursor-not-allowed enabled:hover:bg-red-600/20"
              >
                <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
                Stop
              </button>

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
          </div>
        </div>
      </div>

      {/* Agent ID + session size + parent link */}
      <div className="shrink-0 bg-surface border-b border-divider px-4 py-1">
        <div className="max-w-2xl mx-auto flex items-center gap-2">
          {agent.parent_id && (
            <button
              type="button"
              onClick={() => navigate(`/agents/${agent.parent_id}`)}
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
                });
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
      </div>

      {/* Messages */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto overflow-x-hidden px-4 py-3 pb-36 max-w-2xl mx-auto w-full flex flex-col"
      >
        <div className="mt-auto" />
        {messages.length === 0 && agent.status === "STARTING" ? (
          <InitializingIndicator />
        ) : (
          <>
            {messages.filter((m) => !(m.role === "USER" && m.status === "PENDING")).map((msg) => (
              <ChatBubble key={msg.id} message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={loadData} />
            ))}

            {/* Streaming output or typing indicator while executing/syncing */}
            {streamingContent
              ? <StreamingBubble content={streamingContent} project={agent.project} />
              : (isExecuting && <TypingIndicator />)
            }

            {/* Pending/scheduled messages always at the bottom */}
            {messages.filter((m) => m.role === "USER" && m.status === "PENDING").map((msg) => (
              <ChatBubble key={msg.id} message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} agentId={id} onRefresh={loadData} />
            ))}
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
        escapeUrgent={isExecuting || hasPendingInteractive || (hasTmux && (streamingContent || (messages.length > 0 && messages[messages.length - 1].role === "USER")))}
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
    </div>
  );
}
