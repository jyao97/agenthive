"""Streaming transcription via WhisperLive-style buffer + OpenAI Whisper API.

Architecture (inspired by github.com/collabora/WhisperLive):
- Client sends raw Float32 audio @ 16kHz as binary WebSocket frames
- Server accumulates audio in a rolling buffer
- Every TRANSCRIBE_INTERVAL seconds, sends accumulated audio to Whisper API
- Returns transcribed text to the client immediately
- No OpenAI Realtime API needed — simpler, more reliable
"""

import asyncio
import io
import json
import logging
import os
import struct
import wave

import numpy as np
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from config import OPENAI_API_KEY

logger = logging.getLogger("orchestrator.voice_stream")

# How often to transcribe accumulated audio (seconds)
TRANSCRIBE_INTERVAL = 2.0

# Minimum audio duration to bother transcribing (seconds)
MIN_AUDIO_DURATION = 0.3

SAMPLE_RATE = 16000  # 16kHz — what Whisper expects


def _get_whisper_client():
    """Lazy-init async OpenAI client (same pattern as voice.py)."""
    global _async_client
    try:
        return _async_client
    except NameError:
        pass
    from openai import AsyncOpenAI
    _async_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _async_client


def _float32_to_wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Convert Float32 audio array to WAV file bytes for Whisper API."""
    # Clip and convert to int16
    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


async def _safe_send(ws: WebSocket, data: dict) -> bool:
    """Send JSON to browser WS, returning False if connection is gone."""
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_text(json.dumps(data))
            return True
    except Exception:
        pass
    return False


async def transcribe_stream_endpoint(ws: WebSocket):
    """WebSocket handler for /ws/transcribe.

    Browser sends:
      Binary frames: raw Float32 PCM @ 16kHz mono (0.5s chunks)
      Text frame:    "stop"

    Server sends back:
      {"type": "transcript", "text": "..."}
      {"type": "error", "message": "..."}
    """
    # Auth check (same pattern as /ws/status)
    from database import SessionLocal
    from auth import get_password_hash, get_jwt_secret, verify_token

    if os.environ.get("DISABLE_AUTH", "").strip() not in ("1", "true", "yes"):
        db = SessionLocal()
        try:
            pw_hash = get_password_hash(db)
            if pw_hash is not None:
                token = ws.query_params.get("token", "")
                jwt_secret = get_jwt_secret(db)
                if not token or not verify_token(token, jwt_secret):
                    await ws.close(code=4001, reason="Unauthorized")
                    return
        finally:
            db.close()

    if not OPENAI_API_KEY:
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "message": "OpenAI API key not configured"}))
        await ws.close()
        return

    await ws.accept()
    logger.info("Transcribe stream client connected")

    # Audio buffer — accumulates Float32 samples from the client
    audio_buffer = np.array([], dtype=np.float32)
    buffer_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    chunks_received = 0

    async def transcribe_buffer():
        """Periodically transcribe accumulated audio and send results."""
        nonlocal audio_buffer, chunks_received
        client = _get_whisper_client()

        while not stop_event.is_set():
            await asyncio.sleep(TRANSCRIBE_INTERVAL)

            async with buffer_lock:
                if len(audio_buffer) < int(SAMPLE_RATE * MIN_AUDIO_DURATION):
                    continue  # Not enough audio yet
                # Take all accumulated audio and clear buffer
                audio_chunk = audio_buffer.copy()
                audio_buffer = np.array([], dtype=np.float32)

            duration = len(audio_chunk) / SAMPLE_RATE
            logger.info("Transcribing %.1fs audio (%d samples)", duration, len(audio_chunk))

            try:
                wav_bytes = _float32_to_wav_bytes(audio_chunk)
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=("audio.wav", wav_bytes),
                )
                text = transcript.text.strip()
                if text:
                    logger.info("Transcript: %r", text[:120])
                    await _safe_send(ws, {"type": "transcript", "text": text})
            except Exception:
                logger.warning("Whisper API error", exc_info=True)

    transcribe_task = asyncio.create_task(transcribe_buffer())

    try:
        while True:
            msg = await ws.receive()

            if msg.get("type") == "websocket.receive":
                if "bytes" in msg and msg["bytes"]:
                    # Binary frame — raw Float32 audio
                    raw = msg["bytes"]
                    samples = np.frombuffer(raw, dtype=np.float32)
                    async with buffer_lock:
                        audio_buffer = np.concatenate([audio_buffer, samples])
                    chunks_received += 1

                elif "text" in msg and msg["text"]:
                    text = msg["text"]
                    if text == "stop":
                        logger.info("Stop received (%d chunks)", chunks_received)
                        break
                    # Try JSON for backward compat
                    try:
                        data = json.loads(text)
                        if data.get("type") == "stop":
                            logger.info("Stop received (%d chunks)", chunks_received)
                            break
                    except (json.JSONDecodeError, AttributeError):
                        pass

            elif msg.get("type") == "websocket.disconnect":
                break

    except WebSocketDisconnect:
        logger.info("Transcribe stream client disconnected")
    except Exception:
        logger.warning("Transcribe stream error", exc_info=True)
        await _safe_send(ws, {"type": "error", "message": "Transcription stream failed"})
    finally:
        stop_event.set()
        transcribe_task.cancel()
        try:
            await transcribe_task
        except (asyncio.CancelledError, Exception):
            pass

        # Final transcription of remaining audio
        async with buffer_lock:
            remaining = audio_buffer.copy()
            audio_buffer = np.array([], dtype=np.float32)

        if len(remaining) >= int(SAMPLE_RATE * MIN_AUDIO_DURATION):
            try:
                duration = len(remaining) / SAMPLE_RATE
                logger.info("Final transcription: %.1fs audio", duration)
                client = _get_whisper_client()
                wav_bytes = _float32_to_wav_bytes(remaining)
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=("audio.wav", wav_bytes),
                )
                text = transcript.text.strip()
                if text:
                    logger.info("Final transcript: %r", text[:120])
                    await _safe_send(ws, {"type": "transcript", "text": text})
            except Exception:
                logger.warning("Final transcription failed", exc_info=True)

        logger.info("Transcribe stream session ended")
