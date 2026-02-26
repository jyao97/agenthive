import { useState, useEffect, useCallback, useRef, useMemo } from "react";
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
} from "../lib/api";
import { relativeTime, renderMarkdown, extractFileAttachments } from "../lib/formatters";
import FileAttachments from "../components/FilePreview";
import { AGENT_STATUS_COLORS, AGENT_STATUS_TEXT_COLORS, modelDisplayName } from "../lib/constants";
import VoiceRecorder from "../components/VoiceRecorder";
import WaveformVisualizer from "../components/WaveformVisualizer";
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

function ChatBubble({ message, project, onCancelMessage, onUpdateMessage, onSendNow }) {
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
    () => (!isUser ? extractFileAttachments(message.content, project) : []),
    [isUser, message.content, project],
  );

  const scheduledTime = isScheduled
    ? new Date(message.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  // Editing UI for scheduled/pending messages
  if (editing) {
    return (
      <div className="flex justify-end my-2">
        <div className="max-w-[85%] w-full">
          <div className="rounded-2xl px-4 py-2.5 bg-amber-600/60 text-white rounded-br-md space-y-2">
            <textarea
              ref={editTextareaRef}
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              rows={2}
              className="w-full rounded-lg bg-black/20 border border-amber-400/40 px-2 py-1.5 text-sm text-white placeholder-amber-200/50 resize-none focus:border-amber-300 focus:outline-none"
            />
            <input
              type="datetime-local"
              value={editSchedule}
              onChange={(e) => setEditSchedule(e.target.value)}
              min={(() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`; })()}
              className="w-full rounded-lg bg-black/20 border border-amber-400/40 px-2 py-1.5 text-sm text-white focus:border-amber-300 focus:outline-none"
            />
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
            <p className="text-sm whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="text-sm">
              {renderMarkdown(message.content, project)}
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
            {renderMarkdown(content, project)}
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

function SendLaterPicker({ onSelect, onClose }) {
  const [customValue, setCustomValue] = useState("");
  const pickerRef = useRef(null);
  const inputRef = useRef(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target)) onClose();
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [onClose]);

  const presets = [
    { label: "30 minutes", minutes: 30 },
    { label: "1 hour", minutes: 60 },
    { label: "2 hours", minutes: 120 },
    { label: "4 hours", minutes: 240 },
  ];

  // "Tomorrow 9 AM" in local time
  const tomorrowMorning = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    return d;
  };

  const handlePreset = (minutes) => {
    const d = new Date(Date.now() + minutes * 60000);
    onSelect(d.toISOString());
  };

  const handleTomorrow = () => {
    onSelect(tomorrowMorning().toISOString());
  };

  const openNativePicker = () => {
    if (inputRef.current) {
      inputRef.current.showPicker?.();
      inputRef.current.focus();
    }
  };

  const handleDateChange = (e) => {
    const val = e.target.value;
    if (!val) return;
    setCustomValue(val);
    const d = new Date(val);
    if (!isNaN(d.getTime()) && d > new Date()) {
      onSelect(d.toISOString());
    }
  };

  const localMin = (() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`; })();

  const displayTime = customValue
    ? new Date(customValue).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : null;

  return (
    <div
      ref={pickerRef}
      className="absolute bottom-12 right-0 w-56 bg-surface border border-divider rounded-xl shadow-lg overflow-hidden z-50"
    >
      <div className="px-3 py-2 border-b border-divider">
        <span className="text-xs font-semibold text-heading">Send Later</span>
      </div>
      <div className="py-1">
        {presets.map((p) => (
          <button
            key={p.minutes}
            type="button"
            onClick={() => handlePreset(p.minutes)}
            className="w-full text-left px-3 py-2 text-sm text-body hover:bg-input transition-colors flex items-center gap-2"
          >
            <svg className="w-4 h-4 text-amber-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6l4 2m6-2a10 10 0 11-20 0 10 10 0 0120 0z" />
            </svg>
            {p.label}
          </button>
        ))}
        <button
          type="button"
          onClick={handleTomorrow}
          className="w-full text-left px-3 py-2 text-sm text-body hover:bg-input transition-colors flex items-center gap-2"
        >
          <svg className="w-4 h-4 text-orange-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
          </svg>
          Tomorrow 9 AM
        </button>
      </div>
      <div className="border-t border-divider px-3 py-2">
        {/* Hidden native picker — triggered by the button below */}
        <input
          ref={inputRef}
          type="datetime-local"
          value={customValue}
          onChange={handleDateChange}
          min={localMin}
          className="sr-only"
          tabIndex={-1}
        />
        <button
          type="button"
          onClick={openNativePicker}
          className="w-full rounded-lg bg-amber-500 hover:bg-amber-400 text-white text-sm py-1.5 font-medium transition-colors flex items-center justify-center gap-2"
        >
          <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
          {displayTime || "Pick a time"}
        </button>
      </div>
    </div>
  );
}

// --- Chat Input ---

function ChatInput({ agentId, onSend, onSendLater, disabled, disabledReason, isBusy, tmuxMode }) {
  const draftKey = agentId ? `agenthive-draft-${agentId}` : null;
  const [text, _setText] = useState(() => {
    if (draftKey) {
      try { return sessionStorage.getItem(draftKey) || ""; } catch { /* ignore */ }
    }
    return "";
  });
  const setText = (v) => {
    const next = typeof v === "function" ? v(text) : v;
    _setText(next);
    if (draftKey) {
      try { if (next) sessionStorage.setItem(draftKey, next); else sessionStorage.removeItem(draftKey); } catch { /* ignore */ }
    }
  };
  const [showPicker, setShowPicker] = useState(false);
  const textareaRef = useRef(null);

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

  const handleSchedule = (scheduledAt) => {
    if (!text.trim()) return;
    onSendLater(text.trim(), scheduledAt);
    setText("");
    setShowPicker(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const canType = !disabled || isBusy;

  // No-op: keyboard dismiss handled by App-level focusout micro-scroll
  const handleBlur = useCallback(() => {}, []);

  return (
    <div className="pb-2 safe-area-pb-tight flex justify-center px-4">
      <div className="glass-bar-nav rounded-[28px] px-3 py-2.5 flex items-end gap-2 w-full relative" style={{ maxWidth: "24rem" }}>
        {voice.recording && voice.analyserNode ? (
          <div className="flex-1 min-h-[40px] flex items-center px-3">
            <WaveformVisualizer analyserNode={voice.analyserNode} remainingSeconds={voice.remainingSeconds} className="flex-1 h-8" />
          </div>
        ) : (
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleBlur}
            placeholder={tmuxMode ? "Send via tmux..." : isBusy ? "Send (queued until ready)..." : disabled ? disabledReason : "Type a message..."}
            disabled={!canType}
            rows={1}
            className="flex-1 min-h-[40px] max-h-[160px] rounded-xl bg-transparent px-3 py-2.5 text-sm text-heading placeholder-hint resize-none focus:outline-none transition-colors disabled:opacity-50"
          />
        )}
        <VoiceRecorder
          recording={voice.recording}
          voiceLoading={voice.voiceLoading}
          micError={voice.micError || voiceError}
          onToggle={voice.toggleRecording}
        />
        {/* Send later (clock) button — always visible between mic and send */}
        <div className="relative">
          <button
            type="button"
            onClick={() => text.trim() && setShowPicker(!showPicker)}
            disabled={!text.trim()}
            title="Schedule message for later"
            className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
              !text.trim()
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
  const [starred, setStarred] = useState(false);
  const [starLoading, setStarLoading] = useState(false);
  const [muted, setMuted] = useState(() => isAgentMuted(id));
  const [streamingContent, setStreamingContent] = useState(null);
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
      setAgent(agentData);
      setMessages(msgData);
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

  // Detect if user has scrolled up (to avoid forcing scroll during streaming)
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    userScrolledUp.current = distFromBottom > 100;
  }, []);

  useEffect(() => {
    if (!userScrolledUp.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages.length, streamingContent]);

  // WebSocket: re-fetch on new_message events, handle streaming
  const { lastEvent, sendWsMessage } = useWebSocket();

  // Notify backend which agent we're viewing (suppresses notifications)
  useEffect(() => {
    sendWsMessage({ type: "viewing", agent_id: id });
    return () => sendWsMessage({ type: "viewing", agent_id: null });
  }, [id, sendWsMessage]);
  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.type === "agent_stream" && lastEvent.data?.agent_id === id) {
      setStreamingContent(lastEvent.data.content);
      return;
    }
    if (lastEvent.type === "new_message" && lastEvent.data?.agent_id === id) {
      setStreamingContent(null);
      loadData();
      return;
    }
    if (lastEvent.type === "agent_update" && lastEvent.data?.agent_id === id) {
      // Clear streaming when agent is no longer executing/syncing
      if (lastEvent.data.status !== "EXECUTING" && lastEvent.data.status !== "SYNCING") {
        setStreamingContent(null);
      }
      loadData();
    }
  }, [lastEvent, id, loadData]);

  // Cleanup
  useEffect(() => {
    return () => { if (toastTimer.current) clearTimeout(toastTimer.current); };
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
  const isStopped = agent.status === "STOPPED";
  const isError = agent.status === "ERROR";

  let disabledReason = "";
  if (isStopped) disabledReason = "Agent is stopped — click Resume to restart";
  else if (isError) disabledReason = "Agent errored — click Resume to restart";

  return (
    <div className="flex flex-col h-full">
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
              {isStopped || isError ? (
                <button
                  type="button"
                  onClick={handleResume}
                  disabled={resuming}
                  className="px-2.5 h-7 flex items-center gap-1 rounded-lg text-xs font-medium bg-cyan-600 hover:bg-cyan-500 text-white transition-colors disabled:opacity-50"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 3l14 9-14 9V3z" />
                  </svg>
                  {resuming ? "..." : "Resume"}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setShowStopConfirm(true)}
                  className="px-2.5 h-7 flex items-center gap-1 rounded-lg text-xs font-medium text-red-400 hover:bg-red-600/20 transition-colors"
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
          </div>
        </div>
      </div>

      {/* Parent agent link */}
      {agent.parent_id && (
        <div className="shrink-0 bg-surface border-b border-divider px-4 py-1.5">
          <div className="max-w-2xl mx-auto">
            <button
              type="button"
              onClick={() => navigate(`/agents/${agent.parent_id}`)}
              className="text-xs text-cyan-400 hover:underline flex items-center gap-1"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
              </svg>
              Continued from previous session
            </button>
          </div>
        </div>
      )}

      {/* Session size indicator */}
      {agent.session_size_bytes != null && agent.session_size_bytes > 0 && (
        <div className="shrink-0 bg-surface border-b border-divider px-4 py-1">
          <div className="max-w-2xl mx-auto flex items-center gap-1.5">
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${
              agent.session_size_bytes < 512000 ? "bg-green-500" :
              agent.session_size_bytes < 2097152 ? "bg-amber-500" : "bg-red-500"
            }`} />
            <span className="text-[10px] text-dim" title="Large sessions use more tokens per message. Consider using /compact in the CLI.">
              Session: {agent.session_size_bytes < 1024 ? `${agent.session_size_bytes} B` :
                agent.session_size_bytes < 1048576 ? `${(agent.session_size_bytes / 1024).toFixed(1)} KB` :
                `${(agent.session_size_bytes / 1048576).toFixed(1)} MB`}
            </span>
          </div>
        </div>
      )}

      {/* Messages */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto overflow-x-hidden px-4 py-3 max-w-2xl mx-auto w-full flex flex-col"
      >
        <div className="mt-auto" />
        {messages.length === 0 && agent.status === "STARTING" ? (
          <InitializingIndicator />
        ) : (
          <>
            {messages.map((msg) => (
              <ChatBubble key={msg.id} message={msg} project={agent.project} onCancelMessage={handleCancelMessage} onUpdateMessage={handleUpdateMessage} onSendNow={handleSendNow} />
            ))}

            {/* Streaming output or typing indicator while executing/syncing */}
            {(isExecuting || isSyncing) && (
              streamingContent !== null
                ? (streamingContent ? <StreamingBubble content={streamingContent} project={agent.project} /> : <TypingIndicator />)
                : isExecuting ? <TypingIndicator />
                : isSyncing && messages.length > 0 && messages[messages.length - 1].role === "USER" && !(messages[messages.length - 1].status === "PENDING" && messages[messages.length - 1].scheduled_at) ? <TypingIndicator />
                : null
            )}
          </>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input bar */}
      <ChatInput
        agentId={id}
        onSend={handleSend}
        onSendLater={handleSendLater}
        disabled={isStopped || isError}
        disabledReason={disabledReason}
        isBusy={isExecuting || (isSyncing && !hasTmux)}
        tmuxMode={hasTmux}
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
    </div>
  );
}
