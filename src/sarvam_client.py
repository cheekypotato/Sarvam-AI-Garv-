"""
Sarvam API client wrapper for Bahi-Khata Bolo.

This is the ONLY module that imports `sarvamai`.
All other modules call functions here (easy to mock / switch to DEMO_MODE).

Provides:
  - Retry with exponential backoff on 429 / 5xx.
  - Request timeouts.
  - Clean, human-readable exceptions for every documented error code.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

from src.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Human-readable error map (from Sarvam Errors & Troubleshooting docs)
# ---------------------------------------------------------------------------
_ERROR_MAP: dict[int, str] = {
    400: "Bad request – check parameters, schema, or file format.",
    402: "Billing error – check your Sarvam subscription credits.",
    403: "Authentication failed – verify your SARVAM_API_KEY (Sarvam uses 403, not 401).",
    404: "Resource not found – check the endpoint or job ID.",
    409: "Conflict – results requested before the job reached a terminal state.",
    413: "File too large – reduce file size (max 200 MB for Document AI).",
    422: "Unprocessable – invalid file format or corrupted file.",
    429: "Rate limit exceeded – back off and retry.",
    500: "Internal server error – retry with backoff.",
    503: "Service unavailable – retry with backoff.",
}

# Status codes that are safe to retry
_RETRYABLE = {429, 500, 503}


class SarvamError(Exception):
    """Raised when a Sarvam API call fails after retries."""

    def __init__(self, status_code: int, message: str, raw: Any = None):
        self.status_code = status_code
        self.raw = raw
        friendly = _ERROR_MAP.get(status_code, f"Unexpected error (HTTP {status_code}).")
        super().__init__(f"[Sarvam {status_code}] {friendly}\nDetails: {message}")


def _call_with_backoff(fn, *, max_retries: int = 5):
    """Call *fn*; retry on 429 / 5xx with exponential backoff."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            return fn()
        except ApiError as exc:
            if exc.status_code not in _RETRYABLE or attempt == max_retries - 1:
                raise SarvamError(
                    status_code=exc.status_code,
                    message=str(exc.body),
                    raw=exc.body,
                ) from exc
            logger.warning(
                "Sarvam API %s (attempt %d/%d) – retrying in %.1fs",
                exc.status_code,
                attempt + 1,
                max_retries,
                delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 60)
    # Should never reach here, but just in case
    raise RuntimeError("Exhausted retries without a result or exception")


# ---------------------------------------------------------------------------
# Thin wrappers – one per API used in the app
# ---------------------------------------------------------------------------

def get_client(settings: Settings) -> SarvamAI:
    """Construct a SarvamAI client from the app settings."""
    return SarvamAI(api_subscription_key=settings.sarvam_api_key)


# ---- Chat -----------------------------------------------------------------

def chat(
    client: SarvamAI,
    messages: list[dict[str, str]],
    *,
    model: str = "sarvam-105b",
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Send a chat completion request and return the assistant's text reply.

    ``reasoning_effort=None`` disables thinking mode for speed.
    """
    kwargs: dict[str, Any] = dict(model=model, messages=messages)
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    response = _call_with_backoff(lambda: client.chat.completions(**kwargs))
    return response.choices[0].message.content


# ---- Text-to-Speech -------------------------------------------------------

def tts(
    client: SarvamAI,
    text: str,
    *,
    language_code: str = "hi-IN",
    model: str = "bulbul:v3",
    speaker: str = "shubh",
) -> bytes:
    """Convert *text* to speech and return raw WAV bytes.

    The Sarvam TTS REST API returns a list of base64 strings in
    ``response.audios``; we decode and concatenate them here.
    """
    response = _call_with_backoff(
        lambda: client.text_to_speech.convert(
            text=text,
            language_code=language_code,
            model=model,
            speaker=speaker,
        )
    )
    return base64.b64decode("".join(response.audios))


# ---- Speech-to-Text -------------------------------------------------------

def stt(
    client: SarvamAI,
    audio_file,
    *,
    model: str = "saaras:v3",
    mode: str = "transcribe",
    language_code: str = "unknown",
) -> dict[str, str]:
    """Transcribe *audio_file* and return ``{"transcript": ..., "language_code": ...}``.

    *audio_file* should be an open file-like object in binary mode.
    """
    kwargs: dict[str, Any] = dict(file=audio_file, model=model, mode=mode)
    if language_code and language_code != "unknown":
        kwargs["language_code"] = language_code

    response = _call_with_backoff(lambda: client.speech_to_text.transcribe(**kwargs))
    return {
        "transcript": response.transcript,
        "language_code": getattr(response, "language_code", language_code),
    }


# ---- Document AI (Extract) ------------------------------------------------

def doc_extract_create(
    client: SarvamAI,
    file_path: str,
    schema: dict,
    *,
    language: str = "hi-IN",
    output_format: str = "json",
) -> str:
    """Create an Extract job and return the ``job_id``.

    ``schema`` is passed as a JSON *string* (not a dict) per the docs.
    ``language`` (not ``language_code``) is the correct parameter name.
    """
    import mimetypes

    mime, _ = mimetypes.guess_type(file_path)
    mime = mime or "application/octet-stream"
    name = file_path.rsplit("/", 1)[-1]

    with open(file_path, "rb") as f:
        job = _call_with_backoff(
            lambda: client.doc_ai.extract(
                file=[(name, f, mime)],
                schema=json.dumps(schema),
                language=language,
                output_format=output_format,
            )
        )
    logger.info("Extract job created: %s (status=%s)", job.job_id, job.status)
    return job.job_id


def doc_get_status(client: SarvamAI, job_id: str) -> dict[str, Any]:
    """Poll the status of a Document AI job.

    Returns a dict with at least ``status`` and ``usage`` keys.
    """
    st = _call_with_backoff(lambda: client.doc_ai.get_status(job_id=job_id))
    return {
        "status": st.status,
        "usage": {
            "pages_total": st.usage.pages_total,
            "pages_processed": st.usage.pages_processed,
            "pages_succeeded": getattr(st.usage, "pages_succeeded", None),
            "pages_failed": getattr(st.usage, "pages_failed", None),
        },
    }


def doc_get_results(client: SarvamAI, job_id: str) -> Any:
    """Fetch the extraction results for a completed job."""
    results = _call_with_backoff(lambda: client.doc_ai.get_results(job_id=job_id))
    return results.result


# ---- Document AI (Digitise) — fallback ------------------------------------

def doc_digitise_create(
    client: SarvamAI,
    file_path: str,
    *,
    language: str = "hi-IN",
    output_format: str = "md",
) -> str:
    """Create a Digitise job and return the ``job_id``."""
    import mimetypes

    mime, _ = mimetypes.guess_type(file_path)
    mime = mime or "application/octet-stream"
    name = file_path.rsplit("/", 1)[-1]

    with open(file_path, "rb") as f:
        job = _call_with_backoff(
            lambda: client.doc_ai.digitise(
                file=[(name, f, mime)],
                language=language,
                output_format=output_format,
            )
        )
    logger.info("Digitise job created: %s (status=%s)", job.job_id, job.status)
    return job.job_id


def doc_get_download_url(client: SarvamAI, job_id: str) -> dict[str, str]:
    """Get the download URL for a completed digitise job."""
    dl = _call_with_backoff(lambda: client.doc_ai.get_download_url(job_id=job_id))
    return {"method": dl.method, "url": dl.url}
