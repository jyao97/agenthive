import { useState, useRef, useCallback, useEffect } from "react";
import { transcribeVoice } from "../lib/api";

const MAX_RECORDING_MS = 60000; // 60 seconds

/**
 * Voice recording hook with AnalyserNode for waveform visualisation.
 *
 * Returns:
 *  recording, voiceLoading, micError, analyserNode,
 *  startRecording, stopRecording, toggleRecording
 */
export default function useVoiceRecorder({ onTranscript, onError }) {
  const [recording, setRecording] = useState(false);
  const [voiceLoading, setVoiceLoading] = useState(false);
  const [micError, setMicError] = useState(null);
  const [analyserNode, setAnalyserNode] = useState(null);

  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const streamRef = useRef(null);
  const audioCtxRef = useRef(null);
  const timerRef = useRef(null);
  const startingRef = useRef(false); // guard against double-tap

  // Keep stable refs for callbacks to avoid stale closures
  const onTranscriptRef = useRef(onTranscript);
  const onErrorRef = useRef(onError);
  useEffect(() => { onTranscriptRef.current = onTranscript; }, [onTranscript]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (streamRef.current) streamRef.current.getTracks().forEach((t) => t.stop());
      if (audioCtxRef.current) audioCtxRef.current.close().catch(() => {});
    };
  }, []);

  const startRecording = useCallback(async () => {
    // Guard against re-entry (rapid double-tap)
    if (startingRef.current || voiceLoading) return;
    startingRef.current = true;
    setMicError(null);

    // Check browser support / secure context before attempting
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

      // Setup AnalyserNode for waveform
      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      setAnalyserNode(analyser);

      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        audioCtx.close().catch(() => {});
        setAnalyserNode(null);

        // Use the actual MIME type from MediaRecorder (Safari = mp4, Chrome = webm)
        const mimeType = mediaRecorder.mimeType || "audio/webm";
        const audioBlob = new Blob(audioChunksRef.current, { type: mimeType });
        if (audioBlob.size === 0) return;

        setVoiceLoading(true);
        try {
          const data = await transcribeVoice(audioBlob, mimeType);
          if (data.text) onTranscriptRef.current?.(data.text);
        } catch (err) {
          onErrorRef.current?.("Voice transcription failed: " + err.message);
        } finally {
          setVoiceLoading(false);
        }
      };

      mediaRecorder.start();
      setRecording(true);

      // Auto-stop after MAX_RECORDING_MS
      timerRef.current = setTimeout(() => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
          mediaRecorderRef.current.stop();
          setRecording(false);
        }
      }, MAX_RECORDING_MS);
    } catch (err) {
      // Cleanup stream if MediaRecorder or AudioContext setup failed
      stream.getTracks().forEach((t) => t.stop());
      if (audioCtxRef.current) audioCtxRef.current.close().catch(() => {});
      setMicError("Could not start recording — try again.");
    } finally {
      startingRef.current = false;
    }
  }, [voiceLoading]);

  const stopRecording = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    setRecording(false);
  }, []);

  const toggleRecording = useCallback(() => {
    if (recording) stopRecording();
    else startRecording();
  }, [recording, startRecording, stopRecording]);

  return {
    recording,
    voiceLoading,
    micError,
    analyserNode,
    startRecording,
    stopRecording,
    toggleRecording,
  };
}
