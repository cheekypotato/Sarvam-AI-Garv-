"""
Voice module for Bahi-Khata Bolo.

Wrappers around STT and TTS with:
  - Audio duration validation (max 30 s for sync STT).
  - Language fallback for TTS.
  - Text truncation / splitting for TTS (max 2500 chars per request).
"""

from __future__ import annotations

import io
import logging
import wave
from typing import Any

from src.config import Settings
from src.sarvam_client import get_client, stt as _stt_raw, tts as _tts_raw

logger = logging.getLogger(__name__)


def speech_to_text(
    audio_bytes: bytes,
    settings: Settings,
) -> dict[str, str]:
    """Transcribe audio bytes and return ``{"transcript": ..., "language_code": ...}``.

    Validates duration is within the sync REST limit (max_audio_seconds).
    """
    # Validate duration if it's a WAV
    try:
        duration = _wav_duration(audio_bytes)
        if duration and duration > settings.max_audio_seconds:
            return {
                "transcript": "",
                "language_code": "",
                "error": (
                    f"Audio is {duration:.0f}s long, but the limit is "
                    f"{settings.max_audio_seconds}s. Please record a shorter clip."
                ),
            }
    except Exception:
        pass  # Not a WAV or corrupted – let the API handle it

    client = get_client(settings)
    result = _stt_raw(
        client,
        io.BytesIO(audio_bytes),
        model=settings.stt_model,
        mode=settings.stt_mode,
        language_code=settings.stt_language,
    )
    return result


def text_to_speech(
    text: str,
    language_code: str,
    settings: Settings,
) -> bytes:
    """Convert *text* to speech WAV bytes.

    Handles:
      - Language fallback: if *language_code* is not supported, use the
        ``TTS_FALLBACK_LANGUAGE`` from settings.
      - Text truncation: split at sentence boundaries if > ``tts_max_chars``.
    """
    # Language fallback
    supported_tts = {
        "hi-IN", "bn-IN", "ta-IN", "te-IN", "kn-IN",
        "ml-IN", "mr-IN", "gu-IN", "pa-IN", "od-IN", "en-IN",
    }
    if language_code not in supported_tts:
        logger.info(
            "TTS language %s not supported, falling back to %s",
            language_code,
            settings.tts_fallback_language,
        )
        language_code = settings.tts_fallback_language

    client = get_client(settings)

    # Split if too long
    chunks = _split_text(text, settings.tts_max_chars)
    audio_parts: list[bytes] = []

    for chunk in chunks:
        wav_bytes = _tts_raw(
            client,
            chunk,
            language_code=language_code,
            model=settings.tts_model,
            speaker=settings.tts_speaker,
        )
        audio_parts.append(wav_bytes)

    # If multiple parts, concatenate (simple byte concat works for WAV if
    # the headers are compatible — for a demo this is fine).
    if len(audio_parts) == 1:
        return audio_parts[0]
    return b"".join(audio_parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wav_duration(audio_bytes: bytes) -> float | None:
    """Return duration in seconds if the bytes are a valid WAV, else None."""
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / rate if rate else None
    except Exception:
        return None


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split *text* into chunks of at most *max_chars* at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""

    # Split on sentence-ending punctuation
    sentences = text.replace("।", ".").split(".")
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = (current + ". " + sentence).strip(". ")
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current + ".")
            current = sentence

    if current:
        chunks.append(current + ".")

    return chunks if chunks else [text[:max_chars]]
