"""Streaming transcription via OpenAI Realtime API (transcription mode).

Architecture:
- Browser sends PCM16 audio @ 24kHz as binary WebSocket frames (100ms chunks)
- Server proxies to OpenAI Realtime API (wss://api.openai.com/v1/realtime)
- OpenAI server-side VAD detects speech boundaries automatically
- Transcription deltas stream back in real-time (~232ms average latency)
- Each completed turn is sent as {"type": "transcript", "text": "..."}

Replaces the old WhisperLive-style buffer + batch Whisper API approach,
which had 2.5-5.5s latency due to fixed 2s polling + HTTP round-trips.
"""

import asyncio
import base64
import json
import logging
import os

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from config import OPENAI_API_KEY

logger = logging.getLogger("orchestrator.voice_stream")

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"

# Transcription model — gpt-4o-mini-transcribe supports streaming deltas
# and costs ~$0.003/min (cheaper than batch whisper-1 at $0.006/min)
TRANSCRIBE_MODEL = os.environ.get("VOICE_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")

# VAD silence duration — how long silence triggers end-of-turn (ms)
VAD_SILENCE_MS = int(os.environ.get("VOICE_VAD_SILENCE_MS", "800"))


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
      Binary frames: PCM16 @ 24kHz mono (100ms chunks, 4800 bytes each)
      Text frame:    {"type": "stop"} or "stop"

    Server sends back:
      {"type": "delta", "text": "...", "item_id": "..."}    — streaming partial
      {"type": "transcript", "text": "...", "item_id": "..."} — completed turn
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
    logger.info("Transcribe stream connected (model=%s, vad_silence=%dms)", TRANSCRIBE_MODEL, VAD_SILENCE_MS)

    # Connect to OpenAI Realtime API
    import websockets

    openai_ws = None
    try:
        openai_ws = await websockets.connect(
            OPENAI_REALTIME_URL,
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            },
            max_size=None,
            open_timeout=10,
        )
    except Exception as e:
        logger.warning("Failed to connect to OpenAI Realtime API: %s", e)
        await _safe_send(ws, {"type": "error", "message": f"Failed to connect to transcription service"})
        await ws.close()
        return

    logger.info("Connected to OpenAI Realtime API")

    # Configure transcription session
    session_config = {
        "type": "transcription_session.update",
        "session": {
            "input_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": TRANSCRIBE_MODEL,
            },
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": VAD_SILENCE_MS,
            },
        },
    }

    try:
        await openai_ws.send(json.dumps(session_config))
    except Exception as e:
        logger.warning("Failed to configure session: %s", e)
        await _safe_send(ws, {"type": "error", "message": "Failed to configure transcription session"})
        await openai_ws.close()
        await ws.close()
        return

    chunks_received = 0
    turns_completed = 0

    async def browser_to_openai():
        """Forward PCM16 audio from browser → OpenAI Realtime API."""
        nonlocal chunks_received
        try:
            while True:
                msg = await ws.receive()

                if msg.get("type") == "websocket.receive":
                    if "bytes" in msg and msg["bytes"]:
                        # PCM16 binary from browser → base64 → OpenAI
                        raw = msg["bytes"]
                        audio_b64 = base64.b64encode(raw).decode("ascii")
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": audio_b64,
                        }))
                        chunks_received += 1

                    elif "text" in msg and msg["text"]:
                        text = msg["text"]
                        if text == "stop":
                            break
                        try:
                            data = json.loads(text)
                            if data.get("type") == "stop":
                                break
                        except (json.JSONDecodeError, AttributeError):
                            pass

                elif msg.get("type") == "websocket.disconnect":
                    break

        except WebSocketDisconnect:
            logger.info("Browser disconnected")
        except Exception:
            logger.warning("browser_to_openai error", exc_info=True)

    async def openai_to_browser():
        """Forward transcription events from OpenAI → browser."""
        nonlocal turns_completed
        try:
            async for message in openai_ws:
                event = json.loads(message)
                t = event.get("type", "")

                if t == "conversation.item.input_audio_transcription.delta":
                    delta = event.get("delta", "")
                    if delta:
                        await _safe_send(ws, {
                            "type": "delta",
                            "text": delta,
                            "item_id": event.get("item_id", ""),
                        })

                elif t == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "").strip()
                    if transcript:
                        turns_completed += 1
                        logger.info("Turn %d transcript: %r", turns_completed, transcript[:120])
                        await _safe_send(ws, {
                            "type": "transcript",
                            "text": transcript,
                            "item_id": event.get("item_id", ""),
                        })

                elif t == "conversation.item.input_audio_transcription.failed":
                    err = event.get("error", {})
                    logger.warning("Transcription failed: %s", err.get("message", "unknown"))

                elif t == "input_audio_buffer.speech_started":
                    await _safe_send(ws, {"type": "speech_started"})

                elif t == "input_audio_buffer.speech_stopped":
                    await _safe_send(ws, {"type": "speech_stopped"})

                elif t == "error":
                    err = event.get("error", {})
                    err_msg = err.get("message", "Unknown transcription error")
                    logger.warning("OpenAI Realtime error: %s (code=%s)", err_msg, err.get("code"))
                    await _safe_send(ws, {"type": "error", "message": err_msg})

                elif t in ("transcription_session.created", "transcription_session.updated"):
                    logger.info("Session %s: %s", t.split(".")[-1], event.get("session", {}).get("id", "?"))

        except Exception:
            logger.warning("openai_to_browser error", exc_info=True)

    try:
        browser_task = asyncio.create_task(browser_to_openai())
        openai_task = asyncio.create_task(openai_to_browser())

        # Wait for either direction to finish (browser stop or OpenAI disconnect)
        done, pending = await asyncio.wait(
            [browser_task, openai_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    finally:
        logger.info("Session ended: %d chunks received, %d turns transcribed", chunks_received, turns_completed)
        try:
            await openai_ws.close()
        except Exception:
            pass
