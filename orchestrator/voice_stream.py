"""WebSocket proxy for OpenAI Realtime API streaming transcription.

Key finding: gpt-4o-mini-transcribe does NOT emit delta events — only
completed transcriptions. Server VAD commits on silence, but provides
no incremental text during speech.

Strategy: use turn_detection=None with periodic manual commits (~2s).
Each commit triggers transcription of the accumulated audio, giving
the user near-real-time text feedback during continuous speech.
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

# How often to commit audio buffer (seconds). Each commit triggers
# transcription of whatever audio has accumulated since the last commit.
COMMIT_INTERVAL = 2.0


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

    Server relays back:
      {"type": "transcript", "text": "..."}   — transcribed text chunk
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
    commit_task = None
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

        # Configure: no VAD — we control commits manually
        await openai_ws.send(json.dumps({
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": OPENAI_REALTIME_MODEL,
                },
                "turn_detection": None,
            },
        }))

        # -- Relay: OpenAI → browser --
        async def relay_from_openai():
            nonlocal has_pending_audio
            try:
                async for raw in openai_ws:
                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")

                    if msg_type == "conversation.item.input_audio_transcription.completed":
                        text = msg.get("transcript", "").strip()
                        if text:
                            logger.info("Transcription: %r", text[:120])
                            await _safe_send(ws, {"type": "transcript", "text": text})

                    elif msg_type == "input_audio_buffer.committed":
                        has_pending_audio = False

                    elif msg_type == "error":
                        err = msg.get("error", {})
                        err_msg = err.get("message", "Unknown OpenAI error")
                        # Suppress expected empty-buffer errors
                        if "buffer too small" not in err_msg and "commit_empty" not in err.get("code", ""):
                            logger.warning("OpenAI error: %s", err_msg)
                            await _safe_send(ws, {"type": "error", "message": err_msg})

                    # Ignore: transcription_session.updated, conversation.item.created, etc.

            except websockets.exceptions.ConnectionClosed:
                logger.debug("OpenAI WS closed")
            except Exception:
                logger.warning("Relay from OpenAI ended", exc_info=True)
            finally:
                stop_event.set()

        relay_task = asyncio.create_task(relay_from_openai())

        # -- Periodic commit timer --
        async def periodic_commit():
            """Commit audio buffer every COMMIT_INTERVAL seconds to trigger
            transcription of accumulated audio."""
            nonlocal has_pending_audio
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(COMMIT_INTERVAL)
                    if has_pending_audio and openai_ws and openai_ws.open:
                        try:
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.commit",
                            }))
                        except Exception:
                            break
            except asyncio.CancelledError:
                pass

        commit_task = asyncio.create_task(periodic_commit())

        # -- Read from browser, forward audio to OpenAI --
        audio_chunks_sent = 0
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
                # Final commit for remaining audio
                if has_pending_audio:
                    try:
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.commit",
                        }))
                    except Exception:
                        pass

                # Wait for final transcription
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
        if commit_task and not commit_task.done():
            commit_task.cancel()
            try:
                await commit_task
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
