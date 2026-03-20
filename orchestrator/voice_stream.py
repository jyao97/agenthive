"""WebSocket proxy for OpenAI Realtime API streaming transcription."""

import asyncio
import base64
import json
import logging
import os

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
import websockets

from config import OPENAI_API_KEY

logger = logging.getLogger("orchestrator.voice_stream")

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
OPENAI_REALTIME_MODEL = "gpt-4o-mini-transcribe"


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

    try:
        # Connect to OpenAI Realtime API
        openai_ws = await websockets.connect(
            OPENAI_REALTIME_URL,
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            },
        )

        # Wait for session.created
        init_msg = await asyncio.wait_for(openai_ws.recv(), timeout=10)
        init_data = json.loads(init_msg)
        if init_data.get("type") != "session.created":
            logger.warning("Unexpected init message: %s", init_data.get("type"))

        # Configure transcription session
        await openai_ws.send(json.dumps({
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": OPENAI_REALTIME_MODEL,
                    "language": "en",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "silence_duration_ms": 500,
                    "prefix_padding_ms": 300,
                },
            },
        }))

        # Relay OpenAI → browser
        async def relay_from_openai():
            try:
                async for raw in openai_ws:
                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")

                    if msg_type == "conversation.item.input_audio_transcription.delta":
                        delta = msg.get("delta", "")
                        if delta:
                            await ws.send_text(json.dumps({"type": "delta", "text": delta}))

                    elif msg_type == "conversation.item.input_audio_transcription.completed":
                        text = msg.get("transcript", "")
                        if text:
                            await ws.send_text(json.dumps({"type": "committed", "text": text}))

                    elif msg_type == "error":
                        err = msg.get("error", {})
                        await ws.send_text(json.dumps({
                            "type": "error",
                            "message": err.get("message", "Unknown OpenAI error"),
                        }))

                    elif msg_type == "input_audio_buffer.speech_started":
                        await ws.send_text(json.dumps({"type": "speech_started"}))

                    elif msg_type == "input_audio_buffer.speech_stopped":
                        await ws.send_text(json.dumps({"type": "speech_stopped"}))

            except websockets.exceptions.ConnectionClosed:
                logger.debug("OpenAI WS closed")
            except Exception:
                logger.warning("Relay from OpenAI error", exc_info=True)

        relay_task = asyncio.create_task(relay_from_openai())

        # Read from browser, forward to OpenAI
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "audio":
                # Forward audio chunk
                audio_b64 = msg.get("data", "")
                if audio_b64:
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": audio_b64,
                    }))

            elif msg_type == "stop":
                # Signal end of audio — commit the buffer
                await openai_ws.send(json.dumps({
                    "type": "input_audio_buffer.commit",
                }))
                # Give OpenAI a moment to flush final transcription
                await asyncio.sleep(1.0)
                break

    except WebSocketDisconnect:
        logger.info("Transcribe stream client disconnected")
    except Exception:
        logger.warning("Transcribe stream error", exc_info=True)
        try:
            await ws.send_text(json.dumps({"type": "error", "message": "Transcription stream failed"}))
        except Exception:
            pass
    finally:
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
