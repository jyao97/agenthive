/**
 * Mic toggle button (no waveform — waveform is shown inline in the textarea area).
 * Props: recording, voiceLoading, micError, onToggle
 */
export default function VoiceRecorder({
  recording,
  voiceLoading,
  refining,
  micError,
  onToggle,
  className = "",
}) {
  const busy = voiceLoading || refining;
  return (
    <>
      <button
        type="button"
        onClick={onToggle}
        disabled={busy}
        title={refining ? "Refining..." : recording ? "Stop recording" : "Start voice input"}
        className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${className} ${
          recording
            ? "bg-red-500 hover:bg-red-600 text-white"
            : busy
              ? "bg-elevated cursor-wait"
              : "bg-elevated hover:bg-hover"
        }`}
      >
        {busy ? (
          <svg className="animate-spin h-5 w-5 text-body" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        ) : (
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
        )}
      </button>
      {micError && <p className="absolute -top-6 left-3 text-xs text-red-400">{micError}</p>}
    </>
  );
}
