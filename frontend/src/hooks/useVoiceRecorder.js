import { useState, useRef, useCallback, useEffect } from "react";
import { transcribeVoice, refineVoiceText } from "../lib/api";

export const DEFAULT_MAX_RECORDING_MS = 300000; // 5 minutes

/**
 * Batch voice recorder — records fully via MediaRecorder, then uploads
 * the audio file for transcription via Whisper API, optionally refined by LLM.
 *
 * @param {object} opts
 * @param {function} opts.onTranscript - called with the transcribed (and optionally refined) text
 * @param {function} opts.onError - called with error message string
 * @param {number}   [opts.maxDurationMs] - recording time limit in ms (default 5 min)
 *
 * Returns:
 *  recording, voiceLoading, micError, analyserNode, remainingSeconds,
 *  startRecording, stopRecording, toggleRecording
 */
export default function useVoiceRecorder({ onTranscript, onError, maxDurationMs }) {
  const limit = maxDurationMs || DEFAULT_MAX_RECORDING_MS;
  const [recording, setRecording] = useState(false);
  const [voiceLoading, setVoiceLoading] = useState(false);
  const [refining, setRefining] = useState(false);
  const [micError, setMicError] = useState(null);
  const [analyserNode, setAnalyserNode] = useState(null);
  const [remainingSeconds, setRemainingSeconds] = useState(limit / 1000);

  const streamRef = useRef(null);
  const audioCtxRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);
  const countdownRef = useRef(null);
  const startTimeRef = useRef(null);
  const startingRef = useRef(false);

  // Keep stable refs for callbacks
  const onTranscriptRef = useRef(onTranscript);
  const onErrorRef = useRef(onError);
  const limitRef = useRef(limit);
  useEffect(() => { onTranscriptRef.current = onTranscript; }, [onTranscript]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);
  useEffect(() => { limitRef.current = limit; }, [limit]);

  // When limit changes while not recording, reset the displayed countdown.
  useEffect(() => {
    if (!recording) {
      setRemainingSeconds(limit / 1000);
    }
  }, [limit, recording]);

  // Deliver transcript — optionally refine via LLM first
  const deliverTranscript = useCallback((text) => {
    const shouldRefine = (() => {
      try { return localStorage.getItem("pref:voiceRefine") !== "false"; } catch { return true; }
    })();
    if (shouldRefine && text.length >= 2) {
      setRefining(true);
      refineVoiceText(text)
        .then((res) => onTranscriptRef.current?.(res.text))
        .catch(() => onTranscriptRef.current?.(text))
        .finally(() => setRefining(false));
    } else {
      onTranscriptRef.current?.(text);
    }
  }, []);

  // Helper: clean up audio resources (mic, audio context)
  const cleanup = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    if (countdownRef.current) { clearInterval(countdownRef.current); countdownRef.current = null; }
    if (recorderRef.current) {
      try { if (recorderRef.current.state !== "inactive") recorderRef.current.stop(); } catch {}
      recorderRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    setAnalyserNode(null);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => { cleanup(); };
  }, [cleanup]);

  // Upload blob and deliver transcript
  const processRecording = useCallback(async (blob, mimeType) => {
    if (blob.size < 100) return; // too short, skip
    setVoiceLoading(true);
    try {
      const result = await transcribeVoice(blob, mimeType);
      const text = (result.text || "").trim();
      if (text) {
        deliverTranscript(text);
      }
    } catch (err) {
      const msg = err?.message || "";
      if (msg.includes("503") || msg.toLowerCase().includes("api key")) {
        onErrorRef.current?.("Voice input unavailable — OpenAI API key not configured. Add OPENAI_API_KEY to .env");
      } else {
        onErrorRef.current?.("Transcription failed — try again.");
      }
    } finally {
      setVoiceLoading(false);
    }
  }, [deliverTranscript]);

  const startRecording = useCallback(async () => {
    if (startingRef.current || voiceLoading) return;
    startingRef.current = true;
    setMicError(null);
    chunksRef.current = [];

    // Check browser support
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      startingRef.current = false;
      if (window.location.protocol === "http:" && window.location.hostname !== "localhost") {
        setMicError("Microphone requires HTTPS. Open this page via https:// or localhost.");
      } else {
        setMicError("cert_needed");
      }
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      startingRef.current = false;
      if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
        setMicError("cert_needed");
      } else if (err.name === "NotFoundError" || err.name === "NotReadableError") {
        setMicError("No microphone detected — plug one in or check your system audio settings.");
      } else if (err.name === "AbortError") {
        setMicError("Microphone access was interrupted — try again.");
      } else {
        setMicError("cert_needed");
      }
      return;
    }

    try {
      streamRef.current = stream;

      // AudioContext for AnalyserNode (waveform visualization)
      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      if (audioCtx.state === "suspended") await audioCtx.resume();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      setAnalyserNode(analyser);

      // Pick a supported MIME type for MediaRecorder
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/mp4")
          ? "audio/mp4"
          : "";

      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        const chunks = chunksRef.current;
        chunksRef.current = [];
        if (chunks.length > 0) {
          const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
          processRecording(blob, recorder.mimeType || "audio/webm");
        }
      };

      recorder.start();

      const curLimit = limitRef.current;
      setRecording(true);
      startTimeRef.current = Date.now();
      setRemainingSeconds(curLimit / 1000);

      // Update countdown every second
      countdownRef.current = setInterval(() => {
        const elapsed = Date.now() - startTimeRef.current;
        const remaining = Math.max(0, Math.ceil((limitRef.current - elapsed) / 1000));
        setRemainingSeconds(remaining);
      }, 1000);

      // Auto-stop after limit
      timerRef.current = setTimeout(() => {
        stopRecordingInternal();
      }, curLimit);

    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      if (audioCtxRef.current) audioCtxRef.current.close().catch(() => {});
      setMicError("Could not start recording — try again.");
    } finally {
      startingRef.current = false;
    }
  }, [voiceLoading, cleanup, processRecording]);

  const stopRecordingInternal = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    if (countdownRef.current) { clearInterval(countdownRef.current); countdownRef.current = null; }

    // Stop MediaRecorder — this triggers onstop which calls processRecording
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      try { recorder.stop(); } catch {}
    }
    recorderRef.current = null;

    // Stop mic tracks and audio context
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    setAnalyserNode(null);

    setRecording(false);
    startTimeRef.current = null;
    setRemainingSeconds(limitRef.current / 1000);
  }, []);

  const stopRecording = useCallback(() => {
    stopRecordingInternal();
  }, [stopRecordingInternal]);

  const toggleRecording = useCallback(() => {
    if (recording) stopRecording();
    else startRecording();
  }, [recording, startRecording, stopRecording]);

  return {
    recording,
    voiceLoading,
    refining,
    micError,
    analyserNode,
    remainingSeconds,
    streamingText: "", // no longer used — kept for API compat
    startRecording,
    stopRecording,
    toggleRecording,
  };
}
