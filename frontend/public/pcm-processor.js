/**
 * AudioWorklet processor: captures audio, downsamples to 16kHz, outputs Float32.
 *
 * Modelled after WhisperLive's audiopreprocessor.js:
 * - Target sample rate: 16kHz (what Whisper expects)
 * - Output format: Float32Array (raw samples, no PCM16 conversion)
 * - Chunk size: 0.5s of audio (8000 samples at 16kHz = 32KB per chunk)
 * - Sent as binary WebSocket frames (no base64 overhead)
 */

const TARGET_SAMPLE_RATE = 16000;
const CHUNK_DURATION = 0.5; // seconds
const CHUNK_SIZE = TARGET_SAMPLE_RATE * CHUNK_DURATION; // 8000 samples

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

    // Downsample to 16kHz via nearest-neighbor
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

    // Send 0.5s chunks as Float32 (same as WhisperLive)
    while (this._buffer.length >= CHUNK_SIZE) {
      const chunk = this._buffer.slice(0, CHUNK_SIZE);
      this._buffer = this._buffer.slice(CHUNK_SIZE);
      this.port.postMessage(chunk.buffer, [chunk.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm-processor", PCMProcessor);
