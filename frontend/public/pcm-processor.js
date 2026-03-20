/**
 * AudioWorklet processor that captures PCM16 audio at 24kHz.
 *
 * The browser's AudioContext usually runs at 44.1kHz or 48kHz. This processor
 * downsamples to 24kHz and converts Float32 samples to Int16 (PCM16), then
 * posts chunks to the main thread for WebSocket transmission.
 *
 * Register with: audioCtx.audioWorklet.addModule("/pcm-processor.js")
 * Create with:  new AudioWorkletNode(audioCtx, "pcm-processor")
 */

const TARGET_SAMPLE_RATE = 24000;
// Send audio every ~100ms worth of samples
const CHUNK_SIZE = Math.floor(TARGET_SAMPLE_RATE * 0.1); // 2400 samples

class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(0);
    this._inputSampleRate = sampleRate; // global in AudioWorklet scope
    this._ratio = this._inputSampleRate / TARGET_SAMPLE_RATE;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) return true;

    const channelData = input[0]; // mono channel 0

    // Downsample: pick samples at ratio intervals
    const outputLen = Math.floor(channelData.length / this._ratio);
    const resampled = new Float32Array(outputLen);
    for (let i = 0; i < outputLen; i++) {
      const srcIdx = Math.floor(i * this._ratio);
      resampled[i] = channelData[Math.min(srcIdx, channelData.length - 1)];
    }

    // Accumulate
    const newBuf = new Float32Array(this._buffer.length + resampled.length);
    newBuf.set(this._buffer);
    newBuf.set(resampled, this._buffer.length);
    this._buffer = newBuf;

    // Send chunks
    while (this._buffer.length >= CHUNK_SIZE) {
      const chunk = this._buffer.slice(0, CHUNK_SIZE);
      this._buffer = this._buffer.slice(CHUNK_SIZE);

      // Convert Float32 [-1, 1] to Int16 [-32768, 32767]
      const pcm16 = new Int16Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) {
        const s = Math.max(-1, Math.min(1, chunk[i]));
        pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }

      this.port.postMessage({ type: "pcm16", buffer: pcm16.buffer }, [pcm16.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm-processor", PCMProcessor);
