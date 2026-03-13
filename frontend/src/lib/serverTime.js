/**
 * Server-time synchronization.
 *
 * Browser clocks can drift from the server. All "X ago" / elapsed-time
 * displays compare a server-generated timestamp against `Date.now()`,
 * so even a small skew produces a visible offset on every bubble.
 *
 * Call `calibrate(serverISOString)` whenever you receive a server
 * timestamp (e.g. from a WebSocket event). Then use `serverNow()`
 * instead of `Date.now()` everywhere you compare against server timestamps.
 */

let _offset = 0; // serverTime - clientTime (ms)
let _calibrated = false;

/**
 * Update the offset from a server-supplied ISO timestamp.
 * Uses exponential smoothing so a single outlier can't jump the value.
 */
export function calibrate(serverISO) {
  if (!serverISO) return;
  let str = String(serverISO);
  if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(str)) str += "Z";
  const serverMs = new Date(str).getTime();
  if (Number.isNaN(serverMs)) return;
  const sample = serverMs - Date.now();
  if (!_calibrated) {
    _offset = sample;
    _calibrated = true;
  } else {
    // Smooth toward the new sample (α = 0.3)
    _offset = Math.round(_offset * 0.7 + sample * 0.3);
  }
}

/** Returns a `Date.now()`-like value adjusted to server time. */
export function serverNow() {
  return Date.now() + _offset;
}
