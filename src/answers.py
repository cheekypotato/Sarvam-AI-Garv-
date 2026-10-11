"""
Answer generation for Bahi-Khata Bolo.

Given an Intent + DB connection → compute the numbers + supporting rows +
human-readable text.

The LLM NEVER computes or alters a balance — all numbers come from db.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.config import Settings
from src.db import (
    CustomerBalance,
    balance_of,
    customer_display_names,
    entries_of,
    outstanding_total,
    top_debtors,
)
from src.intents import Intent

logger = logging.getLogger(__name__)


@dataclass
class Answer:
    """Structured answer ready for the UI and TTS."""

    text: str  # Human-readable answer text
    numbers: dict[str, Any] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)  # Supporting ledger rows
    language: str = "hi-IN"


def generate_answer(
    intent: Intent,
    conn,
    settings: Settings,
    *,
    detected_language: str = "hi-IN",
) -> Answer:
    """Produce an Answer from an Intent by querying the DB.

    Every number in the answer is computed by code, not the LLM.
    """
    display = customer_display_names(conn)

    if intent.intent == "balance_of":
        return _answer_balance_of(intent, conn, display, detected_language)

    if intent.intent == "outstanding_total":
        return _answer_outstanding_total(conn, detected_language)

    if intent.intent == "top_debtors":
        n = intent.n or 5
        return _answer_top_debtors(conn, n, display, detected_language)

    if intent.intent == "entries_of":
        return _answer_entries_of(intent, conn, display, detected_language)

    return Answer(
        text="माफ़ कीजिये, मैं आपका सवाल समझ नहीं पाया। कृपया दोबारा पूछें।"
        if detected_language.startswith("hi")
        else "Sorry, I could not understand your question. Please try again.",
        language=detected_language,
    )


# ---------------------------------------------------------------------------
# Per-intent handlers
# ---------------------------------------------------------------------------

def _answer_balance_of(
    intent: Intent, conn, display: dict, lang: str
) -> Answer:
    if intent.ambiguous_matches:
        names = ", ".join(intent.ambiguous_matches)
        text = (
            f"कई ग्राहक मिले: {names}। कृपया पूरा नाम बताएं।"
            if lang.startswith("hi")
            else f"Multiple customers found: {names}. Please specify the full name."
        )
        return Answer(text=text, language=lang)

    key = intent.customer_key
    if not key:
        text = (
            "ग्राहक का नाम नहीं मिला।"
            if lang.startswith("hi")
            else "Customer name not found."
        )
        return Answer(text=text, language=lang)

    bal = balance_of(conn, key)
    if not bal:
        raw_name = intent.customer_name or key
        text = (
            f"{raw_name} का कोई लेन-देन नहीं मिला।"
            if lang.startswith("hi")
            else f"No transactions found for {raw_name}."
        )
        return Answer(text=text, language=lang)

    name = display.get(key, bal.customer_raw)
    rows = entries_of(conn, key)

    if lang.startswith("hi"):
        text = (
            f"{name} का बाकी हिसाब ₹{bal.balance:,.0f} है। "
            f"कुल उधार ₹{bal.total_credit:,.0f}, "
            f"कुल भुगतान ₹{bal.total_paid:,.0f}।"
        )
    else:
        text = (
            f"{name} owes ₹{bal.balance:,.0f}. "
            f"Total credit: ₹{bal.total_credit:,.0f}, "
            f"Total paid: ₹{bal.total_paid:,.0f}."
        )

    return Answer(
        text=text,
        numbers={
            "balance": bal.balance,
            "total_credit": bal.total_credit,
            "total_paid": bal.total_paid,
        },
        rows=rows,
        language=lang,
    )


def _answer_outstanding_total(conn, lang: str) -> Answer:
    total = outstanding_total(conn)

    if lang.startswith("hi"):
        text = f"सबका कुल बाकी हिसाब ₹{total:,.0f} है।"
    else:
        text = f"Total outstanding balance is ₹{total:,.0f}."

    return Answer(
        text=text,
        numbers={"outstanding_total": total},
        language=lang,
    )


def _answer_top_debtors(conn, n: int, display: dict, lang: str) -> Answer:
    debtors = top_debtors(conn, n)

    if not debtors:
        text = (
            "कोई बकाया नहीं है।" if lang.startswith("hi")
            else "No outstanding balances found."
        )
        return Answer(text=text, language=lang)

    lines = []
    for i, d in enumerate(debtors, 1):
        name = display.get(d.customer_key, d.customer_raw)
        lines.append(f"{i}. {name}: ₹{d.balance:,.0f}")

    if lang.startswith("hi"):
        header = f"सबसे ज़्यादा बकाया वाले {len(debtors)} ग्राहक:"
    else:
        header = f"Top {len(debtors)} debtors:"

    text = header + "\n" + "\n".join(lines)

    return Answer(
        text=text,
        numbers={"top_debtors": [{"name": d.customer_raw, "balance": d.balance} for d in debtors]},
        rows=[],
        language=lang,
    )


def _answer_entries_of(intent: Intent, conn, display: dict, lang: str) -> Answer:
    key = intent.customer_key
    if not key:
        text = (
            "ग्राहक का नाम नहीं मिला।"
            if lang.startswith("hi")
            else "Customer name not found."
        )
        return Answer(text=text, language=lang)

    rows = entries_of(conn, key)
    if not rows:
        name = intent.customer_name or key
        text = (
            f"{name} के कोई लेन-देन नहीं मिले।"
            if lang.startswith("hi")
            else f"No transactions found for {name}."
        )
        return Answer(text=text, language=lang)

    name = display.get(key, intent.customer_name or key)
    total_entries = len(rows)

    if lang.startswith("hi"):
        text = f"{name} के {total_entries} लेन-देन मिले।"
    else:
        text = f"Found {total_entries} transactions for {name}."

    return Answer(
        text=text,
        numbers={"total_entries": total_entries},
        rows=rows,
        language=lang,
    )
