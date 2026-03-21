import { useState, useRef, useCallback, useEffect } from "react";
import { getAuthToken } from "../lib/api";

export const DEFAULT_MAX_RECORDING_MS = 300000; // 5 minutes

/**
 * Streaming voice recorder — audio is sent via WebSocket to the backend
 * which proxies to OpenAI Realtime API for near-real-time transcription.
 *
 * The backend commits audio every ~2s, triggering transcription of each
 * chunk. Text appears incrementally as the user speaks (every ~2 seconds).
 *
 * @param {object} opts
 * @param {function} opts.onTranscript - called with each transcribed chunk
 * @param {function} opts.onError - called with error message string
 * @param {number}   [opts.maxDurationMs] - recording time limit in ms (default 5 min)
 *
 * Returns:
 *  recording, voiceLoading, micError, analyserNode, remainingSeconds,
 *  streamingText, startRecording, stopRecording, toggleRecording
 */
export default function useVoiceRecorder({ onTranscript, onError, maxDurationMs }) {
  const limit = maxDurationMs || DEFAULT_MAX_RECORDING_MS;
  const [recording, setRecording] = useState(false);
  const [voiceLoading, setVoiceLoading] = useState(false);
  const [micError, setMicError] = useState(null);
  const [analyserNode, setAnalyserNode] = useState(null);
  const [remainingSeconds, setRemainingSeconds] = useState(limit / 1000);
  // streamingText kept for API compat but not actively used (no delta events)
  const [streamingText, setStreamingText] = useState("");

  const streamRef = useRef(null);
  const audioCtxRef = useRef(null);
  const workletNodeRef = useRef(null);
  const wsRef = useRef(null);
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

  // Helper: clean up audio recording resources (mic, worklet, audio context)
  const cleanup = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    if (countdownRef.current) { clearInterval(countdownRef.current); countdownRef.current = null; }
    if (workletNodeRef.current) {
      try { workletNodeRef.current.disconnect(); } catch {}
      workletNodeRef.current = null;
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

  // Helper: close WebSocket (sends stop signal first)
  const closeWs = useCallback(() => {
    if (wsRef.current) {
      try {
        if (wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: "stop" }));
        }
        wsRef.current.close();
      } catch {}
      wsRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => { cleanup(); closeWs(); };
  }, [cleanup, closeWs]);

  const startRecording = useCallback(async () => {
    if (startingRef.current || voiceLoading) return;
    startingRef.current = true;
    setMicError(null);
    setStreamingText("");

    // Check browser support
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      startingRef.current = false;
      if (window.location.protocol === "http:" && window.location.hostname !== "localhost") {
        setMicError("Microphone requires HTTPS. Open this page via https:// or localhost.");
      } else {
        setMicError("Your browser does not support microphone access.");
      }
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      startingRef.current = false;
      if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
        setMicError("Microphone blocked — click the lock icon in your browser's address bar to allow access.");
      } else if (err.name === "NotFoundError" || err.name === "NotReadableError") {
        setMicError("No microphone detected — plug one in or check your system audio settings.");
      } else if (err.name === "AbortError") {
        setMicError("Microphone access was interrupted — try again.");
      } else {
        setMicError("Could not access microphone — check browser permissions and ensure HTTPS.");
      }
      return;
    }

    try {
      streamRef.current = stream;

      // AudioContext at default sample rate — the worklet will downsample to 24kHz
      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      // Ensure context is running (some browsers start suspended)
      if (audioCtx.state === "suspended") await audioCtx.resume();

      const source = audioCtx.createMediaStreamSource(stream);

      // AnalyserNode for waveform visualization
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      setAnalyserNode(analyser);

      // Load PCM processor worklet
      await audioCtx.audioWorklet.addModule("/pcm-processor.js");
      const workletNode = new AudioWorkletNode(audioCtx, "pcm-processor");
      workletNodeRef.current = workletNode;
      source.connect(workletNode);

      // Connect worklet to destination (with zero gain) so the browser
      // actually calls process() — disconnected nodes may be skipped.
      const silentGain = audioCtx.createGain();
      silentGain.gain.value = 0;
      workletNode.connect(silentGain);
      silentGain.connect(audioCtx.destination);

      // Open WebSocket to backend transcription proxy
      const token = getAuthToken();
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${proto}//${window.location.host}/ws/transcribe${token ? `?token=${encodeURIComponent(token)}` : ""}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      await new Promise((resolve, reject) => {
        ws.onopen = resolve;
        ws.onerror = () => reject(new Error("WebSocket connection failed"));
        setTimeout(() => reject(new Error("WebSocket connection timeout")), 5000);
      });

      // Handle transcription events from server
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === "transcript") {
            // Each transcript is a ~2s chunk — append to caller's text
            onTranscriptRef.current?.(msg.text);
          } else if (msg.type === "error") {
            onErrorRef.current?.(msg.message || "Transcription error");
          }
        } catch {}
      };

      ws.onclose = () => {
        // If WS closes while we're still recording, clean up
        if (startTimeRef.current) {
          cleanup();
          setRecording(false);
          setRemainingSeconds(limitRef.current / 1000);
        }
      };

      // Forward PCM16 chunks from worklet → WebSocket
      workletNode.port.onmessage = (e) => {
        if (e.data.type === "pcm16" && ws.readyState === WebSocket.OPEN) {
          const b64 = arrayBufferToBase64(e.data.buffer);
          ws.send(JSON.stringify({ type: "audio", data: b64 }));
        }
      };

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
      closeWs();
      setMicError("Could not start streaming transcription — try again.");
    } finally {
      startingRef.current = false;
    }
  }, [voiceLoading, cleanup, closeWs]);

  const stopRecordingInternal = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    if (countdownRef.current) { clearInterval(countdownRef.current); countdownRef.current = null; }

    // Stop mic/audio immediately but keep WS open for final transcription
    cleanup();
    setRecording(false);
    startTimeRef.current = null;
    setRemainingSeconds(limitRef.current / 1000);

    // Send stop signal and wait for server to flush remaining transcription
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      setVoiceLoading(true);
      try { ws.send(JSON.stringify({ type: "stop" })); } catch {}

      // Keep WS open to receive final transcript, then close
      setTimeout(() => {
        try { ws.close(); } catch {}
        wsRef.current = null;
        setVoiceLoading(false);
      }, 3000);
    }
  }, [cleanup]);

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
    micError,
    analyserNode,
    remainingSeconds,
    streamingText,
    startRecording,
    stopRecording,
    toggleRecording,
  };
}

/** Convert ArrayBuffer to base64 string. */
function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}
