"""Voice recognition — Whisper API integration."""

import logging

from fastapi import APIRouter, HTTPException, UploadFile

from config import OPENAI_API_KEY

logger = logging.getLogger("orchestrator.voice")
router = APIRouter()

MAX_AUDIO_SIZE = 25 * 1024 * 1024  # 25MB (Whisper API limit)
MIN_AUDIO_SIZE = 100  # bytes — short recordings are still valid

# Lazy-init async client singleton (avoids import cost at startup if unused)
_async_client = None


def _get_client():
    global _async_client
    if _async_client is None:
        from openai import AsyncOpenAI
        _async_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _async_client


@router.post("/api/voice")
async def transcribe_audio(file: UploadFile):
    """Transcribe audio file using OpenAI Whisper API."""
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OpenAI API key not configured")

    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No audio file provided")

    content = await file.read()
    if len(content) < MIN_AUDIO_SIZE:
        raise HTTPException(status_code=400, detail="Audio file too short")
    if len(content) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=400, detail=f"Audio file too large (max {MAX_AUDIO_SIZE // 1024 // 1024}MB)")

    client = _get_client()

    try:
        # Whisper accepts various formats: mp3, mp4, mpeg, mpga, m4a, wav, webm
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=(file.filename, content),
        )
    except Exception as e:
        logger.exception("Whisper API error")
        raise HTTPException(status_code=502, detail=f"Whisper API error: {e}")

    text = transcript.text.strip()
    logger.info("Transcribed %d bytes audio → %d chars text", len(content), len(text))
    return {"text": text}
