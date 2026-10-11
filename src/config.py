"""
Configuration module for Bahi-Khata Bolo.

Loads .env via python-dotenv and exposes typed settings.
Fails fast with a clear message if SARVAM_API_KEY is missing or still
the placeholder from .env.example.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (two levels up from src/config.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _require_env(name: str) -> str:
    """Return the value of an env var or exit with a clear message."""
    value = os.environ.get(name, "").strip()
    if not value:
        print(
            f"\n❌  Environment variable {name} is not set.\n"
            f"   Copy .env.example to .env and fill it in.\n",
            file=sys.stderr,
        )
        sys.exit(1)
    # Reject the placeholder value from .env.example
    if value.startswith("your_") or value == "":
        print(
            f"\n❌  {name} still contains the placeholder value.\n"
            f"   Open .env and paste your real key.\n",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


@dataclass(frozen=True)
class Settings:
    """Typed, immutable application settings loaded from environment."""

    # --- Auth ---------------------------------------------------------------
    sarvam_api_key: str = field(repr=False)  # never print the key

    # --- Document AI --------------------------------------------------------
    ledger_language: str = "hi-IN"
    doc_poll_interval_seconds: int = 3
    doc_timeout_seconds: int = 180

    # --- Speech-to-Text -----------------------------------------------------
    stt_model: str = "saaras:v3"
    stt_mode: str = "transcribe"
    stt_language: str = "unknown"
    max_audio_seconds: int = 30

    # --- Chat / reasoning ---------------------------------------------------
    chat_model: str = "sarvam-105b"
    chat_reasoning_effort: str | None = None  # empty string → None (disabled)

    # --- Text-to-Speech -----------------------------------------------------
    tts_model: str = "bulbul:v3"
    tts_speaker: str = "shubh"
    tts_fallback_language: str = "hi-IN"
    tts_max_chars: int = 2500

    # --- App ----------------------------------------------------------------
    db_path: str = "./data/ledger.db"
    demo_mode: bool = False
    fixtures_dir: str = "./fixtures"
    log_level: str = "INFO"


def load_settings() -> Settings:
    """Build a Settings instance from the current environment."""
    api_key = _require_env("SARVAM_API_KEY")

    reasoning = os.getenv("CHAT_REASONING_EFFORT", "").strip()

    return Settings(
        sarvam_api_key=api_key,
        # Doc AI
        ledger_language=os.getenv("LEDGER_LANGUAGE", "hi-IN"),
        doc_poll_interval_seconds=int(os.getenv("DOC_POLL_INTERVAL_SECONDS", "3")),
        doc_timeout_seconds=int(os.getenv("DOC_TIMEOUT_SECONDS", "180")),
        # STT
        stt_model=os.getenv("STT_MODEL", "saaras:v3"),
        stt_mode=os.getenv("STT_MODE", "transcribe"),
        stt_language=os.getenv("STT_LANGUAGE", "unknown"),
        max_audio_seconds=int(os.getenv("MAX_AUDIO_SECONDS", "30")),
        # Chat
        chat_model=os.getenv("CHAT_MODEL", "sarvam-105b"),
        chat_reasoning_effort=reasoning if reasoning else None,
        # TTS
        tts_model=os.getenv("TTS_MODEL", "bulbul:v3"),
        tts_speaker=os.getenv("TTS_SPEAKER", "shubh"),
        tts_fallback_language=os.getenv("TTS_FALLBACK_LANGUAGE", "hi-IN"),
        tts_max_chars=int(os.getenv("TTS_MAX_CHARS", "2500")),
        # App
        db_path=os.getenv("DB_PATH", "./data/ledger.db"),
        demo_mode=os.getenv("DEMO_MODE", "false").lower() in ("true", "1", "yes"),
        fixtures_dir=os.getenv("FIXTURES_DIR", "./fixtures"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
