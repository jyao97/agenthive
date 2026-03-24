"""Voice recognition — Whisper API + LLM refinement."""

import logging

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from config import OPENAI_API_KEY, VOICE_REFINE_MODEL

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

    # Whisper accepts various formats: mp3, mp4, mpeg, mpga, m4a, wav, webm
    transcript = await client.audio.transcriptions.create(
        model="whisper-1",
        file=(file.filename, content),
    )

    text = transcript.text.strip()
    logger.info("Transcribed %d bytes audio → %d chars text", len(content), len(text))
    return {"text": text}


# ---------------------------------------------------------------------------
# LLM refinement — correct speech errors, grammar, punctuation
# ---------------------------------------------------------------------------

REFINE_SYSTEM_PROMPT = (
    "你是语音转文字后处理助手。修正口误、语法错误，补充标点符号，但不要改变原意。"
    "如果文本已经正确，原样返回。只返回修正后的文本，不要添加任何解释。"
)


class RefineRequest(BaseModel):
    text: str


@router.post("/api/voice/refine")
async def refine_text(req: RefineRequest):
    """Post-process transcribed text with LLM to fix speech errors."""
    raw = req.text.strip()
    if len(raw) < 2:
        return {"text": raw}

    if not OPENAI_API_KEY:
        logger.warning("Voice refine skipped — no OpenAI API key")
        return {"text": raw}

    client = _get_client()
    resp = await client.chat.completions.create(
        model=VOICE_REFINE_MODEL,
        temperature=0,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": REFINE_SYSTEM_PROMPT},
            {"role": "user", "content": raw},
        ],
    )
    refined = resp.choices[0].message.content.strip()
    logger.info("Voice refine: %r → %r", raw, refined)
    return {"text": refined}
