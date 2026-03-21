import VoiceRecorder from "./VoiceRecorder";
import SendLaterPicker from "./SendLaterPicker";

export default function PromptInputBar({
  value, onChange, onSubmit, placeholder,
  textareaRef,
  voice,
  attachments = [], onAddFiles, onRemoveAttachment,
  dragOver, dragHandlers,
  showSchedule, onScheduleToggle, onScheduleSelect,
  submitting, disabled,
  onPaste,
  fileInputRef,
  onKeyDown,
}) {
  const handleKeyDown = (e) => {
    if (onKeyDown) { onKeyDown(e); return; }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSubmit(e); }
  };

  return (
    <div
      className="glass-bar-nav rounded-[22px] px-3 pt-2 pb-2.5 flex flex-col gap-2 relative"
      {...(dragHandlers || {})}
    >
      {/* Drop zone overlay */}
      {dragOver && (
        <div className="absolute inset-0 z-30 rounded-[22px] bg-cyan-500/15 border-2 border-dashed border-cyan-500 flex items-center justify-center pointer-events-none">
          <span className="text-sm font-medium text-cyan-400">Drop files here</span>
        </div>
      )}
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={onPaste}
        placeholder={placeholder || "What should this agent do?"}
        rows={3}
        className="w-full min-h-[72px] max-h-[180px] rounded-xl bg-transparent px-3 py-2 text-sm text-heading placeholder-hint resize-none focus:outline-none transition-colors"
      />
      {voice?.streamingText && (
        <div className="px-3 pb-1 text-sm text-cyan-400/80 italic animate-pulse">
          {voice.streamingText}
        </div>
      )}
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
                <button type="button" onClick={() => onRemoveAttachment?.(att.id)} className="text-dim hover:text-heading shrink-0">
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {fileInputRef && (
        <input ref={fileInputRef} type="file" accept="image/*,video/*,.pdf,.txt,.csv,.json,.md,.py,.js,.ts,.jsx,.tsx,.html,.css,.yaml,.yml,.xml,.log,.zip,.tar,.gz" multiple className="hidden" onChange={(e) => { const files = Array.from(e.target.files || []); e.target.value = ""; if (files.length > 0) onAddFiles?.(files); }} />
      )}
      <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-1.5 items-center px-1">
        {/* Attach button */}
        {fileInputRef ? (
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
        ) : <div />}
        <div className="flex items-center gap-1.5">
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
            micError={voice.micError}
            onToggle={voice.toggleRecording}
          />
        </div>
        {onScheduleToggle ? (
          <div className="relative">
            <button
              type="button"
              onClick={onScheduleToggle}
              disabled={disabled}
              className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                disabled
                  ? "bg-elevated text-dim cursor-not-allowed"
                  : "bg-amber-500 hover:bg-amber-400 text-white"
              }`}
              title="Send later"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6l4 2m6-2a10 10 0 11-20 0 10 10 0 0120 0z" />
              </svg>
            </button>
            {showSchedule && (
              <SendLaterPicker
                onSelect={onScheduleSelect}
                onClose={onScheduleToggle}
              />
            )}
          </div>
        ) : <div />}
        <button
          type="button"
          onClick={onSubmit}
          disabled={disabled}
          className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
            disabled
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
