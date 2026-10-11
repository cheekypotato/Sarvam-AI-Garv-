#!/usr/bin/env python3
"""
Smoke test: prove the Sarvam API key works.

1. One tiny chat call (reasoning disabled for speed).
2. One TTS call that saves out.wav.

Acceptance: this script prints a chat reply and writes a playable out.wav.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on sys.path so `src` can be imported
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.config import load_settings
from src.sarvam_client import get_client, chat, tts


def main() -> None:
    print("=" * 60)
    print("Bahi-Khata Bolo — Smoke Test")
    print("=" * 60)

    # ---- Load settings & build client ----
    settings = load_settings()
    client = get_client(settings)
    print("✅  Settings loaded, client created (key not printed).\n")

    # ---- 1. Chat completion ----
    print("▶ Chat completion (reasoning_effort=None for speed)...")
    reply = chat(
        client,
        messages=[{"role": "user", "content": "Say 'namaste' and tell me you are working."}],
        model=settings.chat_model,
        reasoning_effort=None,
    )
    print(f"  🗣  {reply}\n")

    # ---- 2. Text-to-Speech ----
    print("▶ Text-to-Speech (bulbul:v3, speaker=shubh, hi-IN)...")
    wav_bytes = tts(
        client,
        text="नमस्ते, बही-खाता बोलो ऐप काम कर रहा है।",
        language_code="hi-IN",
        model=settings.tts_model,
        speaker=settings.tts_speaker,
    )
    out_path = _PROJECT_ROOT / "out.wav"
    out_path.write_bytes(wav_bytes)
    print(f"  🔊  Saved {len(wav_bytes):,} bytes → {out_path}\n")

    print("=" * 60)
    print("✅  Smoke test passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
