/**
 * AudioWorklet processor: captures audio, downsamples to 24kHz, outputs PCM16.
 *
 * Output format: Int16Array (PCM16) at 24kHz mono — ready for OpenAI Realtime API.
 * Chunk size: 100ms of audio (2400 samples at 24kHz = 4800 bytes per chunk).
 * Sent as binary WebSocket frames (no base64 overhead on browser→server leg).
 */

const TARGET_SAMPLE_RATE = 24000;
const CHUNK_DURATION = 0.1; // seconds — 100ms for low-latency streaming
const CHUNK_SIZE = TARGET_SAMPLE_RATE * CHUNK_DURATION; // 2400 samples

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

    // Downsample to 24kHz via nearest-neighbor
    const outputLen = Math.floor(channelData.length / this._ratio);
    const resampled = new Float32Array(outputLen);
    for (let i = 0; i < outputLen; i++) {
      resampled[i] = channelData[Math.floor(i * this._ratio)];
    }

    // Accumulate
    const newBuf = new Float32Array(this._buffer.length + resampled.length);
    newBuf.set(this._buffer);
    newBuf.set(resampled, this._buffer.length);
    this._buffer = newBuf;

    // Send 100ms chunks as PCM16 (Int16)
    while (this._buffer.length >= CHUNK_SIZE) {
      const chunk = this._buffer.slice(0, CHUNK_SIZE);
      this._buffer = this._buffer.slice(CHUNK_SIZE);

      // Convert Float32 [-1.0, 1.0] → Int16 [-32768, 32767]
      const pcm16 = new Int16Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) {
        const s = Math.max(-1, Math.min(1, chunk[i]));
        pcm16[i] = s < 0 ? s * 32768 : s * 32767;
      }

      this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm-processor", PCMProcessor);
