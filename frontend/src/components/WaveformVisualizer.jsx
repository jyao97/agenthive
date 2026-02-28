import { useRef, useEffect } from "react";

/**
 * Canvas-based AnalyserNode waveform visualiser with cyan accent.
 */
export default function WaveformVisualizer({ analyserNode, remainingSeconds, onTap, className = "" }) {
  const canvasRef = useRef(null);
  const rafRef = useRef(null);

  useEffect(() => {
    if (!analyserNode) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const bufferLength = analyserNode.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    function draw() {
      rafRef.current = requestAnimationFrame(draw);
      analyserNode.getByteTimeDomainData(dataArray);

      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      ctx.lineWidth = 2;
      ctx.strokeStyle = "#00d2ff";
      ctx.beginPath();

      const sliceWidth = w / bufferLength;
      let x = 0;
      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0;
        const y = (v * h) / 2;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
        x += sliceWidth;
      }
      ctx.lineTo(w, h / 2);
      ctx.stroke();
    }
    draw();

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [analyserNode]);

  if (!analyserNode) return null;

  const formatTime = (s) => {
    if (s == null) return null;
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}:${String(sec).padStart(2, "0")}` : `${sec}s`;
  };

  return (
    <div className="flex flex-col items-center gap-0.5 cursor-pointer" onClick={onTap}>
      <canvas
        ref={canvasRef}
        width={200}
        height={40}
        className={`w-full rounded bg-transparent ${className}`}
      />
      {remainingSeconds != null && (
        <span className={`text-[11px] tabular-nums ${remainingSeconds <= 10 ? "text-red-400" : "text-dim"}`}>
          {formatTime(remainingSeconds)} remaining
        </span>
      )}
    </div>
  );
}
