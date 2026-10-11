"""
Intent parser for Bahi-Khata Bolo.

Turns a user's transcript (spoken question) into a structured Intent:
  {"intent": "balance_of|outstanding_total|top_debtors|entries_of|unknown",
   "customer_name": str | null,
   "n": int | null}

Uses Sarvam-105B Chat with JSON-only output.
Falls back to deterministic keyword/name matching if the LLM output is invalid.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from src.config import Settings
from src.normalize import customer_key, fuzzy_match_customer
from src.sarvam_client import chat, get_client

logger = logging.getLogger(__name__)


@dataclass
class Intent:
    intent: str  # balance_of | outstanding_total | top_debtors | entries_of | unknown
    customer_name: Optional[str] = None
    customer_key: Optional[str] = None
    n: Optional[int] = None
    raw_json: Optional[dict] = None
    ambiguous_matches: Optional[list[str]] = None  # when two names are close


# ---------------------------------------------------------------------------
# System prompt for the LLM
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a JSON-only intent classifier for a kirana shop credit ledger app.

The user speaks a question about their bahi-khata (credit ledger).

Output ONLY valid JSON (no markdown, no explanation, no extra text):
{
  "intent": "balance_of" | "outstanding_total" | "top_debtors" | "entries_of" | "unknown",
  "customer_name": "<name as spoken>" or null,
  "n": <integer> or null
}

Intent definitions:
- balance_of: how much does a specific customer owe? Requires customer_name.
- outstanding_total: what is the total outstanding across all customers?
- top_debtors: who owes the most? n = how many to show (default 5).
- entries_of: show all transactions of a specific customer. Requires customer_name.
- unknown: cannot understand the question.

Rules:
- Extract customer_name exactly as spoken (preserve script/language).
- If the question is about all customers, set customer_name to null.
- If "top" or "sabse zyada" is mentioned, use top_debtors.
- If "total", "kul", "sab ka", "overall" is mentioned, use outstanding_total.
- Respond ONLY with the JSON object, nothing else.
"""


def parse_intent(
    transcript: str,
    known_customers: list[str],
    settings: Settings,
) -> Intent:
    """Parse a spoken transcript into a structured Intent.

    1. Ask the LLM to classify.
    2. Resolve customer_name against known_customers via fuzzy matching.
    3. Fall back to keyword match if LLM output is invalid.
    """
    intent = _llm_parse(transcript, settings)

    # Resolve customer name
    if intent.customer_name and known_customers:
        intent = _resolve_customer(intent, known_customers)

    return intent


# ---------------------------------------------------------------------------
# LLM-based parsing
# ---------------------------------------------------------------------------

def _llm_parse(transcript: str, settings: Settings) -> Intent:
    """Ask the LLM to classify the transcript."""
    try:
        client = get_client(settings)
        raw_reply = chat(
            client,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            model=settings.chat_model,
            reasoning_effort=settings.chat_reasoning_effort,
        )

        # Strip markdown fences if present
        text = raw_reply.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        parsed = json.loads(text)
        return Intent(
            intent=parsed.get("intent", "unknown"),
            customer_name=parsed.get("customer_name"),
            n=parsed.get("n"),
            raw_json=parsed,
        )
    except Exception as exc:
        logger.warning("LLM intent parsing failed: %s — falling back to keyword match.", exc)
        return _keyword_fallback(transcript)


# ---------------------------------------------------------------------------
# Keyword fallback
# ---------------------------------------------------------------------------

_BALANCE_KEYWORDS = {"kitna", "baaki", "baki", "balance", "udhar", "hisab", "कितना", "बाकी", "उधार", "हिसाब"}
_TOTAL_KEYWORDS = {"total", "kul", "sab", "overall", "sabka", "कुल", "सब", "टोटल"}
_TOP_KEYWORDS = {"top", "sabse", "zyada", "maximum", "सबसे", "ज़्यादा", "ज्यादा"}
_ENTRIES_KEYWORDS = {"transactions", "entries", "history", "details", "सौदे", "लेनदेन"}


def _keyword_fallback(transcript: str) -> Intent:
    """Simple keyword-based intent detection as a fallback."""
    words = set(transcript.lower().split())

    if words & _TOP_KEYWORDS:
        return Intent(intent="top_debtors", n=5)
    if words & _TOTAL_KEYWORDS:
        return Intent(intent="outstanding_total")
    if words & _ENTRIES_KEYWORDS:
        return Intent(intent="entries_of")
    if words & _BALANCE_KEYWORDS:
        return Intent(intent="balance_of")

    return Intent(intent="unknown")


# ---------------------------------------------------------------------------
# Customer resolution
# ---------------------------------------------------------------------------

def _resolve_customer(intent: Intent, known_customers: list[str]) -> Intent:
    """Resolve intent.customer_name against the list of known customer keys."""
    if not intent.customer_name:
        return intent

    matches = fuzzy_match_customer(intent.customer_name, known_customers, threshold=70)

    if not matches:
        # No match found — keep the name as-is; the UI should tell the user.
        intent.customer_key = customer_key(intent.customer_name)
        return intent

    best_name, best_score = matches[0]

    # If two names are very close, flag ambiguity instead of guessing
    if len(matches) >= 2:
        second_name, second_score = matches[1]
        if best_score - second_score < 10:
            intent.ambiguous_matches = [m for m, _ in matches[:3]]
            intent.customer_key = None
            return intent

    intent.customer_key = best_name
    return intent
