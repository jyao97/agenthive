"""WebSocket proxy for OpenAI Realtime API streaming transcription.

The OpenAI Realtime transcription API only emits transcription events
AFTER audio is committed (either by server VAD on silence, or manually).
Delta events are the model's token-by-token output, not live-as-you-speak.

To give the user near-real-time feedback during continuous speech, we run
a periodic commit timer (~3s) alongside server VAD. This ensures long
stretches of uninterrupted speech still produce incremental text updates.
"""

import asyncio
import json
import logging
import os

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState
import websockets

from config import OPENAI_API_KEY

logger = logging.getLogger("orchestrator.voice_stream")

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
OPENAI_REALTIME_MODEL = "gpt-4o-mini-transcribe"

# How often to force-commit audio buffer during continuous speech (seconds).
# Shorter = more responsive but more API calls and potential word splits.
PERIODIC_COMMIT_INTERVAL = 3.0


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
      {"type": "audio", "data": "<base64 PCM16 24kHz mono>"}
      {"type": "stop"}

    Server forwards to OpenAI Realtime API and relays back:
      {"type": "delta", "text": "..."}       — incremental word(s)
      {"type": "committed", "text": "..."}   — full committed turn
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

    openai_ws = None
    relay_task = None
    periodic_task = None
    has_pending_audio = False
    stop_event = asyncio.Event()

    try:
        # Connect to OpenAI Realtime API
        openai_ws = await websockets.connect(
            OPENAI_REALTIME_URL,
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            },
        )

        # Wait for transcription_session.created
        init_msg = await asyncio.wait_for(openai_ws.recv(), timeout=10)
        init_data = json.loads(init_msg)
        logger.info("OpenAI init: %s", init_data.get("type"))

        # Configure transcription session — keep server VAD for natural pauses
        await openai_ws.send(json.dumps({
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": OPENAI_REALTIME_MODEL,
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.4,
                    "silence_duration_ms": 500,
                    "prefix_padding_ms": 300,
                },
            },
        }))

        # -- Relay: OpenAI → browser --
        audio_chunks_sent = 0

        async def relay_from_openai():
            nonlocal has_pending_audio
            try:
                async for raw in openai_ws:
                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")

                    if msg_type == "conversation.item.input_audio_transcription.delta":
                        delta = msg.get("delta", "")
                        if delta:
                            await _safe_send(ws, {"type": "delta", "text": delta})

                    elif msg_type == "conversation.item.input_audio_transcription.completed":
                        text = msg.get("transcript", "")
                        logger.info("Transcription: %r", text[:120] if text else "")
                        if text:
                            await _safe_send(ws, {"type": "committed", "text": text})
                        has_pending_audio = False

                    elif msg_type == "error":
                        err = msg.get("error", {})
                        err_msg = err.get("message", "Unknown OpenAI error")
                        # Suppress expected empty-buffer errors from periodic commits
                        if "buffer too small" in err_msg:
                            logger.debug("Empty buffer commit (expected)")
                        else:
                            logger.warning("OpenAI error: %s", err_msg)
                            await _safe_send(ws, {"type": "error", "message": err_msg})

                    elif msg_type == "input_audio_buffer.speech_started":
                        await _safe_send(ws, {"type": "speech_started"})

                    elif msg_type == "input_audio_buffer.speech_stopped":
                        await _safe_send(ws, {"type": "speech_stopped"})

                    elif msg_type == "input_audio_buffer.committed":
                        has_pending_audio = False

                    # Silently ignore session events, other events

            except websockets.exceptions.ConnectionClosed:
                logger.debug("OpenAI WS closed")
            except Exception:
                logger.warning("Relay from OpenAI ended", exc_info=True)
            finally:
                stop_event.set()

        relay_task = asyncio.create_task(relay_from_openai())

        # -- Periodic commit: force transcription during continuous speech --
        async def periodic_commit():
            """Every N seconds, commit the audio buffer so long continuous
            speech still produces incremental transcription updates.
            Server VAD handles natural pauses; this handles the case where
            the user speaks without pausing."""
            nonlocal has_pending_audio
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(PERIODIC_COMMIT_INTERVAL)
                    if has_pending_audio and openai_ws and openai_ws.open:
                        try:
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.commit",
                            }))
                            logger.debug("Periodic commit sent")
                        except Exception:
                            break
            except asyncio.CancelledError:
                pass

        periodic_task = asyncio.create_task(periodic_commit())

        # -- Read from browser, forward audio to OpenAI --
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "audio":
                audio_b64 = msg.get("data", "")
                if audio_b64:
                    has_pending_audio = True
                    audio_chunks_sent += 1
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": audio_b64,
                    }))

            elif msg_type == "stop":
                logger.info("Stop received (%d chunks sent)", audio_chunks_sent)
                # Final commit for any remaining audio
                if has_pending_audio:
                    try:
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.commit",
                        }))
                    except Exception:
                        pass

                # Wait for final transcription to arrive
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
                break

    except WebSocketDisconnect:
        logger.info("Transcribe stream client disconnected")
    except Exception:
        logger.warning("Transcribe stream error", exc_info=True)
        await _safe_send(ws, {"type": "error", "message": "Transcription stream failed"})
    finally:
        if periodic_task and not periodic_task.done():
            periodic_task.cancel()
            try:
                await periodic_task
            except (asyncio.CancelledError, Exception):
                pass
        if relay_task and not relay_task.done():
            relay_task.cancel()
            try:
                await relay_task
            except (asyncio.CancelledError, Exception):
                pass
        if openai_ws:
            try:
                await openai_ws.close()
            except Exception:
                pass
        logger.info("Transcribe stream session ended")
